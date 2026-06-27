"""
PROJECT-R DATA MODELS
Multi-tenant with offset transaction support
"""
from typing import Optional
from datetime import datetime as dt
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Numeric, Boolean, Text, func, UniqueConstraint, ForeignKey

class Base(DeclarativeBase):
    pass

class User(Base):
    """Multi-tenant user accounts"""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    plan: Mapped[str] = mapped_column(String(16), default="free")  # free|pro
    
    # Company info
    company_name: Mapped[str] = mapped_column(String(128), default="")
    company_website: Mapped[str] = mapped_column(String(256), default="")
    logo_url: Mapped[str] = mapped_column(String(512), default="")
    primary_color: Mapped[str] = mapped_column(String(16), default="#FF6B00")
    secondary_color: Mapped[str] = mapped_column(String(16), default="#FF8A65")
    
    # Onboarding state
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    quickbooks_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Password reset
    reset_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    reset_token_expires: Mapped[Optional[dt]] = mapped_column(DateTime, nullable=True, default=None)
    
    # Starting balance for forecasts
    starting_balance: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())

class Transaction(Base):
    """Raw bank transactions"""
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date_posted: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    amount_signed: Mapped[float] = mapped_column(Numeric(12,2))       # +in, -out
    description: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str] = mapped_column(String(128), default="")
    method: Mapped[str] = mapped_column(String(16), default="")       # ach|wire|check|card
    balance: Mapped[float] = mapped_column(Numeric(12,2), nullable=True)
    
    # Categorization
    group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    is_offset: Mapped[bool] = mapped_column(Boolean, default=False)   # True = offset transaction
    
    # Starred/Favorites
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    star_note: Mapped[str] = mapped_column(Text, default="")
    
    # Audit
    raw_file_id: Mapped[str] = mapped_column(String(64), default="")
    
    __table_args__ = (
        UniqueConstraint("user_id", "date_posted", "amount_signed", "description", name="uq_txn_dedupe"),
    )

class TransactionGroup(Base):
    """User-defined transaction groups with offset support"""
    __tablename__ = "transaction_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    
    # Category from predefined list
    category: Mapped[str] = mapped_column(String(64), default="uncategorized")
    
    # Stream type (primary direction)
    stream_type: Mapped[str] = mapped_column(String(16))  # inflow | outflow
    
    # Frequency
    frequency: Mapped[str] = mapped_column(String(16))  # daily | weekly | semimonthly | monthly | uncommon
    
    # For scheduling
    typical_day_of_month: Mapped[int] = mapped_column(Integer, nullable=True)
    typical_day_of_week: Mapped[int] = mapped_column(Integer, nullable=True)
    
    # Computed stats
    avg_amount: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    net_amount: Mapped[float] = mapped_column(Numeric(12,2), default=0)  # After offsets
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    offset_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Offset support - allows opposite-sign transactions (chargebacks, rebates, etc.)
    allow_offsets: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Auto-matching pattern
    pattern_keywords: Mapped[str] = mapped_column(Text, default="")
    
    # User-set trend expectation (up/down/flat)
    trend: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default="flat")
    
    # Calculated trend from data
    calculated_trend_percent: Mapped[float] = mapped_column(Numeric(8,2), default=0)  # e.g., -10.5 means down 10.5%
    
    # User trend adjustments
    adjusted_trend_percent: Mapped[Optional[float]] = mapped_column(Numeric(8,2), nullable=True)  # Override calculated
    trend_period: Mapped[str] = mapped_column(String(16), default="month")  # day|week|month
    trend_duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # How long trend applies (null = indefinite)
    trend_then: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # flat|up|down after duration
    trend_then_percent: Mapped[Optional[float]] = mapped_column(Numeric(8,2), nullable=True)  # Rate after duration
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_group_name"),
    )


class GroupCorrelation(Base):
    """Correlations between groups - when one changes, another follows"""
    __tablename__ = "group_correlations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    
    # Source group (the trigger)
    source_group_id: Mapped[int] = mapped_column(Integer, index=True)
    
    # Target group (the effect)
    target_group_id: Mapped[int] = mapped_column(Integer, index=True)
    
    # Correlation details
    direction: Mapped[str] = mapped_column(String(8))  # same|opposite (same=both up/down, opposite=inverse)
    percent: Mapped[float] = mapped_column(Numeric(8,2))  # e.g., 50 means 50% of source change
    
    # Delay before effect applies
    delay_value: Mapped[int] = mapped_column(Integer, default=0)
    delay_unit: Mapped[str] = mapped_column(String(16), default="days")  # days|weeks|months
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("source_group_id", "target_group_id", name="uq_correlation"),
    )

class ScheduleRule(Base):
    """Scheduled/recurring transaction rules"""
    __tablename__ = "schedule_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    
    name: Mapped[str] = mapped_column(String(128), default="")
    rule_type: Mapped[str] = mapped_column(String(32))  # monthly|semi_monthly|weekly|fixed_dates
    rule_params_json: Mapped[str] = mapped_column(Text, default="{}")
    amount: Mapped[float] = mapped_column(Numeric(12,2))
    priority: Mapped[str] = mapped_column(String(16), default="must")
    confidence: Mapped[str] = mapped_column(String(16), default="high")

class UpcomingBill(Base):
    """User-entered upcoming bills due in the future.

    These are one-off (or optionally recurring) known obligations the user
    knows about but that aren't yet captured by historical transactions or
    schedule rules. They are subtracted from projected cash on/after their
    due date so the forecast reflects them.
    """
    __tablename__ = "upcoming_bills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(128))          # payee / what the bill is for
    amount: Mapped[float] = mapped_column(Numeric(12, 2))   # always stored positive (an outflow)
    due_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD

    # Optional recurrence: none|monthly. "monthly" repeats on the same
    # day-of-month from due_date through the forecast horizon.
    recurrence: Mapped[str] = mapped_column(String(16), default="none")

    notes: Mapped[str] = mapped_column(Text, default="")
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)  # paid bills drop out of the forecast

    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())

