# QuickBooks Integration Routes
# Add these routes to your main.py Flask application

from flask import request, jsonify, redirect, session
from services.quickbooks import QuickBooksClient
import secrets
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Initialize QuickBooks client
qb_client = QuickBooksClient()

@app.route('/api/quickbooks/connect', methods=['GET'])
def quickbooks_connect():
    """
    Initiate QuickBooks OAuth flow
    Frontend calls this to get the authorization URL
    """
    try:
        user_id = request.args.get('user_id')  # Or get from session/JWT
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Generate CSRF state token
        state = secrets.token_urlsafe(32)
        
        # Store state in session or database for verification
        session[f'qb_state_{user_id}'] = state
        
        # Get authorization URL
        auth_url = qb_client.get_authorization_url(state)
        
        return jsonify({
            'auth_url': auth_url,
            'state': state
        })
        
    except Exception as e:
        logger.error(f"QuickBooks connect error: {str(e)}")
        return jsonify({'error': 'Failed to initiate QuickBooks connection'}), 500


@app.route('/api/quickbooks/callback', methods=['GET'])
def quickbooks_callback():
    """
    OAuth callback - QuickBooks redirects here after user authorizes
    """
    try:
        # Get authorization code and state from query params
        auth_code = request.args.get('code')
        state = request.args.get('state')
        realm_id = request.args.get('realmId')  # QuickBooks Company ID
        error = request.args.get('error')
        
        # Check for errors
        if error:
            logger.error(f"QuickBooks OAuth error: {error}")
            return redirect(f'/settings?qb_error={error}')
        
        if not auth_code or not state or not realm_id:
            return redirect('/settings?qb_error=missing_params')
        
        # Verify state token (CSRF protection)
        # In production, retrieve this from session/database
        # stored_state = session.get(f'qb_state_{user_id}')
        # if state != stored_state:
        #     return redirect('/settings?qb_error=invalid_state')
        
        # Exchange authorization code for tokens
        token_data = qb_client.exchange_code_for_tokens(auth_code)
        
        # Extract tokens
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)
        
        # Encrypt tokens
        encrypted_access = qb_client.encrypt_token(access_token)
        encrypted_refresh = qb_client.encrypt_token(refresh_token)
        
        # Calculate expiration time
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        # Store in database
        # TODO: Get user_id from session/JWT
        user_id = 1  # Replace with actual user ID
        
        # Insert or update user_integrations table
        # Using raw SQL for now - adapt to your ORM
        conn = get_db_connection()  # Your database connection
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO user_integrations 
            (user_id, integration_type, realm_id, access_token, refresh_token, 
             token_expires_at, connected_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, integration_type)
            DO UPDATE SET
                realm_id = EXCLUDED.realm_id,
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                token_expires_at = EXCLUDED.token_expires_at,
                connected_at = EXCLUDED.connected_at,
                status = 'active',
                error_message = NULL
        """, (user_id, 'quickbooks', realm_id, encrypted_access, encrypted_refresh,
              expires_at, datetime.utcnow(), 'active'))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Redirect to settings page with success message
        return redirect('/settings?qb_connected=true')
        
    except Exception as e:
        logger.error(f"QuickBooks callback error: {str(e)}")
        return redirect(f'/settings?qb_error=connection_failed')


@app.route('/api/quickbooks/disconnect', methods=['POST'])
def quickbooks_disconnect():
    """
    Disconnect QuickBooks integration
    """
    try:
        user_id = request.json.get('user_id')  # Or get from session/JWT
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Update status in database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE user_integrations 
            SET status = 'disconnected', last_sync_at = NULL
            WHERE user_id = %s AND integration_type = 'quickbooks'
        """, (user_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'message': 'QuickBooks disconnected'})
        
    except Exception as e:
        logger.error(f"QuickBooks disconnect error: {str(e)}")
        return jsonify({'error': 'Failed to disconnect QuickBooks'}), 500


