#!/usr/bin/env python3
"""
Script to add QuickBooks routes to main.py
"""

routes_addition = '''

# ============================================================================
# QuickBooks Integration Routes
# ============================================================================

@app.get("/api/quickbooks/auth-url")
async def get_quickbooks_auth_url(user=Depends(get_current_user)):
    """Generate QuickBooks OAuth authorization URL"""
    from .services.quickbooks import QuickBooksService
    
    try:
        qb_service = QuickBooksService()
        auth_url = qb_service.get_authorization_url(user.id)
        return {"auth_url": auth_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate auth URL: {str(e)}")


@app.get("/api/quickbooks/callback")
async def quickbooks_callback(
    code: str,
    state: str,
    realmId: str,
):
    """Handle OAuth callback from QuickBooks"""
    from .services.quickbooks import QuickBooksService
    import json
    
    try:
        # Parse state to get user_id
        state_data = json.loads(state)
        user_id = state_data.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid state parameter")
        
        # Exchange code for tokens
        qb_service = QuickBooksService()
        db = next(get_db())
        
        integration = qb_service.handle_oauth_callback(db, user_id, code, realmId)
        
        # Redirect to frontend settings page with success
        frontend_url = os.getenv("FRONTEND_URL", "https://web-production-8d237.up.railway.app")
        return RedirectResponse(url=f"{frontend_url}/settings?quickbooks=connected")
        
    except Exception as e:
        # Redirect to frontend with error
        frontend_url = os.getenv("FRONTEND_URL", "https://web-production-8d237.up.railway.app")
        return RedirectResponse(url=f"{frontend_url}/settings?quickbooks=error&message={str(e)}")


@app.get("/api/quickbooks/status")
async def get_quickbooks_status(user=Depends(get_current_user)):
    """Get QuickBooks connection status for current user"""
    from .services.quickbooks import QuickBooksService
    
    db = next(get_db())
    qb_service = QuickBooksService()
    
    integration = qb_service.get_user_integration(db, user.id)
    
    if not integration:
        return {
            "connected": False,
            "company_name": None,
            "last_sync": None
        }
    
    return {
        "connected": integration.is_active,
        "company_name": integration.company_name,
        "last_sync": integration.last_sync_at.isoformat() if integration.last_sync_at else None,
        "auto_sync_enabled": integration.auto_sync_enabled
    }


@app.post("/api/quickbooks/sync")
async def sync_quickbooks(user=Depends(get_current_user)):
    """Manually trigger QuickBooks data sync"""
    from .services.quickbooks import QuickBooksService
    
    db = next(get_db())
    qb_service = QuickBooksService()
    
    integration = qb_service.get_user_integration(db, user.id)
    if not integration or not integration.is_active:
        raise HTTPException(status_code=400, detail="QuickBooks not connected")
    
    try:
        result = qb_service.sync_transactions(db, user.id)
        return {
            "success": True,
            "transactions_synced": result.get("transactions_synced", 0),
            "message": "Sync completed successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@app.delete("/api/quickbooks/disconnect")
async def disconnect_quickbooks(user=Depends(get_current_user)):
    """Disconnect QuickBooks integration"""
    from .services.quickbooks import QuickBooksService
    
    db = next(get_db())
    qb_service = QuickBooksService()
    
    success = qb_service.disconnect_integration(db, user.id)
    
    if success:
        return {"success": True, "message": "QuickBooks disconnected successfully"}
    else:
        raise HTTPException(status_code=404, detail="No QuickBooks integration found")


@app.post("/api/quickbooks/settings")
async def update_quickbooks_settings(
    auto_sync_enabled: bool = True,
    sync_frequency_hours: int = 24,
    user=Depends(get_current_user)
):
    """Update QuickBooks sync settings"""
    from .services.quickbooks import QuickBooksService
    
    db = next(get_db())
    qb_service = QuickBooksService()
    
    integration = qb_service.get_user_integration(db, user.id)
    if not integration:
        raise HTTPException(status_code=404, detail="QuickBooks not connected")
    
    integration.auto_sync_enabled = auto_sync_enabled
    integration.sync_frequency_hours = sync_frequency_hours
    db.commit()
    
    return {"success": True, "message": "Settings updated"}
'''

# Read current main.py
with open('/home/tasklet/project-r/api/app/main.py', 'r') as f:
    content = f.read()

# Append the routes
with open('/home/tasklet/project-r/api/app/main.py', 'a') as f:
    f.write(routes_addition)

print("✓ QuickBooks routes added to main.py")
print("  - GET /api/quickbooks/auth-url")
print("  - GET /api/quickbooks/callback")
print("  - GET /api/quickbooks/status")
print("  - POST /api/quickbooks/sync")
print("  - DELETE /api/quickbooks/disconnect")
print("  - POST /api/quickbooks/settings")
