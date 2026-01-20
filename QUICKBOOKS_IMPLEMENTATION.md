# QuickBooks Integration Implementation Guide
## Project-R Multi-Tenant Cash Flow Forecasting

---

## 🎯 Overview

Enable Project-R users to connect their QuickBooks Online accounts for automatic data sync:
- Import transactions, invoices, bills
- Real-time balance synchronization  
- Automated cash flow forecasting from actual accounting data

**Architecture**: Multi-tenant OAuth 2.0 with per-user credential storage

---

## 📋 Prerequisites

### QuickBooks Developer Account Setup
✅ You have: QuickBooks Developer Account with sandbox access

**Required Steps**:
1. Go to: https://developer.intuit.com/
2. Create new app in "My Apps"
3. Get credentials:
   - **Client ID**
   - **Client Secret**
4. Configure Redirect URIs:
   - Production: `https://web-production-8d237.up.railway.app/api/quickbooks/callback`
   - Development: `http://localhost:3000/api/quickbooks/callback`

### Sandbox Testing
- **Sandbox Company**: Test data environment (separate from production)
- **Test Users**: Create test companies for development
- **API Endpoint**: `https://sandbox-quickbooks.api.intuit.com`

---

## 🗄️ Database Schema Changes

### New Tables

```sql
-- Connection storage for each user
CREATE TABLE user_integrations (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  integration_type VARCHAR(50) NOT NULL, -- 'quickbooks', 'stripe', etc.
  
  -- OAuth credentials (encrypted)
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  realm_id VARCHAR(255) NOT NULL, -- QuickBooks Company ID
  
  -- Metadata
  connected_at TIMESTAMP DEFAULT NOW(),
  last_synced_at TIMESTAMP,
  token_expires_at TIMESTAMP NOT NULL,
  
  -- Connection status
  is_active BOOLEAN DEFAULT TRUE,
  company_name VARCHAR(255),
  
  UNIQUE(user_id, integration_type)
);

-- Sync history and error tracking
CREATE TABLE integration_sync_logs (
  id SERIAL PRIMARY KEY,
  integration_id INTEGER REFERENCES user_integrations(id) ON DELETE CASCADE,
  sync_started_at TIMESTAMP DEFAULT NOW(),
  sync_completed_at TIMESTAMP,
  status VARCHAR(20), -- 'success', 'failed', 'partial'
  
  records_synced JSONB, -- {"transactions": 45, "invoices": 12}
  error_message TEXT,
  
  INDEX(integration_id, sync_started_at)
);

-- Map QuickBooks transactions to Project-R transactions
CREATE TABLE quickbooks_transaction_map (
  id SERIAL PRIMARY KEY,
  integration_id INTEGER REFERENCES user_integrations(id) ON DELETE CASCADE,
  transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
  
  quickbooks_id VARCHAR(255) NOT NULL, -- QB transaction ID
  quickbooks_type VARCHAR(50), -- 'Invoice', 'Bill', 'Payment', etc.
  
  last_updated_at TIMESTAMP DEFAULT NOW(),
  
  UNIQUE(integration_id, quickbooks_id)
);

-- Indexes for performance
CREATE INDEX idx_user_integrations_user_id ON user_integrations(user_id);
CREATE INDEX idx_user_integrations_active ON user_integrations(user_id, is_active);
CREATE INDEX idx_sync_logs_integration ON integration_sync_logs(integration_id);
CREATE INDEX idx_qb_map_integration ON quickbooks_transaction_map(integration_id);
```

### Migration File

Create: `app/migrations/008_quickbooks_integration.sql`

---

## 🔐 Environment Variables

Add to Railway:

```bash
# QuickBooks OAuth
QUICKBOOKS_CLIENT_ID=your_client_id_here
QUICKBOOKS_CLIENT_SECRET=your_client_secret_here
QUICKBOOKS_REDIRECT_URI=https://web-production-8d237.up.railway.app/api/quickbooks/callback

# Use sandbox for development
QUICKBOOKS_ENVIRONMENT=sandbox  # or 'production'
QUICKBOOKS_API_BASE_URL=https://sandbox-quickbooks.api.intuit.com

# Encryption key for storing tokens
INTEGRATION_ENCRYPTION_KEY=generate_random_32_byte_key
```

