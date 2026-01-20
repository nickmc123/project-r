import requests
import os
from datetime import datetime, timedelta
import logging
from cryptography.fernet import Fernet
import json

logger = logging.getLogger(__name__)

class QuickBooksClient:
    """QuickBooks API Client for OAuth and data sync"""
    
    def __init__(self, db_connection=None):
        self.client_id = os.environ.get('QB_CLIENT_ID')
        self.client_secret = os.environ.get('QB_CLIENT_SECRET')
        self.redirect_uri = os.environ.get('QB_REDIRECT_URI')
        self.environment = os.environ.get('QB_ENVIRONMENT', 'sandbox')  # 'sandbox' or 'production'
        self.db = db_connection
        
        # Encryption key for storing tokens
        encryption_key = os.environ.get('QB_ENCRYPTION_KEY')
        if encryption_key:
            self.cipher = Fernet(encryption_key.encode())
        else:
            # Generate a key if not set (for development)
            self.cipher = Fernet(Fernet.generate_key())
            logger.warning("QB_ENCRYPTION_KEY not set - using generated key")
        
        # API endpoints
        if self.environment == 'sandbox':
            self.base_url = 'https://sandbox-quickbooks.api.intuit.com'
            self.auth_url = 'https://appcenter.intuit.com/connect/oauth2'
        else:
            self.base_url = 'https://quickbooks.api.intuit.com'
            self.auth_url = 'https://appcenter.intuit.com/connect/oauth2'
    
    def get_authorization_url(self, state):
        """Generate OAuth authorization URL"""
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'com.intuit.quickbooks.accounting',
            'state': state
        }
        
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{self.auth_url}?{query_string}"
    
    def exchange_code_for_tokens(self, auth_code):
        """Exchange authorization code for access and refresh tokens"""
        token_endpoint = f"{self.auth_url}/tokens"
        
        payload = {
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.redirect_uri
        }
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            token_endpoint,
            data=payload,
            headers=headers,
            auth=(self.client_id, self.client_secret)
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Token exchange failed: {response.text}")
            raise Exception(f"Failed to exchange code: {response.text}")
    
    def refresh_access_token(self, refresh_token):
        """Refresh an expired access token"""
        token_endpoint = f"{self.auth_url}/tokens"
        
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            token_endpoint,
            data=payload,
            headers=headers,
            auth=(self.client_id, self.client_secret)
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Token refresh failed: {response.text}")
            raise Exception(f"Failed to refresh token: {response.text}")
    
    def encrypt_token(self, token):
        """Encrypt token for storage"""
        return self.cipher.encrypt(token.encode()).decode()
    
    def decrypt_token(self, encrypted_token):
        """Decrypt stored token"""
        return self.cipher.decrypt(encrypted_token.encode()).decode()
    
    def make_api_request(self, endpoint, method='GET', access_token=None, realm_id=None, data=None):
        """Make authenticated API request to QuickBooks"""
        url = f"{self.base_url}/v3/company/{realm_id}/{endpoint}"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        if method == 'GET':
            response = requests.get(url, headers=headers)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            logger.error(f"API request failed: {response.text}")
            raise Exception(f"QuickBooks API error: {response.text}")
    
    def get_invoices(self, access_token, realm_id, max_results=100):
        """Fetch invoices from QuickBooks"""
        query = f"select * from Invoice maxresults {max_results}"
        endpoint = f"query?query={query}"
        return self.make_api_request(endpoint, access_token=access_token, realm_id=realm_id)
    
    def get_bills(self, access_token, realm_id, max_results=100):
        """Fetch bills from QuickBooks"""
        query = f"select * from Bill maxresults {max_results}"
        endpoint = f"query?query={query}"
        return self.make_api_request(endpoint, access_token=access_token, realm_id=realm_id)
    
    def get_payments(self, access_token, realm_id, max_results=100):
        """Fetch payments from QuickBooks"""
        query = f"select * from Payment maxresults {max_results}"
        endpoint = f"query?query={query}"
        return self.make_api_request(endpoint, access_token=access_token, realm_id=realm_id)
    
    def sync_to_project_r(self, user_id, access_token, realm_id):
        """Sync QuickBooks data to Project-R transactions"""
        try:
            # Fetch data from QuickBooks
            invoices = self.get_invoices(access_token, realm_id)
            bills = self.get_bills(access_token, realm_id)
            payments = self.get_payments(access_token, realm_id)
            
            sync_results = {
                'invoices_synced': 0,
                'bills_synced': 0,
                'payments_synced': 0,
                'errors': []
            }
            
            # Process invoices (revenue)
            if invoices and 'QueryResponse' in invoices:
                for invoice in invoices['QueryResponse'].get('Invoice', []):
                    try:
                        self._sync_invoice(user_id, invoice)
                        sync_results['invoices_synced'] += 1
                    except Exception as e:
                        sync_results['errors'].append(f"Invoice {invoice.get('Id')}: {str(e)}")
            
            # Process bills (expenses)
            if bills and 'QueryResponse' in bills:
                for bill in bills['QueryResponse'].get('Bill', []):
                    try:
                        self._sync_bill(user_id, bill)
                        sync_results['bills_synced'] += 1
                    except Exception as e:
                        sync_results['errors'].append(f"Bill {bill.get('Id')}: {str(e)}")
            
            # Process payments
            if payments and 'QueryResponse' in payments:
                for payment in payments['QueryResponse'].get('Payment', []):
                    try:
                        self._sync_payment(user_id, payment)
                        sync_results['payments_synced'] += 1
                    except Exception as e:
                        sync_results['errors'].append(f"Payment {payment.get('Id')}: {str(e)}")
            
            return sync_results
            
        except Exception as e:
            logger.error(f"Sync failed for user {user_id}: {str(e)}")
            raise
    
    def _sync_invoice(self, user_id, invoice):
        """Convert QuickBooks invoice to Project-R transaction"""
        # This will be implemented based on your database schema
        # Example structure:
        # - Extract invoice date, amount, customer
        # - Create a revenue transaction in Project-R
        # - Store QB invoice ID for reference
        pass
    
    def _sync_bill(self, user_id, bill):
        """Convert QuickBooks bill to Project-R transaction"""
        # This will be implemented based on your database schema
        pass
    
    def _sync_payment(self, user_id, payment):
        """Convert QuickBooks payment to Project-R transaction"""
        # This will be implemented based on your database schema
        pass