class TrendSentiment(Base):
    """User sentiment on trends"""
    __tablename__ = "trend_sentiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=True)
    
    historical_trend: Mapped[str] = mapped_column(String(16))  # up | down | flat
    trend_pct_per_month: Mapped[float] = mapped_column(Numeric(6,2), default=0)
    
    expected_direction: Mapped[str] = mapped_column(String(16))  # continue | flatten | reverse
    expected_change_pct: Mapped[float] = mapped_column(Numeric(6,2), default=0)
    
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class CalendarDay(Base):
    """Working day calendar"""
    __tablename__ = "calendar_days"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False)
    is_working_day: Mapped[bool] = mapped_column(Boolean, default=True)
    batch_weight: Mapped[float] = mapped_column(Numeric(8,4), default=1.0)
    
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_calendar_day"),
    )

class AICache(Base):
    """Cache AI responses"""
    __tablename__ = "ai_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    value_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())

class UsageDaily(Base):
    """Track usage for rate limiting"""
    __tablename__ = "usage_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_usage_day"),
    )

class SavedScenario(Base):
    """Saved what-if scenarios"""
    __tablename__ = "saved_scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    
    # Scenario parameters (JSON)
    scenario_type: Mapped[str] = mapped_column(String(32))  # revenue_change | expense_change | combined
    params_json: Mapped[str] = mapped_column(Text, default="{}")  # {"weekly_delta": 5000, "group_id": null}
    
    # Calculated results at save time
    baseline_30day: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    adjusted_30day: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    baseline_low: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    adjusted_low: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # Apply to projections
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_scenario_name"),
    )

class FavoriteReport(Base):
    """Quick access to favorite views/reports"""
    __tablename__ = "favorite_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    
    name: Mapped[str] = mapped_column(String(128))
    icon: Mapped[str] = mapped_column(String(8), default="📊")
    
    # Report configuration
    report_type: Mapped[str] = mapped_column(String(32))  # projection | transactions | groups | chat_query
    config_json: Mapped[str] = mapped_column(Text, default="{}")  # {"period": "weekly", "days": 30, "group_id": null}
    
    # For chat queries
    query_text: Mapped[str] = mapped_column(Text, default="")
    
    # Display order
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())

# Predefined categories
CATEGORIES = {
    "outflow": [
        {"id": "payroll", "name": "Payroll", "icon": "💼", "allow_offsets": False},
        {"id": "rent", "name": "Rent", "icon": "🏢", "allow_offsets": False},
        {"id": "utilities", "name": "Utilities", "icon": "💡", "allow_offsets": True},
        {"id": "insurance", "name": "Insurance", "icon": "🛡️", "allow_offsets": True},
        {"id": "taxes", "name": "Taxes", "icon": "🏛️", "allow_offsets": True},
        {"id": "legal_accounting", "name": "Legal/Accounting", "icon": "⚖️", "allow_offsets": False},
        {"id": "cogs", "name": "Cost of Goods Sold", "icon": "📦", "allow_offsets": True},
        {"id": "credit_card", "name": "Credit Card Payments", "icon": "💳", "allow_offsets": False},
        {"id": "distributions", "name": "Owner Distributions", "icon": "💰", "allow_offsets": False},
        {"id": "subscriptions", "name": "Subscriptions/IT", "icon": "🔧", "allow_offsets": False},
        {"id": "marketing", "name": "Marketing/Advertising", "icon": "📢", "allow_offsets": True},
        {"id": "refunds_out", "name": "Refunds Issued", "icon": "↩️", "allow_offsets": False},
        {"id": "other_expense", "name": "Other Expenses", "icon": "📋", "allow_offsets": True},
    ],
    "inflow": [
        {"id": "revenue_cc", "name": "Credit Card Revenue", "icon": "💳", "allow_offsets": True},  # Chargebacks
        {"id": "revenue_check", "name": "Check Deposits", "icon": "📄", "allow_offsets": True},    # Bounced checks
        {"id": "revenue_wire", "name": "Wire Transfers In", "icon": "🏦", "allow_offsets": False},
        {"id": "revenue_ach", "name": "ACH Deposits", "icon": "🔄", "allow_offsets": True},        # Returns
        {"id": "revenue_cash", "name": "Cash Revenue", "icon": "💵", "allow_offsets": False},
        {"id": "refunds_received", "name": "Refunds Received", "icon": "↩️", "allow_offsets": False},
        {"id": "other_income", "name": "Other Income", "icon": "💰", "allow_offsets": True},
    ]
}


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
    token_expires_at: Mapped[Optional[dt]] = mapped_column(DateTime, nullable=True)
    
    # Integration metadata
    company_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Sync settings
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_frequency_hours: Mapped[int] = mapped_column(Integer, default=24)
    last_sync_at: Mapped[Optional[dt]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[dt] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class IntegrationSyncLog(Base):
    """Log sync operations for debugging and monitoring"""
    __tablename__ = "integration_sync_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_integration_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_integrations.id"), index=True)
    
    sync_started_at: Mapped[dt] = mapped_column(DateTime, server_default=func.now())
    sync_completed_at: Mapped[Optional[dt]] = mapped_column(DateTime, nullable=True)
    
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
    last_synced_at: Mapped[dt] = mapped_column(DateTime, server_default=func.now())
    qb_last_modified: Mapped[Optional[dt]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[dt] = mapped_column(DateTime, server_default=func.now())