---

## 🔧 Backend Implementation

### File Structure

```
app/
├── api/
│   └── quickbooks/
│       ├── auth.py          # OAuth flow
│       ├── sync.py          # Data sync logic
│       ├── webhooks.py      # QB webhook handler
│       └── client.py        # API client wrapper
├── services/
│   └── quickbooks_service.py  # Business logic
└── utils/
    └── encryption.py        # Token encryption
```

### 1. OAuth Authentication Flow

**File**: `app/api/quickbooks/auth.py`

```python
from flask import Blueprint, request, redirect, jsonify
from app.services.quickbooks_service import QuickBooksService
from app.utils.auth import require_auth
import os

quickbooks_auth_bp = Blueprint('quickbooks_auth', __name__)

@quickbooks_auth_bp.route('/connect', methods=['GET'])
@require_auth
def connect_quickbooks():
    """Step 1: Initiate OAuth flow"""
    user_id = request.current_user['id']
    
    # Generate OAuth URL
    auth_url = QuickBooksService.get_authorization_url(user_id)
    
    return jsonify({
        'authorization_url': auth_url
    })

@quickbooks_auth_bp.route('/callback', methods=['GET'])
def quickbooks_callback():
    """Step 2: Handle OAuth callback"""
    code = request.args.get('code')
    state = request.args.get('state')
    realm_id = request.args.get('realmId')
    
    if not code or not realm_id:
        return jsonify({'error': 'Missing authorization code'}), 400
    
    # Verify state and extract user_id
    user_id = QuickBooksService.verify_state(state)
    if not user_id:
        return jsonify({'error': 'Invalid state parameter'}), 400
    
    # Exchange code for tokens
    success = QuickBooksService.handle_oauth_callback(
        user_id=user_id,
        auth_code=code,
        realm_id=realm_id
    )
    
    if success:
        # Redirect to frontend success page
        return redirect(f'{os.getenv("FRONTEND_URL")}/settings/integrations?qb=success')
    else:
        return redirect(f'{os.getenv("FRONTEND_URL")}/settings/integrations?qb=error')

@quickbooks_auth_bp.route('/disconnect', methods=['POST'])
@require_auth
def disconnect_quickbooks():
    """Disconnect QuickBooks integration"""
    user_id = request.current_user['id']
    
    success = QuickBooksService.disconnect(user_id)
    
    return jsonify({
        'success': success,
        'message': 'QuickBooks disconnected' if success else 'Failed to disconnect'
    })

@quickbooks_auth_bp.route('/status', methods=['GET'])
@require_auth
def connection_status():
    """Check if user has active QuickBooks connection"""
    user_id = request.current_user['id']
    
    status = QuickBooksService.get_connection_status(user_id)
    
    return jsonify(status)
```

### 2. QuickBooks API Client

**File**: `app/api/quickbooks/client.py`

```python
import requests
from typing import Optional, Dict, Any
import os

class QuickBooksClient:
    """Wrapper for QuickBooks API calls"""
    
    def __init__(self, access_token: str, realm_id: str):
        self.access_token = access_token
        self.realm_id = realm_id
        self.base_url = os.getenv('QUICKBOOKS_API_BASE_URL', 
                                   'https://sandbox-quickbooks.api.intuit.com')
        self.api_version = 'v3'
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict[Any, Any]:
        """Make authenticated request to QuickBooks API"""
        url = f"{self.base_url}/{self.api_version}/company/{self.realm_id}/{endpoint}"
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        response = requests.request(method, url, headers=headers, params=params)
        response.raise_for_status()
        
        return response.json()
    
    def get_company_info(self) -> Dict:
        """Get company information"""
        return self._make_request('GET', 'companyinfo/1')
    
    def get_invoices(self, max_results: int = 100, start_position: int = 1) -> Dict:
        """Fetch invoices"""
        query = f"SELECT * FROM Invoice MAXRESULTS {max_results} STARTPOSITION {start_position}"
        return self._make_request('GET', 'query', params={'query': query})
    
    def get_bills(self, max_results: int = 100, start_position: int = 1) -> Dict:
        """Fetch bills (accounts payable)"""
        query = f"SELECT * FROM Bill MAXRESULTS {max_results} STARTPOSITION {start_position}"
        return self._make_request('GET', 'query', params={'query': query})
    
    def get_payments(self, max_results: int = 100, start_position: int = 1) -> Dict:
        """Fetch payments"""
        query = f"SELECT * FROM Payment MAXRESULTS {max_results} STARTPOSITION {start_position}"
        return self._make_request('GET', 'query', params={'query': query})
    
    def get_bank_accounts(self) -> Dict:
        """Fetch bank account list"""
        query = "SELECT * FROM Account WHERE AccountType = 'Bank'"
        return self._make_request('GET', 'query', params={'query': query})
    
    def get_account_balance(self, account_id: str) -> Dict:
        """Get specific account details with balance"""
        return self._make_request('GET', f'account/{account_id}')
```