@app.route('/api/quickbooks/status', methods=['GET'])
def quickbooks_status():
    """
    Check QuickBooks connection status
    """
    try:
        user_id = request.args.get('user_id')  # Or get from session/JWT
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Query database for connection status
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT status, connected_at, last_sync_at, error_message
            FROM user_integrations
            WHERE user_id = %s AND integration_type = 'quickbooks'
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return jsonify({
                'connected': False,
                'status': 'not_connected'
            })
        
        return jsonify({
            'connected': result[0] == 'active',
            'status': result[0],
            'connected_at': result[1].isoformat() if result[1] else None,
            'last_sync_at': result[2].isoformat() if result[2] else None,
            'error_message': result[3]
        })
        
    except Exception as e:
        logger.error(f"QuickBooks status error: {str(e)}")
        return jsonify({'error': 'Failed to get status'}), 500


@app.route('/api/quickbooks/sync', methods=['POST'])
def quickbooks_sync():
    """
    Manually trigger sync from QuickBooks
    """
    try:
        user_id = request.json.get('user_id')  # Or get from session/JWT
        
        if not user_id:
            return jsonify({'error': 'User ID required'}), 400
        
        # Get user's QuickBooks integration
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, realm_id, access_token, refresh_token, token_expires_at
            FROM user_integrations
            WHERE user_id = %s AND integration_type = 'quickbooks' AND status = 'active'
        """, (user_id,))
        
        integration = cursor.fetchone()
        
        if not integration:
            cursor.close()
            conn.close()
            return jsonify({'error': 'QuickBooks not connected'}), 400
        
        integration_id, realm_id, encrypted_access, encrypted_refresh, expires_at = integration
        
        # Decrypt tokens
        access_token = qb_client.decrypt_token(encrypted_access)
        refresh_token = qb_client.decrypt_token(encrypted_refresh)
        
        # Check if token needs refresh
        if datetime.utcnow() >= expires_at:
            # Refresh token
            token_data = qb_client.refresh_access_token(refresh_token)
            access_token = token_data.get('access_token')
            refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', 3600)
            
            # Update database
            encrypted_access = qb_client.encrypt_token(access_token)
            encrypted_refresh = qb_client.encrypt_token(refresh_token)
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            cursor.execute("""
                UPDATE user_integrations
                SET access_token = %s, refresh_token = %s, token_expires_at = %s
                WHERE id = %s
            """, (encrypted_access, encrypted_refresh, expires_at, integration_id))
            conn.commit()
        
        # Create sync log
        cursor.execute("""
            INSERT INTO integration_sync_logs 
            (user_integration_id, sync_started_at, status, sync_type)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (integration_id, datetime.utcnow(), 'in_progress', 'manual'))
        
        sync_log_id = cursor.fetchone()[0]
        conn.commit()
        
        # Perform sync
        try:
            sync_results = qb_client.sync_to_project_r(user_id, access_token, realm_id)
            
            # Update sync log
            cursor.execute("""
                UPDATE integration_sync_logs
                SET sync_completed_at = %s,
                    status = %s,
                    records_processed = %s,
                    records_success = %s,
                    records_failed = %s,
                    error_details = %s
                WHERE id = %s
            """, (
                datetime.utcnow(),
                'success' if not sync_results['errors'] else 'partial',
                sum([sync_results['invoices_synced'], sync_results['bills_synced'], sync_results['payments_synced']]),
                sum([sync_results['invoices_synced'], sync_results['bills_synced'], sync_results['payments_synced']]) - len(sync_results['errors']),
                len(sync_results['errors']),
                {'errors': sync_results['errors']} if sync_results['errors'] else None,
                sync_log_id
            ))
            
            # Update last_sync_at
            cursor.execute("""
                UPDATE user_integrations
                SET last_sync_at = %s
                WHERE id = %s
            """, (datetime.utcnow(), integration_id))
            
            conn.commit()
            
            return jsonify({
                'success': True,
                'sync_results': sync_results
            })
            
        except Exception as sync_error:
            # Update sync log with error
            cursor.execute("""
                UPDATE integration_sync_logs
                SET sync_completed_at = %s,
                    status = 'failed',
                    error_details = %s
                WHERE id = %s
            """, (datetime.utcnow(), {'error': str(sync_error)}, sync_log_id))
            conn.commit()
            
            raise sync_error
        
        finally:
            cursor.close()
            conn.close()
        
    except Exception as e:
        logger.error(f"QuickBooks sync error: {str(e)}")
        return jsonify({'error': f'Sync failed: {str(e)}'}), 500
