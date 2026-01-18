"""
PROJECT-R API
Multi-tenant cash flow forecasting
"""
import os
import httpx
from datetime import date
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, Body
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
def signup(req: SignupRequest, db: Session = Depends(get_db)):
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

@app.post("/import/debug")
async def import_data_debug(data: str = Body(..., embed=True), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Import with full debug output - doesn't save to DB"""
    from .services.ingest import parse_web_pasted_data
    transactions, debug_log = parse_web_pasted_data(data)
    return {
        "ok": True,
        "parsed_count": len(transactions),
        "transactions": [
            {
                "date": t["date"].isoformat() if t.get("date") else None,
                "amount": t["amount"],
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
    forecast = compute_forecast(db, user.id, horizon_days=30)
    
    if "error" in forecast:
        return {"error": forecast["error"]}
    
    return {
        "current_balance": forecast["starting_balance"],
        "low_point": forecast["summary"]["baseline"]["low_point"],
        "high_point": forecast["summary"]["baseline"]["high_point"],
        "monthly_profit": forecast["summary"]["baseline"]["monthly_profit"],
        "status": "HEALTHY" if forecast["summary"]["baseline"]["low_point"]["balance"] > 10000 else 
                  "TIGHT" if forecast["summary"]["baseline"]["low_point"]["balance"] > 0 else "CRITICAL"
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

@app.get("/ask")
async def ask_question_get(
    question: str = Query(..., description="Natural language question"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Answer a natural language question about cash flow"""
    from .services.llm import get_llm_service
    
    # Get forecast data for context
    forecast = compute_forecast(db, user.id, horizon_days=90)
    
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
    
    # Get forecast data for context
    forecast = compute_forecast(db, user.id, horizon_days=90)
    
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
