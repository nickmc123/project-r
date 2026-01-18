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
    
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_group_name"),
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
