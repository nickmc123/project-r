# QuickBooks Integration Models
# Add these to your existing models.py file

"""
Database schema for QuickBooks integration:

1. user_integrations - Stores OAuth tokens and connection status
2. integration_sync_logs - Tracks sync history
3. quickbooks_transaction_map - Maps QB transactions to Project-R transactions
"""

# SQL for creating the tables:

CREATE_USER_INTEGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS user_integrations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    integration_type VARCHAR(50) NOT NULL,  -- 'quickbooks', 'xero', etc
    realm_id VARCHAR(255),  -- QuickBooks Company ID
    access_token TEXT NOT NULL,  -- Encrypted
    refresh_token TEXT NOT NULL,  -- Encrypted
    token_expires_at TIMESTAMP,
    connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_sync_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'active',  -- 'active', 'disconnected', 'error'
    error_message TEXT,
    metadata JSONB,  -- Store additional connection info
    UNIQUE(user_id, integration_type)
);

CREATE INDEX idx_user_integrations_user_id ON user_integrations(user_id);
CREATE INDEX idx_user_integrations_status ON user_integrations(status);
"""

CREATE_INTEGRATION_SYNC_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS integration_sync_logs (
    id SERIAL PRIMARY KEY,
    user_integration_id INTEGER NOT NULL REFERENCES user_integrations(id) ON DELETE CASCADE,
    sync_started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sync_completed_at TIMESTAMP,
    status VARCHAR(20),  -- 'in_progress', 'success', 'partial', 'failed'
    records_processed INTEGER DEFAULT 0,
    records_success INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_details JSONB,
    sync_type VARCHAR(50),  -- 'manual', 'auto', 'initial'
    metadata JSONB
);

CREATE INDEX idx_sync_logs_integration ON integration_sync_logs(user_integration_id);
CREATE INDEX idx_sync_logs_status ON integration_sync_logs(status);
"""

CREATE_QB_TRANSACTION_MAP_TABLE = """
CREATE TABLE IF NOT EXISTS quickbooks_transaction_map (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_r_transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    qb_transaction_type VARCHAR(50),  -- 'invoice', 'bill', 'payment', 'expense'
    qb_transaction_id VARCHAR(100) NOT NULL,
    qb_doc_number VARCHAR(100),
    qb_amount DECIMAL(15,2),
    qb_date DATE,
    qb_customer_vendor VARCHAR(255),
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sync_status VARCHAR(20) DEFAULT 'synced',  -- 'synced', 'modified', 'deleted'
    raw_data JSONB,  -- Store full QB response
    UNIQUE(user_id, qb_transaction_type, qb_transaction_id)
);

CREATE INDEX idx_qb_map_user ON quickbooks_transaction_map(user_id);
CREATE INDEX idx_qb_map_transaction ON quickbooks_transaction_map(project_r_transaction_id);
CREATE INDEX idx_qb_map_qb_id ON quickbooks_transaction_map(qb_transaction_id);
"""

# Python model classes (if using SQLAlchemy):

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, DECIMAL, JSON, ForeignKey, Date
from sqlalchemy.orm import relationship

class UserIntegration(Base):
    __tablename__ = 'user_integrations'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    integration_type = Column(String(50), nullable=False)
    realm_id = Column(String(255))
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime)
    connected_at = Column(DateTime, default=datetime.utcnow)
    last_sync_at = Column(DateTime)
    status = Column(String(20), default='active')
    error_message = Column(Text)
    metadata = Column(JSON)
    
    # Relationships
    sync_logs = relationship('IntegrationSyncLog', back_populates='integration', cascade='all, delete-orphan')

class IntegrationSyncLog(Base):
    __tablename__ = 'integration_sync_logs'
    
    id = Column(Integer, primary_key=True)
    user_integration_id = Column(Integer, ForeignKey('user_integrations.id'), nullable=False)
    sync_started_at = Column(DateTime, default=datetime.utcnow)
    sync_completed_at = Column(DateTime)
    status = Column(String(20))
    records_processed = Column(Integer, default=0)
    records_success = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)
    error_details = Column(JSON)
    sync_type = Column(String(50))
    metadata = Column(JSON)
    
    # Relationships
    integration = relationship('UserIntegration', back_populates='sync_logs')

class QuickBooksTransactionMap(Base):
    __tablename__ = 'quickbooks_transaction_map'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    project_r_transaction_id = Column(Integer, ForeignKey('transactions.id'))
    qb_transaction_type = Column(String(50))
    qb_transaction_id = Column(String(100), nullable=False)
    qb_doc_number = Column(String(100))
    qb_amount = Column(DECIMAL(15, 2))
    qb_date = Column(Date)
    qb_customer_vendor = Column(String(255))
    synced_at = Column(DateTime, default=datetime.utcnow)
    last_updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sync_status = Column(String(20), default='synced')
    raw_data = Column(JSON)