### 3. Service Layer

**File**: `app/services/quickbooks_service.py`

```python
from app.models.database import get_db
from app.api.quickbooks.client import QuickBooksClient
from app.utils.encryption import encrypt_token, decrypt_token
import requests
import secrets
import os
from datetime import datetime, timedelta
from typing import Optional, Dict

class QuickBooksService:
    
    OAUTH_AUTHORIZE_URL = 'https://appcenter.intuit.com/connect/oauth2'
    OAUTH_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
    
    # In-memory state storage (use Redis in production)
    _oauth_states = {}
    
    @staticmethod
    def get_authorization_url(user_id: int) -> str:
        """Generate OAuth authorization URL"""
        state = secrets.token_urlsafe(32)
        QuickBooksService._oauth_states[state] = {
            'user_id': user_id,
            'created_at': datetime.now()
        }
        
        params = {
            'client_id': os.getenv('QUICKBOOKS_CLIENT_ID'),
            'redirect_uri': os.getenv('QUICKBOOKS_REDIRECT_URI'),
            'response_type': 'code',
            'scope': 'com.intuit.quickbooks.accounting',
            'state': state
        }
        
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{QuickBooksService.OAUTH_AUTHORIZE_URL}?{query_string}"
    
    @staticmethod
    def verify_state(state: str) -> Optional[int]:
        """Verify OAuth state and return user_id"""
        state_data = QuickBooksService._oauth_states.get(state)
        if not state_data:
            return None
        
        # Clean up state
        del QuickBooksService._oauth_states[state]
        
        return state_data['user_id']
    
    @staticmethod
    def handle_oauth_callback(user_id: int, auth_code: str, realm_id: str) -> bool:
        """Exchange authorization code for tokens and store"""
        
        # Exchange code for tokens
        token_response = requests.post(
            QuickBooksService.OAUTH_TOKEN_URL,
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            auth=(
                os.getenv('QUICKBOOKS_CLIENT_ID'),
                os.getenv('QUICKBOOKS_CLIENT_SECRET')
            ),
            data={
                'grant_type': 'authorization_code',
                'code': auth_code,
                'redirect_uri': os.getenv('QUICKBOOKS_REDIRECT_URI')
            }
        )
        
        if token_response.status_code != 200:
            return False
        
        tokens = token_response.json()
        
        # Get company name
        client = QuickBooksClient(tokens['access_token'], realm_id)
        company_info = client.get_company_info()
        company_name = company_info['CompanyInfo']['CompanyName']
        
        # Store encrypted tokens
        db = get_db()
        db.execute('''
            INSERT INTO user_integrations 
            (user_id, integration_type, access_token, refresh_token, realm_id, 
             token_expires_at, company_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, integration_type) 
            DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                realm_id = excluded.realm_id,
                token_expires_at = excluded.token_expires_at,
                company_name = excluded.company_name,
                connected_at = NOW(),
                is_active = TRUE
        ''', (
            user_id,
            'quickbooks',
            encrypt_token(tokens['access_token']),
            encrypt_token(tokens['refresh_token']),
            realm_id,
            datetime.now() + timedelta(seconds=tokens['expires_in']),
            company_name
        ))
        db.commit()
        
        # Trigger initial sync
        QuickBooksService.sync_data(user_id)
        
        return True
    
    @staticmethod
    def refresh_access_token(integration_id: int) -> bool:
        """Refresh expired access token"""
        db = get_db()
        integration = db.execute(
            'SELECT * FROM user_integrations WHERE id = ?',
            (integration_id,)
        ).fetchone()
        
        if not integration:
            return False
        
        refresh_token = decrypt_token(integration['refresh_token'])
        
        token_response = requests.post(
            QuickBooksService.OAUTH_TOKEN_URL,
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            auth=(
                os.getenv('QUICKBOOKS_CLIENT_ID'),
                os.getenv('QUICKBOOKS_CLIENT_SECRET')
            ),
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token
            }
        )
        
        if token_response.status_code != 200:
            return False
        
        tokens = token_response.json()
        
        db.execute('''
            UPDATE user_integrations 
            SET access_token = ?,
                refresh_token = ?,
                token_expires_at = ?
            WHERE id = ?
        ''', (
            encrypt_token(tokens['access_token']),
            encrypt_token(tokens['refresh_token']),
            datetime.now() + timedelta(seconds=tokens['expires_in']),
            integration_id
        ))
        db.commit()
        
        return True
    
    @staticmethod
    def sync_data(user_id: int):
        """Sync QuickBooks data to Project-R"""
        db = get_db()
        
        # Get active integration
        integration = db.execute(
            'SELECT * FROM user_integrations WHERE user_id = ? AND integration_type = ? AND is_active = TRUE',
            (user_id, 'quickbooks')
        ).fetchone()
        
        if not integration:
            return
        
        # Check if token needs refresh
        if datetime.fromisoformat(integration['token_expires_at']) < datetime.now():
            if not QuickBooksService.refresh_access_token(integration['id']):
                return
            # Reload integration
            integration = db.execute(
                'SELECT * FROM user_integrations WHERE id = ?',
                (integration['id'],)
            ).fetchone()
        
        # Create sync log
        sync_log_id = db.execute(
            'INSERT INTO integration_sync_logs (integration_id, status) VALUES (?, ?) RETURNING id',
            (integration['id'], 'in_progress')
        ).fetchone()['id']
        db.commit()
        
        try:
            access_token = decrypt_token(integration['access_token'])
            client = QuickBooksClient(access_token, integration['realm_id'])
            
            records_synced = {
                'invoices': 0,
                'bills': 0,
                'payments': 0
            }
            
            # Sync Invoices (Accounts Receivable)
            invoices = client.get_invoices()
            for invoice in invoices.get('QueryResponse', {}).get('Invoice', []):
                QuickBooksService._import_invoice(user_id, integration['id'], invoice)
                records_synced['invoices'] += 1
            
            # Sync Bills (Accounts Payable)
            bills = client.get_bills()
            for bill in bills.get('QueryResponse', {}).get('Bill', []):
                QuickBooksService._import_bill(user_id, integration['id'], bill)
                records_synced['bills'] += 1
            
            # Sync Payments
            payments = client.get_payments()
            for payment in payments.get('QueryResponse', {}).get('Payment', []):
                QuickBooksService._import_payment(user_id, integration['id'], payment)
                records_synced['payments'] += 1
            
            # Update sync log
            db.execute('''
                UPDATE integration_sync_logs 
                SET sync_completed_at = NOW(),
                    status = 'success',
                    records_synced = ?
                WHERE id = ?
            ''', (str(records_synced), sync_log_id))
            
            # Update last synced timestamp
            db.execute(
                'UPDATE user_integrations SET last_synced_at = NOW() WHERE id = ?',
                (integration['id'],)
            )
            db.commit()
            
        except Exception as e:
            db.execute('''
                UPDATE integration_sync_logs 
                SET sync_completed_at = NOW(),
                    status = 'failed',
                    error_message = ?
                WHERE id = ?
            ''', (str(e), sync_log_id))
            db.commit()
    
    @staticmethod
    def _import_invoice(user_id: int, integration_id: int, invoice: Dict):
        """Import a single invoice as a transaction"""
        db = get_db()
        
        # Check if already imported
        existing = db.execute(
            'SELECT id FROM quickbooks_transaction_map WHERE integration_id = ? AND quickbooks_id = ?',
            (integration_id, invoice['Id'])
        ).fetchone()
        
        if existing:
            return  # Already imported
        
        # Get user's account_id (assumes user has one account)
        account = db.execute(
            'SELECT id FROM accounts WHERE user_id = ? LIMIT 1',
            (user_id,)
        ).fetchone()
        
        if not account:
            return
        
        # Create transaction
        transaction_id = db.execute('''
            INSERT INTO transactions 
            (account_id, type, amount, description, date, status, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        ''', (
            account['id'],
            'income',
            float(invoice.get('TotalAmt', 0)),
            f"Invoice #{invoice.get('DocNumber')} - {invoice.get('CustomerRef', {}).get('name', 'Unknown')}",
            invoice.get('TxnDate'),
            'pending' if invoice.get('Balance', 0) > 0 else 'completed',
            'Revenue'
        )).fetchone()['id']
        
        # Map to QuickBooks
        db.execute('''
            INSERT INTO quickbooks_transaction_map 
            (integration_id, transaction_id, quickbooks_id, quickbooks_type)
            VALUES (?, ?, ?, ?)
        ''', (integration_id, transaction_id, invoice['Id'], 'Invoice'))
        
        db.commit()
    
    @staticmethod
    def _import_bill(user_id: int, integration_id: int, bill: Dict):
        """Import a single bill as an expense transaction"""
        db = get_db()
        
        existing = db.execute(
            'SELECT id FROM quickbooks_transaction_map WHERE integration_id = ? AND quickbooks_id = ?',
            (integration_id, bill['Id'])
        ).fetchone()
        
        if existing:
            return
        
        account = db.execute(
            'SELECT id FROM accounts WHERE user_id = ? LIMIT 1',
            (user_id,)
        ).fetchone()
        
        if not account:
            return
        
        transaction_id = db.execute('''
            INSERT INTO transactions 
            (account_id, type, amount, description, date, status, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        ''', (
            account['id'],
            'expense',
            float(bill.get('TotalAmt', 0)),
            f"Bill #{bill.get('DocNumber')} - {bill.get('VendorRef', {}).get('name', 'Unknown')}",
            bill.get('TxnDate'),
            'pending' if bill.get('Balance', 0) > 0 else 'completed',
            'Operating Expenses'
        )).fetchone()['id']
        
        db.execute('''
            INSERT INTO quickbooks_transaction_map 
            (integration_id, transaction_id, quickbooks_id, quickbooks_type)
            VALUES (?, ?, ?, ?)
        ''', (integration_id, transaction_id, bill['Id'], 'Bill'))
        
        db.commit()
    
    @staticmethod
    def _import_payment(user_id: int, integration_id: int, payment: Dict):
        """Import payment transaction"""
        # Similar implementation to invoice/bill
        pass
    
    @staticmethod
    def disconnect(user_id: int) -> bool:
        """Disconnect QuickBooks integration"""
        db = get_db()
        db.execute('''
            UPDATE user_integrations 
            SET is_active = FALSE 
            WHERE user_id = ? AND integration_type = ?
        ''', (user_id, 'quickbooks'))
        db.commit()
        return True
    
    @staticmethod
    def get_connection_status(user_id: int) -> Dict:
        """Get integration connection status"""
        db = get_db()
        integration = db.execute(
            'SELECT * FROM user_integrations WHERE user_id = ? AND integration_type = ? AND is_active = TRUE',
            (user_id, 'quickbooks')
        ).fetchone()
        
        if not integration:
            return {'connected': False}
        
        return {
            'connected': True,
            'company_name': integration['company_name'],
            'connected_at': integration['connected_at'],
            'last_synced_at': integration['last_synced_at']
        }
```

