"""
PROJECT-R API
Multi-tenant cash flow forecasting
"""
import os
import httpx
import secrets
from datetime import date, datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from .db import engine, get_db
from .models import Base, User, Transaction, TransactionGroup, ScheduleRule, TrendSentiment, CATEGORIES
from .auth import hash_pw, verify_pw, create_token, get_current_user
from .services.ingest import ingest_bank_csv, ingest_quickbooks_data
from .services.categorize import (
    auto_categorize_transactions, create_group, move_transactions_to_group,
    get_groups_for_user, get_group_transactions, update_group_stats
)
from .services.forecast import compute_forecast, analyze_trends

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Project-R", version="1.0", description="Cash Flow Forecasting")

# Mount static files
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Pydantic models for requests
class SignupRequest(BaseModel):
    email: str
    password: str
    company_name: Optional[str] = None
    company_website: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class CompanyInfoRequest(BaseModel):
    company_name: str
    company_website: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    logo_url: Optional[str] = None

class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""
    category: str
    stream_type: str  # inflow | outflow
    frequency: str  # daily | weekly | semimonthly | monthly | uncommon
    transaction_ids: List[int] = []
    allow_offsets: bool = False

class MoveTransactionsRequest(BaseModel):
    transaction_ids: List[int]
    target_group_id: int
    mark_as_offset: bool = False

class TrendSentimentRequest(BaseModel):
    group_id: int
    expected_direction: str  # continue | flatten | reverse
    expected_change_pct: float = 0

class ScheduleRuleRequest(BaseModel):
    name: str
    group_id: Optional[int] = None
    rule_type: str  # monthly | semi_monthly | weekly | fixed_dates
    rule_params: dict = {}
    amount: float
    priority: str = "must"

# Routes
@app.get("/")
def root():
    return FileResponse(os.path.join(static_path, "index.html"))

@app.get("/health")
def health():
    return {"ok": True, "version": "1.0"}

