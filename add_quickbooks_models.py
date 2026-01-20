#!/usr/bin/env python3
"""
Script to add QuickBooks integration models to models.py
"""

models_addition = '''

# ============================================================================
# QuickBooks Integration Models
# ============================================================================

class UserIntegration(Base):
    """Store OAuth tokens and integration metadata for each user"""
    __tablename__ = "user_integrations"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    integration_type: Mapped[str] = mapped_column(String(32))  # 'quickbooks', 'xero', etc
    
    # OAuth tokens (encrypted)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    realm_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # QuickBooks company ID
    
    # Token expiry
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Integration metadata
    company_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Sync settings
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_frequency_hours: Mapped[int] = mapped_column(Integer, default=24)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class IntegrationSyncLog(Base):
    """Log sync operations for debugging and monitoring"""
    __tablename__ = "integration_sync_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_integration_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_integrations.id"), index=True)
    
    sync_started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sync_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    status: Mapped[str] = mapped_column(String(32))  # 'success', 'partial', 'failed'
    transactions_synced: Mapped[int] = mapped_column(Integer, default=0)
    
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string


class QuickBooksTransactionMap(Base):
    """Map QuickBooks transactions to Project-R transactions (for updates/deduplication)"""
    __tablename__ = "quickbooks_transaction_map"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    transaction_id: Mapped[int] = mapped_column(Integer, ForeignKey("transactions.id"), index=True)
    
    # QuickBooks identifiers
    qb_transaction_id: Mapped[str] = mapped_column(String(128), index=True)
    qb_transaction_type: Mapped[str] = mapped_column(String(64))  # Invoice, Payment, Expense, etc
    
    # Sync metadata
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    qb_last_modified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
'''

# Read the current models.py file
with open('/home/tasklet/project-r/api/app/models.py', 'r') as f:
    content = f.read()

# Append the new models
with open('/home/tasklet/project-r/api/app/models.py', 'a') as f:
    f.write(models_addition)

print("✓ QuickBooks models added to models.py")
print("  - UserIntegration")
print("  - IntegrationSyncLog")  
print("  - QuickBooksTransactionMap")