### 4. Token Encryption Utility

**File**: `app/utils/encryption.py`

```python
from cryptography.fernet import Fernet
import os

# Load encryption key from environment
ENCRYPTION_KEY = os.getenv('INTEGRATION_ENCRYPTION_KEY').encode()
cipher = Fernet(ENCRYPTION_KEY)

def encrypt_token(token: str) -> str:
    """Encrypt OAuth token"""
    return cipher.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt OAuth token"""
    return cipher.decrypt(encrypted_token.encode()).decode()
```

**Generate encryption key**:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

---

## 🎨 Frontend Implementation

### 1. Integrations Settings Page

**File**: `frontend/src/pages/IntegrationsSettings.jsx`

```jsx
import React, { useEffect, useState } from 'react';
import { Button, Card, Alert } from '../components/ui';
import { api } from '../services/api';

export default function IntegrationsSettings() {
  const [qbStatus, setQbStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    loadConnectionStatus();
    
    // Check for OAuth callback result
    const params = new URLSearchParams(window.location.search);
    if (params.get('qb') === 'success') {
      // Show success message
      setTimeout(loadConnectionStatus, 1000);
    }
  }, []);

  const loadConnectionStatus = async () => {
    try {
      const response = await api.get('/api/quickbooks/status');
      setQbStatus(response.data);
    } catch (error) {
      console.error('Failed to load status', error);
    } finally {
      setLoading(false);
    }
  };

  const handleConnect = async () => {
    try {
      const response = await api.get('/api/quickbooks/connect');
      // Redirect to QuickBooks OAuth
      window.location.href = response.data.authorization_url;
    } catch (error) {
      console.error('Failed to connect', error);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm('Disconnect QuickBooks? Your synced data will remain.')) {
      return;
    }
    
    try {
      await api.post('/api/quickbooks/disconnect');
      setQbStatus({ connected: false });
    } catch (error) {
      console.error('Failed to disconnect', error);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.post('/api/quickbooks/sync');
      await loadConnectionStatus();
      alert('Sync completed!');
    } catch (error) {
      console.error('Sync failed', error);
      alert('Sync failed. Please try again.');
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return <div>Loading...</div>;
  }

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">Integrations</h1>
      
      <Card className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <img 
              src="/quickbooks-logo.svg" 
              alt="QuickBooks" 
              className="w-12 h-12"
            />
            <div>
              <h3 className="text-xl font-semibold">QuickBooks Online</h3>
              <p className="text-gray-600">
                Automatically sync invoices, bills, and payments
              </p>
            </div>
          </div>
          
          {!qbStatus?.connected ? (
            <Button onClick={handleConnect} variant="primary">
              Connect QuickBooks
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button 
                onClick={handleSync} 
                variant="secondary"
                disabled={syncing}
              >
                {syncing ? 'Syncing...' : 'Sync Now'}
              </Button>
              <Button onClick={handleDisconnect} variant="danger">
                Disconnect
              </Button>
            </div>
          )}
        </div>
        
        {qbStatus?.connected && (
          <div className="mt-4 pt-4 border-t">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-600">Company:</span>
                <span className="ml-2 font-medium">{qbStatus.company_name}</span>
              </div>
              <div>
                <span className="text-gray-600">Last Synced:</span>
                <span className="ml-2 font-medium">
                  {qbStatus.last_synced_at 
                    ? new Date(qbStatus.last_synced_at).toLocaleString()
                    : 'Never'}
                </span>
              </div>
            </div>
          </div>
        )}
      </Card>
      
      <Alert className="mt-4" variant="info">
        <strong>Automatic Sync:</strong> QuickBooks data syncs automatically every 6 hours.
        You can also trigger a manual sync at any time.
      </Alert>
    </div>
  );
}
```