# Auth
@app.post("/auth/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db), background_tasks: BackgroundTasks = None):
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already exists")
    
    user = User(
        email=req.email.lower(),
        password_hash=hash_pw(req.password),
        company_name=req.company_name or "",
        company_website=req.company_website or "",
        plan="free"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Send signup notification webhook
    try:
        webhook_url = os.environ.get("SIGNUP_WEBHOOK_URL")
        if webhook_url:
            import httpx
            httpx.post(webhook_url, json={
                "event": "new_signup",
                "email": user.email,
                "company_name": user.company_name or "(not set)",
                "company_website": user.company_website or "",
                "timestamp": datetime.now().isoformat()
            }, timeout=5)
    except Exception as e:
        print(f"Webhook notification failed: {e}")
    
    return {
        "token": create_token(user.id),
        "user": {
            "email": user.email,
            "company_name": user.company_name,
            "onboarding_step": user.onboarding_step,
            "plan": user.plan
        }
    }

@app.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if not user or not verify_pw(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    return {
        "token": create_token(user.id),
        "user": {
            "email": user.email,
            "company_name": user.company_name,
            "onboarding_step": user.onboarding_step,
            "onboarding_complete": user.onboarding_complete,
            "plan": user.plan
        }
    }

@app.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Send password reset token"""
    user = db.query(User).filter(User.email == req.email.lower()).first()
    
    # Always return success to avoid leaking email existence
    if not user:
        return {"ok": True, "message": "If that email exists, a reset link has been sent"}
    
    # Generate reset token
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
    db.commit()
    
    # Send notification via webhook
    try:
        webhook_url = os.environ.get("SIGNUP_WEBHOOK_URL")
        if webhook_url:
            httpx.post(webhook_url, json={
                "event": "password_reset_requested",
                "email": user.email,
                "company_name": user.company_name or "(not set)",
                "reset_token": token,
                "expires": user.reset_token_expires.isoformat(),
                "timestamp": datetime.now().isoformat()
            }, timeout=5)
    except Exception as e:
        print(f"Webhook notification failed: {e}")
    
    return {"ok": True, "message": "If that email exists, a reset link has been sent"}

@app.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using token"""
    user = db.query(User).filter(User.reset_token == req.token).first()
    
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    if user.reset_token_expires and user.reset_token_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Reset token has expired")
    
    # Update password and clear token
    user.password_hash = hash_pw(req.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    
    return {"ok": True, "message": "Password has been reset. You can now login."}

@app.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "email": user.email,
        "company_name": user.company_name,
        "company_website": user.company_website,
        "logo_url": user.logo_url,
        "primary_color": user.primary_color,
        "secondary_color": user.secondary_color,
        "onboarding_step": user.onboarding_step,
        "onboarding_complete": user.onboarding_complete,
        "plan": user.plan
    }

# Onboarding
@app.post("/onboarding/company")
async def set_company_info(req: CompanyInfoRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 1: Set company info, optionally fetch logo/colors from website"""
    user.company_name = req.company_name
    
    if req.company_website:
        user.company_website = req.company_website
        
        # Try to fetch branding from website
        if not req.logo_url:
            try:
                async with httpx.AsyncClient() as client:
                    # Try common favicon locations
                    for path in ["/favicon.ico", "/apple-touch-icon.png", "/logo.png"]:
                        try:
                            r = await client.head(f"https://{req.company_website}{path}", timeout=5)
                            if r.status_code == 200:
                                user.logo_url = f"https://{req.company_website}{path}"
                                break
                        except:
                            continue
            except:
                pass
    
    if req.primary_color:
        user.primary_color = req.primary_color
    if req.secondary_color:
        user.secondary_color = req.secondary_color
    if req.logo_url:
        user.logo_url = req.logo_url
    
    user.onboarding_step = max(user.onboarding_step, 1)
    db.commit()
    
    return {"ok": True, "step": 1}

@app.post("/onboarding/upload-data")
async def upload_bank_data(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 2: Upload bank data (CSV or paste)"""
    content = await file.read()
    text = content.decode('utf-8', errors='ignore')
    from .services.ingest import ingest_bank_data
    result = ingest_bank_data(db, user.id, text, raw_file_id=file.filename or "")
    
    user.onboarding_step = max(user.onboarding_step, 2)
    db.commit()
    
    return {
        "ok": True,
        "step": 2,
        "imported": result
    }

@app.post("/onboarding/paste-data")
async def paste_bank_data(data: str = Body(..., embed=True), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 2 alternative: Paste bank data"""
    from .services.ingest import ingest_bank_data
    result = ingest_bank_data(db, user.id, data, raw_file_id="pasted")
    
    user.onboarding_step = max(user.onboarding_step, 2)
    db.commit()
    
    return {
        "ok": True,
        "step": 2,
        "imported": result
    }

@app.post("/import")
async def import_data(data: str = Body(..., embed=True), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Import bank data (post-onboarding)"""
    from .services.ingest import ingest_bank_data
    result = ingest_bank_data(db, user.id, data, raw_file_id="import")
    return {"ok": True, "result": result}

@app.post("/import/analyze")
async def analyze_import_data(
    data: str = Body(..., embed=True),
    user: User = Depends(get_current_user)
):
    """
    Step 1: Analyze data and return either:
    - Parsed transactions for confirmation (if confident)
    - Data structure analysis for user field mapping (if not confident)
    """
    from .services.ingest import auto_parse_data
    
    transactions, analysis, debug_log = auto_parse_data(data)
    
    if analysis.get("needs_user_input"):
        # Return analysis so user can define field mapping
        return {
            "ok": True,
            "status": "needs_mapping",
            "message": "Please help identify the fields in your data",
            "analysis": {
                "columns": analysis.get("columns", []),
                "sample_data": analysis.get("sample_data", []),
                "separator": analysis.get("separator"),
                "has_header": analysis.get("has_header"),
                "num_columns": analysis.get("num_columns"),
                "total_rows": analysis.get("total_rows")
            },
            "debug": debug_log
        }
    else:
        # Return parsed transactions for confirmation
        return {
            "ok": True,
            "status": "parsed",
            "message": f"Found {len(transactions)} transactions - please confirm",
            "transactions": [
                {
                    "id": i,
                    "date": t["date"].isoformat() if t.get("date") else None,
                    "amount": abs(t.get("signed_amount", t["amount"])),
                    "type": t.get("type", "debit"),
                    "description": t.get("description", ""),
                    "balance": t.get("balance"),
                    "account": t.get("account"),
                    "category": t.get("category"),
                    "raw_line": t.get("raw_line", "")
                }
                for i, t in enumerate(transactions)
            ],
            "detected_mapping": analysis.get("detected_mapping"),
            "debug": debug_log
        }

@app.post("/import/with-mapping")
async def import_with_mapping(
    data: str = Body(...),
    mapping: dict = Body(...),
    user: User = Depends(get_current_user)
):
    """
    Parse data using user-defined field mapping.
    Returns transactions for confirmation.
    """
    from .services.ingest import parse_with_mapping
    
    transactions, debug_log = parse_with_mapping(data, mapping)
    
    return {
        "ok": True,
        "status": "parsed",
        "message": f"Parsed {len(transactions)} transactions with your mapping",
        "transactions": [
            {
                "id": i,
                "date": t["date"].isoformat() if t.get("date") else None,
                "amount": abs(t.get("signed_amount", t["amount"])),
                "type": t.get("type", "debit"),
                "description": t.get("description", ""),
                "balance": t.get("balance"),
                "account": t.get("account"),
                "category": t.get("category"),
                "raw_line": t.get("raw_line", "")
            }
            for i, t in enumerate(transactions)
        ],
        "mapping_used": mapping,
        "debug": debug_log
    }

class TransactionEdit(BaseModel):
    id: int
    date: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None  # "debit" or "credit"
    description: Optional[str] = None
    account: Optional[str] = None
    category: Optional[str] = None
    delete: Optional[bool] = False

class ConfirmImportRequest(BaseModel):
    transactions: List[dict]
    edits: Optional[List[TransactionEdit]] = None

@app.post("/import/confirm")
async def confirm_import(
    request: ConfirmImportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Confirm and save transactions after user review.
    Accepts optional edits to fix any issues.
    """
    from datetime import datetime
    
    transactions = request.transactions
    edits = {e.id: e for e in (request.edits or [])}
    
    saved = 0
    duplicates = 0
    deleted = 0
    
    for txn in transactions:
        txn_id = txn.get("id")
        
        # Check if user marked for deletion
        if txn_id in edits and edits[txn_id].delete:
            deleted += 1
            continue
        
        # Apply any edits
        if txn_id in edits:
            edit = edits[txn_id]
            if edit.date:
                txn["date"] = edit.date
            if edit.amount is not None:
                txn["amount"] = edit.amount
            if edit.type:
                txn["type"] = edit.type
            if edit.description:
                txn["description"] = edit.description
            if edit.account:
                txn["account"] = edit.account
            if edit.category:
                txn["category"] = edit.category
        
        # Parse date
        txn_date = None
        if txn.get("date"):
            if isinstance(txn["date"], str):
                try:
                    txn_date = datetime.fromisoformat(txn["date"]).date()
                except:
                    txn_date = datetime.now().date()
            else:
                txn_date = txn["date"]
        else:
            txn_date = datetime.now().date()
        
        # Calculate signed amount
        amount = txn.get("amount", 0)
        if txn.get("type") == "debit":
            signed_amount = -abs(amount)
        else:
            signed_amount = abs(amount)
        
        # Check for duplicate
        existing = db.query(Transaction).filter(
            Transaction.user_id == user.id,
            Transaction.date == txn_date,
            Transaction.amount == signed_amount,
            Transaction.description == txn.get("description", "")
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
        # Save new transaction
        new_txn = Transaction(
            user_id=user.id,
            date=txn_date,
            amount=signed_amount,
            description=txn.get("description", ""),
            balance=txn.get("balance"),
            account=txn.get("account"),
            category=txn.get("category")
        )
        db.add(new_txn)
        saved += 1
    
    db.commit()
    
    return {
        "ok": True,
        "saved": saved,
        "duplicates": duplicates,
        "deleted": deleted,
        "message": f"Saved {saved} transactions" + (f", {duplicates} duplicates skipped" if duplicates else "") + (f", {deleted} removed" if deleted else "")
    }

@app.post("/import/debug")
async def import_data_debug(data: str = Body(..., embed=True), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Import with full debug output - doesn't save to DB"""
    from .services.ingest import auto_parse_data
    transactions, analysis, debug_log = auto_parse_data(data)
    return {
        "ok": True,
        "parsed_count": len(transactions),
        "needs_user_input": analysis.get("needs_user_input", False),
        "analysis": analysis,
        "transactions": [
            {
                "date": t["date"].isoformat() if t.get("date") else None,
                "amount": t.get("amount"),
                "signed_amount": t.get("signed_amount"),
                "type": t.get("type"),
                "description": t.get("description", "")[:50] if t.get("description") else "",
                "balance": t.get("balance")
            }
            for t in transactions[:20]
        ],
        "debug": debug_log
    }

@app.get("/onboarding/suggest-groups")
def suggest_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 3: Get suggested transaction groups"""
    suggestions = auto_categorize_transactions(db, user.id)
    return suggestions

@app.post("/onboarding/complete")
def complete_onboarding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark onboarding as complete"""
    user.onboarding_complete = True
    db.commit()
    return {"ok": True}

# Categories
@app.get("/categories")
def get_categories():
    """Get available categories for grouping"""
    return CATEGORIES

# Transaction Groups
@app.get("/groups")
def list_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all transaction groups"""
    return get_groups_for_user(db, user.id)

@app.post("/groups")
def create_transaction_group(req: CreateGroupRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a new transaction group"""
    group = create_group(
        db, user.id,
        name=req.name,
        description=req.description,
        category=req.category,
        stream_type=req.stream_type,
        frequency=req.frequency,
        transaction_ids=req.transaction_ids,
        allow_offsets=req.allow_offsets
    )
    return {
        "id": group.id,
        "name": group.name,
        "ok": True
    }

@app.get("/groups/{group_id}/transactions")
def get_group_txns(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get transactions in a group"""
    return get_group_transactions(db, user.id, group_id)

@app.post("/groups/move-transactions")
def move_txns(req: MoveTransactionsRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Move transactions to a different group"""
    move_transactions_to_group(
        db, user.id,
        transaction_ids=req.transaction_ids,
        target_group_id=req.target_group_id,
        mark_as_offset=req.mark_as_offset
    )
    return {"ok": True}

@app.delete("/groups/{group_id}")
def delete_group(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a group (transactions become unassigned)"""
    # Unassign transactions
    db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.group_id == group_id
    ).update({Transaction.group_id: None}, synchronize_session=False)
    
    # Delete group
    db.query(TransactionGroup).filter(
        TransactionGroup.id == group_id,
        TransactionGroup.user_id == user.id
    ).delete()
    
    db.commit()
    return {"ok": True}

# Trends
@app.get("/groups/{group_id}/trend")
def get_group_trend(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Analyze trend for a group"""
    return analyze_trends(db, user.id, group_id)

@app.post("/trends/sentiment")
def set_trend_sentiment(req: TrendSentimentRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Set user sentiment on a trend"""
    # Get current trend analysis
    trend = analyze_trends(db, user.id, req.group_id)
    
    # Upsert sentiment
    existing = db.query(TrendSentiment).filter(
        TrendSentiment.user_id == user.id,
        TrendSentiment.group_id == req.group_id
    ).first()
    
    if existing:
        existing.expected_direction = req.expected_direction
        existing.expected_change_pct = req.expected_change_pct
        existing.historical_trend = trend["trend"]
        existing.trend_pct_per_month = trend["pct_per_month"]
    else:
        sentiment = TrendSentiment(
            user_id=user.id,
            group_id=req.group_id,
            historical_trend=trend["trend"],
            trend_pct_per_month=trend["pct_per_month"],
            expected_direction=req.expected_direction,
            expected_change_pct=req.expected_change_pct
        )
        db.add(sentiment)
    
    db.commit()
    return {"ok": True}

# Schedule Rules
@app.get("/schedule")
def list_schedule_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all schedule rules"""
    rules = db.query(ScheduleRule).filter(ScheduleRule.user_id == user.id).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "group_id": r.group_id,
            "rule_type": r.rule_type,
            "rule_params": r.rule_params_json,
            "amount": float(r.amount),
            "priority": r.priority
        }
        for r in rules
    ]

@app.post("/schedule")
def create_schedule_rule(req: ScheduleRuleRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a schedule rule"""
    import json
    rule = ScheduleRule(
        user_id=user.id,
        name=req.name,
        group_id=req.group_id,
        rule_type=req.rule_type,
        rule_params_json=json.dumps(req.rule_params),
        amount=req.amount,
        priority=req.priority
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"id": rule.id, "ok": True}

# Forecast
@app.get("/forecast")
def get_forecast(
    horizon_days: int = Query(90, ge=1, le=365),
    starting_balance: Optional[float] = None,
    apply_sentiments: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get cash flow forecast"""
    # Enforce plan limits
    if user.plan != "pro" and horizon_days > 90:
        horizon_days = 90
    
    return compute_forecast(
        db, user.id,
        horizon_days=horizon_days,
        starting_balance=starting_balance,
        apply_sentiments=apply_sentiments
    )

@app.get("/summary")
def get_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Quick summary of current position"""
    # Use user's starting balance if set
    start_bal = float(user.starting_balance) if user.starting_balance else None
    forecast = compute_forecast(db, user.id, horizon_days=30, starting_balance=start_bal)
    
    if "error" in forecast:
        return {"error": forecast["error"]}
    
    # Return both calculated and adjusted projections
    baseline = forecast["summary"]["baseline"]
    adjusted = forecast["summary"].get("adjusted", baseline)
    
    return {
        "current_balance": forecast["starting_balance"],
        "low_point": baseline["low_point"],
        "high_point": baseline["high_point"],
        "monthly_profit": baseline["monthly_profit"],
        "monthly_profit_calculated": baseline["monthly_profit"],
        "monthly_profit_adjusted": adjusted.get("monthly_profit", baseline["monthly_profit"]),
        "status": "HEALTHY" if baseline["low_point"]["balance"] > 10000 else 
                  "TIGHT" if baseline["low_point"]["balance"] > 0 else "CRITICAL"
    }

# Transactions
@app.get("/transactions")
def list_transactions(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    group_id: Optional[int] = None,
    unassigned_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List transactions with pagination"""
    query = db.query(Transaction).filter(Transaction.user_id == user.id)
    
    if group_id:
        query = query.filter(Transaction.group_id == group_id)
    if unassigned_only:
        query = query.filter(Transaction.group_id == None)
    
    total = query.count()
    transactions = query.order_by(Transaction.date_posted.desc()).offset((page-1)*per_page).limit(per_page).all()
    
    return {
        "transactions": [
            {
                "id": t.id,
                "date": t.date_posted,
                "amount": float(t.amount_signed),
                "description": t.description,
                "group_id": t.group_id,
                "is_offset": t.is_offset
            }
            for t in transactions
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }

@app.delete("/transactions")
def delete_all_transactions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete all user data (reset)"""
    db.query(Transaction).filter(Transaction.user_id == user.id).delete()
    db.query(TransactionGroup).filter(TransactionGroup.user_id == user.id).delete()
    db.query(ScheduleRule).filter(ScheduleRule.user_id == user.id).delete()
    db.query(TrendSentiment).filter(TrendSentiment.user_id == user.id).delete()
    
    user.onboarding_step = 0
    user.onboarding_complete = False
    
    db.commit()
    return {"ok": True}

# Ask - Natural Language Q&A
class AskRequest(BaseModel):
    question: str

@app.post("/balance")
def update_balance(
    balance: float = Body(..., embed=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the user's starting balance"""
    user.starting_balance = balance
    db.commit()
    return {"success": True, "balance": balance}

@app.get("/ask")
async def ask_question_get(
    question: str = Query(..., description="Natural language question"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Answer a natural language question about cash flow"""
    from .services.llm import get_llm_service
    
    # Get forecast data for context - use user's starting balance
    start_bal = float(user.starting_balance) if user.starting_balance else None
    forecast = compute_forecast(db, user.id, horizon_days=90, starting_balance=start_bal)
    
    # Build data dict for LLM
    forecast_data = {
        "current_balance": forecast.get("starting_balance", 0),
        "status": "HEALTHY",
        "profit_30d": 0,
    }
    
    if "summary" in forecast:
        summary = forecast["summary"]["baseline"]
        forecast_data["low_point"] = {
            "amount": summary["low_point"]["balance"],
            "date": summary["low_point"]["date"],
        }
        forecast_data["high_point"] = {
            "amount": summary["high_point"]["balance"],
            "date": summary["high_point"]["date"],
        }
        forecast_data["profit_30d"] = summary.get("monthly_profit", 0)
        
        # Determine status
        low = summary["low_point"]["balance"]
        if low > 50000:
            forecast_data["status"] = "HEALTHY"
        elif low > 10000:
            forecast_data["status"] = "OK"
        elif low > 0:
            forecast_data["status"] = "TIGHT"
        else:
            forecast_data["status"] = "CRITICAL"
    
    # Get categories
    groups = get_groups_for_user(db, user.id)
    forecast_data["categories"] = {
        g["id"]: {
            "name": g["name"],
            "type": "credit" if g["stream_type"] == "inflow" else "debit",
            "total": g["total"],
            "net_total": g["net_total"],
        }
        for g in groups
    }
    
    # Get answer from LLM service
    llm = get_llm_service()
    result = await llm.answer(question, forecast_data)
    
    return result

@app.post("/ask")
async def ask_question_post(
    req: AskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Answer a natural language question about cash flow (POST)"""
    from .services.llm import get_llm_service
    
    # Get forecast data for context - use user's starting balance
    start_bal = float(user.starting_balance) if user.starting_balance else None
    forecast = compute_forecast(db, user.id, horizon_days=90, starting_balance=start_bal)
    
    # Build data dict for LLM
    forecast_data = {
        "current_balance": forecast.get("starting_balance", 0),
        "status": "HEALTHY",
        "profit_30d": 0,
    }
    
    if "summary" in forecast:
        summary = forecast["summary"]["baseline"]
        forecast_data["low_point"] = {
            "amount": summary["low_point"]["balance"],
            "date": summary["low_point"]["date"],
        }
        forecast_data["high_point"] = {
            "amount": summary["high_point"]["balance"],
            "date": summary["high_point"]["date"],
        }
        forecast_data["profit_30d"] = summary.get("monthly_profit", 0)
        
        # Determine status
        low = summary["low_point"]["balance"]
        if low > 50000:
            forecast_data["status"] = "HEALTHY"
        elif low > 10000:
            forecast_data["status"] = "OK"
        elif low > 0:
            forecast_data["status"] = "TIGHT"
        else:
            forecast_data["status"] = "CRITICAL"
    
    # Get categories
    groups = get_groups_for_user(db, user.id)
    forecast_data["categories"] = {
        g["id"]: {
            "name": g["name"],
            "type": "credit" if g["stream_type"] == "inflow" else "debit",
            "total": g["total"],
            "net_total": g["net_total"],
        }
        for g in groups
    }
    
    # Get answer from LLM service
    llm = get_llm_service()
    result = await llm.answer(req.question, forecast_data)
    
    return result

# QuickBooks Integration (placeholder)
@app.get("/integrations/quickbooks/connect")
def quickbooks_connect(user: User = Depends(get_current_user)):
    """Get QuickBooks OAuth URL"""
    # TODO: Implement QuickBooks OAuth
    client_id = os.environ.get("QUICKBOOKS_CLIENT_ID", "")
    redirect_uri = os.environ.get("QUICKBOOKS_REDIRECT_URI", "")
    
    if not client_id:
        return {"error": "QuickBooks integration not configured"}
    
    return {
        "oauth_url": f"https://appcenter.intuit.com/connect/oauth2?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope=com.intuit.quickbooks.accounting&state={user.id}"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