### 2. Add Route

**File**: `frontend/src/App.jsx`

```jsx
import IntegrationsSettings from './pages/IntegrationsSettings';

// Add to routes
<Route path="/settings/integrations" element={<IntegrationsSettings />} />
```

### 3. Navigation Link

Add link to settings menu:
```jsx
<NavLink to="/settings/integrations">
  Integrations
</NavLink>
```

---

## 🧪 Testing with Sandbox

### 1. Create Sandbox Company

1. Log in to: https://developer.intuit.com/
2. Navigate to **Sandbox** → **Create Test Company**
3. Choose **Sample Company** (pre-populated with data)

### 2. Test OAuth Flow

```bash
# Start local development server
cd app
python app.py

# Open in browser
http://localhost:3000/settings/integrations

# Click "Connect QuickBooks"
# Complete OAuth flow with sandbox credentials
```

### 3. Test API Calls

```bash
# Get sandbox company info
curl -H "Authorization: Bearer YOUR_SANDBOX_TOKEN" \
  https://sandbox-quickbooks.api.intuit.com/v3/company/REALM_ID/companyinfo/REALM_ID

# Query invoices
curl -H "Authorization: Bearer YOUR_SANDBOX_TOKEN" \
  "https://sandbox-quickbooks.api.intuit.com/v3/company/REALM_ID/query?query=SELECT%20*%20FROM%20Invoice"
```

### 4. Test Sync

```python
# In Python shell
from app.services.quickbooks_service import QuickBooksService

# Trigger sync for test user
QuickBooksService.sync_data(user_id=1)
```

---

## 🚀 Deployment Checklist

### 1. Register Routes

**File**: `app/app.py`

```python
from api.quickbooks.auth import quickbooks_auth_bp

app.register_blueprint(quickbooks_auth_bp, url_prefix='/api/quickbooks')
```

### 2. Set Environment Variables in Railway

```bash
railway variables set QUICKBOOKS_CLIENT_ID="your_client_id"
railway variables set QUICKBOOKS_CLIENT_SECRET="your_secret"
railway variables set QUICKBOOKS_REDIRECT_URI="https://web-production-8d237.up.railway.app/api/quickbooks/callback"
railway variables set QUICKBOOKS_ENVIRONMENT="sandbox"
railway variables set INTEGRATION_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

### 3. Run Database Migration

```bash
# SSH into Railway container
railway run bash

# Run migration
psql $DATABASE_URL < app/migrations/008_quickbooks_integration.sql
```

### 4. Deploy

```bash
git add .
git commit -m "Add QuickBooks integration"
git push origin main
```

---

## 🔄 Automatic Sync (Optional)

### Background Job (Celery/APScheduler)

**File**: `app/tasks/quickbooks_sync.py`

```python
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.quickbooks_service import QuickBooksService
from app.models.database import get_db

def sync_all_active_integrations():
    """Sync all active QuickBooks connections"""
    db = get_db()
    integrations = db.execute('''
        SELECT user_id FROM user_integrations 
        WHERE integration_type = 'quickbooks' AND is_active = TRUE
    ''').fetchall()
    
    for integration in integrations:
        try:
            QuickBooksService.sync_data(integration['user_id'])
        except Exception as e:
            print(f"Sync failed for user {integration['user_id']}: {e}")

# Schedule sync every 6 hours
scheduler = BackgroundScheduler()
scheduler.add_job(sync_all_active_integrations, 'interval', hours=6)
scheduler.start()
```

---

## 📊 Monitoring & Logs

### View Sync History

```sql
-- Recent syncs
SELECT 
  ui.company_name,
  isl.sync_started_at,
  isl.status,
  isl.records_synced,
  isl.error_message
FROM integration_sync_logs isl
JOIN user_integrations ui ON ui.id = isl.integration_id
ORDER BY isl.sync_started_at DESC
LIMIT 20;
```

### Failed Syncs

```sql
SELECT * FROM integration_sync_logs 
WHERE status = 'failed' 
ORDER BY sync_started_at DESC;
```

---

## 🔒 Security Considerations

1. **Token Encryption**: All OAuth tokens encrypted at rest
2. **HTTPS Only**: Enforce SSL for callback URLs
3. **State Verification**: Prevent CSRF attacks in OAuth flow
4. **Token Rotation**: Refresh tokens before expiry
5. **Scope Limitation**: Request only necessary permissions
6. **Error Handling**: Never expose tokens in logs

---

## 📈 Future Enhancements

### Phase 2
- Real-time webhook notifications from QuickBooks
- Bi-directional sync (push Project-R transactions to QB)
- Custom field mapping
- Multi-currency support

### Phase 3
- Stripe integration
- Xero integration  
- Plaid bank connections
- AI-powered categorization

---

## 🆘 Troubleshooting

### Common Issues

**"Invalid Grant" Error**
- Refresh token expired (valid for 100 days)
- User needs to reconnect

**Sandbox vs Production**
- Ensure `QUICKBOOKS_ENVIRONMENT` matches your setup
- Sandbox companies have limited data retention

**Missing Transactions**
- Check sync logs for errors
- Verify QuickBooks permissions scope

---

## 📚 Resources

- [QuickBooks API Docs](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account)
- [OAuth 2.0 Guide](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0)
- [Sandbox Testing](https://developer.intuit.com/app/developer/qbo/docs/develop/sandboxes)

---

**Ready to implement!** Start with database migration, then backend OAuth flow, then frontend UI. Test thoroughly in sandbox before production deployment.
