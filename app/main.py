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
from .models import Base, User, Transaction, TransactionGroup, ScheduleRule, TrendSentiment, GroupCorrelation, CATEGORIES
from .auth import hash_pw, verify_pw, create_token, get_current_user
from .services.ingest import ingest_bank_csv, ingest_quickbooks_data
from .services.categorize import (
    auto_categorize_transactions, create_group, move_transactions_to_group,
    get_groups_for_user, get_group_transactions, update_group_stats
)
from .services.forecast import compute_forecast, analyze_trends

# Create tables
Base.metadata.create_all(bind=engine)

# Seed demo account with fake data
def seed_demo_account():
    from .db import SessionLocal
    import random
    
    db = SessionLocal()
    try:
        # Check if demo account exists
        demo = db.query(User).filter(User.email == "demo@projectr.app").first()
        if demo:
            return  # Already seeded
        
        # Create demo user
        demo = User(
            email="demo@projectr.app",
            password_hash=hash_pw("demo123"),
            company_name="Acme Coffee Co.",
            company_website="https://acmecoffee.com",
            primary_color="#6B4423",
            current_balance=47850.00
        )
        db.add(demo)
        db.commit()
        db.refresh(demo)
        
        # Create transaction groups
        groups_data = [
            {"name": "Daily Sales", "frequency": "daily", "direction": "inflow", "avg_amount": 2500},
            {"name": "Wholesale Orders", "frequency": "weekly", "direction": "inflow", "avg_amount": 8500},
            {"name": "Payroll", "frequency": "semi-monthly", "direction": "outflow", "avg_amount": 12000},
            {"name": "Rent", "frequency": "monthly", "direction": "outflow", "avg_amount": 4500},
            {"name": "Inventory/COGS", "frequency": "weekly", "direction": "outflow", "avg_amount": 3200},
            {"name": "Utilities", "frequency": "monthly", "direction": "outflow", "avg_amount": 850},
            {"name": "Marketing", "frequency": "weekly", "direction": "outflow", "avg_amount": 600},
        ]
        
        groups = {}
        for g in groups_data:
            grp = TransactionGroup(
                user_id=demo.id,
                name=g["name"],
                frequency=g["frequency"],
                direction=g["direction"],
                avg_amount=g["avg_amount"]
            )
            db.add(grp)
            db.commit()
            db.refresh(grp)
            groups[g["name"]] = grp
        
        # Generate 90 days of fake transactions
        today = date.today()
        transactions = []
        
        for days_ago in range(90, -1, -1):
            txn_date = today - timedelta(days=days_ago)
            weekday = txn_date.weekday()
            
            # Daily sales (Mon-Sat)
            if weekday < 6:
                amt = round(random.uniform(2000, 3200), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"Square Deposit - Daily Sales",
                    group_id=groups["Daily Sales"].id,
                    is_credit=True
                ))
            
            # Wholesale orders (Wednesdays)
            if weekday == 2:
                amt = round(random.uniform(7000, 10500), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"ACH - Whole Foods Distribution",
                    group_id=groups["Wholesale Orders"].id,
                    is_credit=True
                ))
            
            # Inventory (Mondays)
            if weekday == 0:
                amt = round(random.uniform(2800, 3800), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"Check - Bean Suppliers Inc",
                    group_id=groups["Inventory/COGS"].id,
                    is_credit=False
                ))
            
            # Marketing (Fridays)
            if weekday == 4:
                amt = round(random.uniform(400, 900), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"ACH - Meta Ads",
                    group_id=groups["Marketing"].id,
                    is_credit=False
                ))
            
            # Payroll (1st and 15th)
            if txn_date.day in [1, 15]:
                amt = round(random.uniform(11000, 13500), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"ADP Payroll",
                    group_id=groups["Payroll"].id,
                    is_credit=False
                ))
            
            # Rent (1st of month)
            if txn_date.day == 1:
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=4500.00,
                    description=f"Check - Landlord LLC",
                    group_id=groups["Rent"].id,
                    is_credit=False
                ))
            
            # Utilities (15th)
            if txn_date.day == 15:
                amt = round(random.uniform(750, 980), 2)
                transactions.append(Transaction(
                    user_id=demo.id,
                    date=txn_date,
                    amount=amt,
                    description=f"ACH - Pacific Gas & Electric",
                    group_id=groups["Utilities"].id,
                    is_credit=False
                ))
        
        db.add_all(transactions)
        db.commit()
        
        # Update group stats
        for grp in groups.values():
            update_group_stats(db, grp.id)
        
        print(f"Demo account seeded: demo@projectr.app / demo123")
    except Exception as e:
        print(f"Demo seed error: {e}")
        db.rollback()
    finally:
        db.close()

# Run seed on startup
seed_demo_account()

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
# Business type keywords and their associated categories
BUSINESS_TYPE_KEYWORDS = {
    "travel": {
        "keywords": ["travel", "vacation", "resort", "timeshare", "hotel", "booking", "destination", "tour", "cruise", "flight", "trip"],
        "revenues": ["Package Sales", "Commission Revenue", "Booking Fees", "Referral Income", "Membership Fees"],
        "expenses": ["Marketing & Advertising", "Commissions Paid", "Payment Processing Fees", "Travel Agent Fees", "Chargebacks & Refunds", "Software & Subscriptions"],
    },
    "restaurant": {
        "keywords": ["restaurant", "food", "dining", "menu", "cuisine", "chef", "catering", "bistro", "cafe", "pizza", "delivery"],
        "revenues": ["Food Sales", "Beverage Sales", "Catering Revenue", "Delivery Fees", "Tips"],
        "expenses": ["Food Costs (COGS)", "Labor & Wages", "Rent", "Utilities", "Kitchen Equipment", "Supplies", "Delivery Costs"],
    },
    "retail": {
        "keywords": ["shop", "store", "buy", "product", "merchandise", "retail", "ecommerce", "cart", "shipping", "order"],
        "revenues": ["Product Sales", "Shipping Revenue", "Gift Cards", "Wholesale Revenue"],
        "expenses": ["Cost of Goods Sold", "Shipping & Fulfillment", "Inventory", "Payment Processing", "Marketing", "Rent", "Packaging"],
    },
    "saas": {
        "keywords": ["software", "platform", "app", "subscription", "saas", "cloud", "api", "dashboard", "enterprise", "solution"],
        "revenues": ["Subscription Revenue", "Annual Contracts", "Professional Services", "API Usage Fees", "Onboarding Fees"],
        "expenses": ["Hosting & Infrastructure", "Engineering Salaries", "Customer Support", "Marketing", "Software Licenses", "Sales Commissions"],
    },
    "healthcare": {
        "keywords": ["health", "medical", "doctor", "clinic", "patient", "care", "therapy", "dental", "wellness", "treatment"],
        "revenues": ["Patient Services", "Insurance Reimbursements", "Cash Pay Services", "Lab Fees", "Consultation Fees"],
        "expenses": ["Staff Salaries", "Medical Supplies", "Insurance", "Rent", "Equipment Leases", "Billing Services", "Compliance Costs"],
    },
    "construction": {
        "keywords": ["construction", "build", "contractor", "remodel", "home", "project", "renovation", "plumbing", "electrical", "roofing"],
        "revenues": ["Contract Revenue", "Change Orders", "Service Calls", "Material Markup"],
        "expenses": ["Labor Costs", "Materials & Supplies", "Equipment Rental", "Subcontractors", "Insurance", "Permits & Licenses", "Vehicle Costs"],
    },
    "professional_services": {
        "keywords": ["consulting", "legal", "accounting", "law", "attorney", "cpa", "advisory", "firm", "counsel", "tax"],
        "revenues": ["Billable Hours", "Retainer Fees", "Project Fees", "Consulting Revenue"],
        "expenses": ["Staff Salaries", "Professional Insurance", "Office Rent", "Marketing", "Professional Memberships", "Software & Tools"],
    },
    "fitness": {
        "keywords": ["gym", "fitness", "workout", "training", "yoga", "pilates", "crossfit", "personal trainer", "membership"],
        "revenues": ["Membership Dues", "Personal Training", "Class Fees", "Merchandise Sales", "Supplements"],
        "expenses": ["Rent", "Equipment", "Trainer Wages", "Utilities", "Insurance", "Marketing", "Cleaning & Maintenance"],
    },
    "real_estate": {
        "keywords": ["real estate", "property", "homes", "realtor", "broker", "listing", "mortgage", "rental", "apartment"],
        "revenues": ["Commission Income", "Property Management Fees", "Rental Income", "Referral Fees"],
        "expenses": ["Marketing & Advertising", "MLS Fees", "Office Rent", "Agent Splits", "Insurance", "Vehicle Costs", "Staging Costs"],
    },
    "manufacturing": {
        "keywords": ["manufacturing", "factory", "production", "assembly", "industrial", "machinery", "warehouse", "supply chain"],
        "revenues": ["Product Sales", "Contract Manufacturing", "Custom Orders", "Scrap Sales"],
        "expenses": ["Raw Materials", "Direct Labor", "Equipment Maintenance", "Utilities", "Shipping & Logistics", "Quality Control", "Factory Rent"],
    },
}

# Default categories for unknown business types
DEFAULT_CATEGORIES = {
    "revenues": ["Sales Revenue", "Service Revenue", "Other Income", "Interest Income"],
    "expenses": ["Payroll", "Rent", "Utilities", "Insurance", "Marketing", "Office Supplies", "Professional Services", "Software & Subscriptions"],
}

def detect_business_type(text: str) -> dict:
    """Detect business type from website text and return suggested categories"""
    text_lower = text.lower()
    
    best_match = None
    best_score = 0
    
    for biz_type, config in BUSINESS_TYPE_KEYWORDS.items():
        score = sum(1 for kw in config["keywords"] if kw in text_lower)
        if score > best_score:
            best_score = score
            best_match = biz_type
    
    if best_match and best_score >= 2:  # Need at least 2 keyword matches
        config = BUSINESS_TYPE_KEYWORDS[best_match]
        return {
            "business_type": best_match.replace("_", " ").title(),
            "confidence": min(best_score * 15, 95),  # Cap at 95%
            "suggested_revenues": config["revenues"],
            "suggested_expenses": config["expenses"],
        }
    
    return {
        "business_type": "General Business",
        "confidence": 30,
        "suggested_revenues": DEFAULT_CATEGORIES["revenues"],
        "suggested_expenses": DEFAULT_CATEGORIES["expenses"],
    }

async def fetch_branding_from_website(website: str) -> dict:
    """Fetch logo, colors, and business info from a website by scraping HTML"""
    import re
    result = {
        "logo_url": None, 
        "primary_color": None,
        "business_type": None,
        "business_description": None,
        "suggested_revenues": [],
        "suggested_expenses": [],
    }
    
    # Clean up website URL
    website = website.strip().lower()
    if website.startswith("http://"):
        website = website[7:]
    if website.startswith("https://"):
        website = website[8:]
    if website.startswith("www."):
        website = website[4:]
    website = website.rstrip("/")
    
    base_url = f"https://{website}"
    
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            # Fetch the homepage HTML
            r = await client.get(base_url, headers={"User-Agent": "Mozilla/5.0 (compatible; CashFlowBot/1.0)"})
            if r.status_code != 200:
                return result
            
            html = r.text
            
            # Extract text content for business analysis
            # Remove scripts and styles
            text_html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text_html = re.sub(r'<style[^>]*>.*?</style>', '', text_html, flags=re.DOTALL | re.IGNORECASE)
            # Extract text from remaining HTML
            text_content = re.sub(r'<[^>]+>', ' ', text_html)
            text_content = re.sub(r'\s+', ' ', text_content).strip()
            
            # Get page title and meta description
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else ""
            
            desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not desc_match:
                desc_match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']', html, re.IGNORECASE)
            description = desc_match.group(1).strip() if desc_match else ""
            
            # OG description as fallback
            if not description:
                og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if og_desc:
                    description = og_desc.group(1).strip()
            
            # Combine text for analysis
            analysis_text = f"{title} {description} {text_content[:3000]}"
            
            # Detect business type and get suggestions
            biz_info = detect_business_type(analysis_text)
            result["business_type"] = biz_info["business_type"]
            result["business_confidence"] = biz_info["confidence"]
            result["suggested_revenues"] = biz_info["suggested_revenues"]
            result["suggested_expenses"] = biz_info["suggested_expenses"]
            result["business_description"] = description[:200] if description else title[:100] if title else None
            
            # Look for logo in various places (priority order)
            logo_patterns = [
                # Apple touch icon (high quality)
                r'<link[^>]+rel=["\']apple-touch-icon["\'][^>]+href=["\']([^"\']+)["\']',
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']apple-touch-icon["\']',
                # OG image (often a good logo)
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                # Large favicon
                r'<link[^>]+rel=["\']icon["\'][^>]+sizes=["\'](?:192|180|152|144|128|120|114|96|72)[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
                # Any icon link
                r'<link[^>]+rel=["\'](?:shortcut )?icon["\'][^>]+href=["\']([^"\']+)["\']',
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:shortcut )?icon["\']',
                # Logo in img tags
                r'<img[^>]+(?:class|id)=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                r'<img[^>]+src=["\']([^"\']+)["\'][^>]+(?:class|id)=["\'][^"\']*logo[^"\']*["\']',
            ]
            
            for pattern in logo_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    logo_url = match.group(1)
                    # Make absolute URL
                    if logo_url.startswith("//"):
                        logo_url = "https:" + logo_url
                    elif logo_url.startswith("/"):
                        logo_url = base_url + logo_url
                    elif not logo_url.startswith("http"):
                        logo_url = base_url + "/" + logo_url
                    result["logo_url"] = logo_url
                    break
            
            # If no logo found, try common paths
            if not result["logo_url"]:
                for path in ["/apple-touch-icon.png", "/favicon-192x192.png", "/logo.png", "/favicon.ico"]:
                    try:
                        r2 = await client.head(base_url + path)
                        if r2.status_code == 200:
                            result["logo_url"] = base_url + path
                            break
                    except:
                        continue
            
            # Look for theme color
            theme_match = re.search(r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not theme_match:
                theme_match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']theme-color["\']', html, re.IGNORECASE)
            if theme_match:
                result["primary_color"] = theme_match.group(1)
            
    except Exception as e:
        print(f"Error fetching branding: {e}")
    
    return result

@app.post("/onboarding/company")
async def set_company_info(req: CompanyInfoRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 1: Set company info, optionally fetch logo/colors from website"""
    user.company_name = req.company_name
    
    fetched_branding = {}
    if req.company_website:
        user.company_website = req.company_website
        
        # Try to fetch branding from website
        if not req.logo_url or not req.primary_color:
            fetched_branding = await fetch_branding_from_website(req.company_website)
    
    # Apply colors - user provided takes priority
    if req.primary_color:
        user.primary_color = req.primary_color
    elif fetched_branding.get("primary_color"):
        user.primary_color = fetched_branding["primary_color"]
        
    if req.secondary_color:
        user.secondary_color = req.secondary_color
    
    # Apply logo - user provided takes priority
    if req.logo_url:
        user.logo_url = req.logo_url
    elif fetched_branding.get("logo_url"):
        user.logo_url = fetched_branding["logo_url"]
    
    user.onboarding_step = max(user.onboarding_step, 1)
    db.commit()
    
    return {
        "ok": True, 
        "step": 1,
        "fetched_logo": fetched_branding.get("logo_url"),
        "fetched_color": fetched_branding.get("primary_color"),
        "applied_logo": user.logo_url,
        "applied_color": user.primary_color,
        "business_type": fetched_branding.get("business_type"),
        "business_confidence": fetched_branding.get("business_confidence"),
        "business_description": fetched_branding.get("business_description"),
        "suggested_revenues": fetched_branding.get("suggested_revenues", []),
        "suggested_expenses": fetched_branding.get("suggested_expenses", []),
    }

@app.get("/fetch-branding")
async def preview_branding(website: str = Query(..., description="Website to fetch branding from")):
    """Preview logo, colors, and business info from a website without saving"""
    branding = await fetch_branding_from_website(website)
    return {
        "website": website,
        "logo_url": branding.get("logo_url"),
        "primary_color": branding.get("primary_color"),
        "business_type": branding.get("business_type"),
        "business_confidence": branding.get("business_confidence"),
        "business_description": branding.get("business_description"),
        "suggested_revenues": branding.get("suggested_revenues", []),
        "suggested_expenses": branding.get("suggested_expenses", []),
    }

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
            Transaction.date_posted == txn_date.strftime("%Y-%m-%d") if hasattr(txn_date, 'strftime') else str(txn_date),
            Transaction.amount_signed == signed_amount,
            Transaction.description == txn.get("description", "")
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
        # Save new transaction
        new_txn = Transaction(
            user_id=user.id,
            date_posted=txn_date.strftime("%Y-%m-%d") if hasattr(txn_date, 'strftime') else str(txn_date),
            amount_signed=signed_amount,
            description=txn.get("description", ""),
            balance=txn.get("balance")
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

@app.get("/groups/{group_id}/trend-detail")
def get_group_trend_detail(
    group_id: int, 
    period: str = Query("weekly", regex="^(weekly|monthly)$"),
    user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """Get transaction totals by week or month for trend analysis"""
    from datetime import timedelta
    from collections import defaultdict
    
    # Get the group
    group = db.query(TransactionGroup).filter(
        TransactionGroup.id == group_id,
        TransactionGroup.user_id == user.id
    ).first()
    if not group:
        raise HTTPException(404, "Group not found")
    
    # Get transactions for this group
    txns = db.query(Transaction).filter(
        Transaction.user_id == user.id,
        Transaction.group_id == group_id
    ).order_by(Transaction.date_posted.desc()).all()
    
    if not txns:
        return {
            "group": {
                "id": group.id,
                "name": group.name,
                "trend": group.trend,
                "calculated_trend_percent": float(group.calculated_trend_percent) if group.calculated_trend_percent else 0,
                "adjusted_trend_percent": float(group.adjusted_trend_percent) if group.adjusted_trend_percent else None
            },
            "periods": [],
            "period_type": period
        }
    
    # Group by week or month
    period_totals = defaultdict(lambda: {"total": 0, "count": 0, "transactions": []})
    
    for t in txns:
        # Parse the date string (YYYY-MM-DD format)
        if not t.date_posted:
            continue  # Skip transactions without dates
        try:
            txn_date = datetime.strptime(t.date_posted, "%Y-%m-%d").date()
        except:
            continue
        if period == "weekly":
            # Start of week (Monday)
            start_of_week = txn_date - timedelta(days=txn_date.weekday())
            period_key = start_of_week.strftime("%Y-%m-%d")
        else:  # monthly
            period_key = txn_date.strftime("%Y-%m")
        
        period_totals[period_key]["total"] += float(t.amount_signed)
        period_totals[period_key]["count"] += 1
        period_totals[period_key]["transactions"].append({
            "id": t.id,
            "date": t.date_posted,
            "description": t.description,
            "amount": float(t.amount_signed)
        })
    
    # Convert to list sorted by period
    periods = []
    for period_key in sorted(period_totals.keys(), reverse=True):
        data = period_totals[period_key]
        periods.append({
            "period": period_key,
            "total": round(data["total"], 2),
            "count": data["count"],
            "transactions": data["transactions"]
        })
    
    # Calculate trend from data
    calc_trend_pct = 0
    if len(periods) >= 2:
        # Compare most recent to previous
        recent_avg = periods[0]["total"]
        older_avg = periods[1]["total"]
        if older_avg != 0:
            calc_trend_pct = round((recent_avg - older_avg) / abs(older_avg) * 100, 1)
    
    return {
        "group": {
            "id": group.id,
            "name": group.name,
            "trend": group.trend or "flat",
            "calculated_trend_percent": calc_trend_pct,
            "adjusted_trend_percent": float(group.adjusted_trend_percent) if group.adjusted_trend_percent else None,
            "trend_period": group.trend_period or "month",
            "trend_duration_days": group.trend_duration_days,
            "trend_then": group.trend_then,
            "trend_then_percent": float(group.trend_then_percent) if group.trend_then_percent else None
        },
        "periods": periods,
        "period_type": period
    }

class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    stream_type: Optional[str] = None
    frequency: Optional[str] = None
    trend: Optional[str] = None  # 'up', 'down', 'flat'
    # New trend adjustment fields
    adjusted_trend_percent: Optional[float] = None
    trend_period: Optional[str] = None  # day|week|month
    trend_duration_days: Optional[int] = None
    trend_then: Optional[str] = None  # flat|up|down
    trend_then_percent: Optional[float] = None

@app.patch("/groups/{group_id}")
def update_group(group_id: int, req: UpdateGroupRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update group properties"""
    group = db.query(TransactionGroup).filter(
        TransactionGroup.id == group_id,
        TransactionGroup.user_id == user.id
    ).first()
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if req.name is not None:
        group.name = req.name
    if req.description is not None:
        group.description = req.description
    if req.stream_type is not None:
        group.stream_type = req.stream_type
    if req.frequency is not None:
        group.frequency = req.frequency
    if req.trend is not None:
        group.trend = req.trend
    # New trend fields
    if req.adjusted_trend_percent is not None:
        group.adjusted_trend_percent = req.adjusted_trend_percent
    if req.trend_period is not None:
        group.trend_period = req.trend_period
    if req.trend_duration_days is not None:
        group.trend_duration_days = req.trend_duration_days
    if req.trend_then is not None:
        group.trend_then = req.trend_then
    if req.trend_then_percent is not None:
        group.trend_then_percent = req.trend_then_percent
    
    db.commit()
    return {"ok": True, "id": group.id}

@app.post("/groups/{group_id}/calculate-trend")
def calculate_group_trend(group_id: int, lookback_days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Calculate trend for a group based on custom lookback period"""
    group = db.query(TransactionGroup).filter(
        TransactionGroup.id == group_id,
        TransactionGroup.user_id == user.id
    ).first()
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Get transactions within the lookback period
    from datetime import datetime, timedelta
    cutoff_date = datetime.now().date() - timedelta(days=lookback_days)
    
    transactions = db.query(Transaction).filter(
        Transaction.group_id == group_id,
        Transaction.user_id == user.id,
        Transaction.date >= cutoff_date
    ).order_by(Transaction.date).all()
    
    if len(transactions) < 2:
        # Not enough data to calculate trend
        return {"calculated_trend_percent": 0, "lookback_days": lookback_days, "transaction_count": len(transactions)}
    
    # Split into two halves and compare
    midpoint = len(transactions) // 2
    first_half = transactions[:midpoint]
    second_half = transactions[midpoint:]
    
    # Sum amounts for each half (inflows positive, outflows negative)
    first_sum = sum(t.amount for t in first_half) if first_half else 0
    second_sum = sum(t.amount for t in second_half) if second_half else 0
    
    # Calculate percentage change
    if first_sum == 0:
        trend_pct = 0
    else:
        trend_pct = ((second_sum - first_sum) / abs(first_sum)) * 100
    
    # Normalize to per-month rate (30 days)
    half_period_days = lookback_days / 2
    if half_period_days > 0:
        trend_per_month = trend_pct * (30 / half_period_days)
    else:
        trend_per_month = 0
    
    # Round to 1 decimal
    trend_per_month = round(trend_per_month, 1)
    
    # Optionally update the calculated_trend_percent in the database
    group.calculated_trend_percent = trend_per_month
    db.commit()
    
    return {
        "calculated_trend_percent": trend_per_month,
        "lookback_days": lookback_days,
        "transaction_count": len(transactions),
        "first_half_sum": first_sum,
        "second_half_sum": second_sum
    }

class BatchMoveRequest(BaseModel):
    transaction_ids: List[int]
    group_id: Optional[int] = None

@app.post("/transactions/batch-move")
def batch_move_transactions(req: BatchMoveRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Move multiple transactions to a category"""
    # Verify group belongs to user if specified
    if req.group_id is not None:
        group = db.query(TransactionGroup).filter(
            TransactionGroup.id == req.group_id,
            TransactionGroup.user_id == user.id
        ).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
    
    # Update transactions
    updated = db.query(Transaction).filter(
        Transaction.id.in_(req.transaction_ids),
        Transaction.user_id == user.id
    ).update({Transaction.group_id: req.group_id}, synchronize_session=False)
    
    db.commit()
    return {"ok": True, "updated": updated}

class BatchDeleteRequest(BaseModel):
    transaction_ids: List[int]

@app.post("/transactions/batch-delete")
def batch_delete_transactions(req: BatchDeleteRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete multiple transactions"""
    deleted = db.query(Transaction).filter(
        Transaction.id.in_(req.transaction_ids),
        Transaction.user_id == user.id
    ).delete(synchronize_session=False)
    
    db.commit()
    return {"ok": True, "deleted": deleted}

# ============ CORRELATIONS ============

class CreateCorrelationRequest(BaseModel):
    source_group_id: int
    target_group_id: int
    direction: str  # 'same' or 'opposite'
    percent: float  # e.g., 50 means 50% of source change
    delay_value: int = 0
    delay_unit: str = "days"  # days|weeks|months

@app.get("/groups/{group_id}/correlations")
def get_group_correlations(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get correlations where this group is the source (trigger)"""
    correlations = db.query(GroupCorrelation).filter(
        GroupCorrelation.source_group_id == group_id,
        GroupCorrelation.user_id == user.id
    ).all()
    
    # Get target group names
    result = []
    for c in correlations:
        target_group = db.query(TransactionGroup).filter(TransactionGroup.id == c.target_group_id).first()
        result.append({
            "id": c.id,
            "target_group_id": c.target_group_id,
            "target_group_name": target_group.name if target_group else "Unknown",
            "direction": c.direction,
            "percent": float(c.percent),
            "delay_value": c.delay_value,
            "delay_unit": c.delay_unit
        })
    
    return result

@app.post("/correlations")
def create_correlation(req: CreateCorrelationRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a correlation between groups"""
    # Verify both groups belong to user
    source = db.query(TransactionGroup).filter(
        TransactionGroup.id == req.source_group_id,
        TransactionGroup.user_id == user.id
    ).first()
    target = db.query(TransactionGroup).filter(
        TransactionGroup.id == req.target_group_id,
        TransactionGroup.user_id == user.id
    ).first()
    
    if not source or not target:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if req.source_group_id == req.target_group_id:
        raise HTTPException(status_code=400, detail="Cannot correlate a group with itself")
    
    # Check for existing
    existing = db.query(GroupCorrelation).filter(
        GroupCorrelation.source_group_id == req.source_group_id,
        GroupCorrelation.target_group_id == req.target_group_id,
        GroupCorrelation.user_id == user.id
    ).first()
    
    if existing:
        # Update existing
        existing.direction = req.direction
        existing.percent = req.percent
        existing.delay_value = req.delay_value
        existing.delay_unit = req.delay_unit
    else:
        # Create new
        corr = GroupCorrelation(
            user_id=user.id,
            source_group_id=req.source_group_id,
            target_group_id=req.target_group_id,
            direction=req.direction,
            percent=req.percent,
            delay_value=req.delay_value,
            delay_unit=req.delay_unit
        )
        db.add(corr)
    
    db.commit()
    return {"ok": True}

@app.delete("/correlations/{correlation_id}")
def delete_correlation(correlation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a correlation"""
    deleted = db.query(GroupCorrelation).filter(
        GroupCorrelation.id == correlation_id,
        GroupCorrelation.user_id == user.id
    ).delete()
    
    db.commit()
    return {"ok": True, "deleted": deleted}

# Get all correlations for forecast calculations
@app.get("/correlations")
def get_all_correlations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all user correlations"""
    correlations = db.query(GroupCorrelation).filter(
        GroupCorrelation.user_id == user.id
    ).all()
    
    result = []
    for c in correlations:
        source = db.query(TransactionGroup).filter(TransactionGroup.id == c.source_group_id).first()
        target = db.query(TransactionGroup).filter(TransactionGroup.id == c.target_group_id).first()
        result.append({
            "id": c.id,
            "source_group_id": c.source_group_id,
            "source_group_name": source.name if source else "Unknown",
            "target_group_id": c.target_group_id,
            "target_group_name": target.name if target else "Unknown",
            "direction": c.direction,
            "percent": float(c.percent),
            "delay_value": c.delay_value,
            "delay_unit": c.delay_unit
        })
    
    return result

# ============ TREND ANALYSIS ============

@app.get("/groups/{group_id}/trend-detail")
def get_group_trend_detail(group_id: int, period: str = "week", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get detailed trend analysis for a group with transactions by week or month"""
    from sqlalchemy import func
    
    group = db.query(TransactionGroup).filter(
        TransactionGroup.id == group_id,
        TransactionGroup.user_id == user.id
    ).first()
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Get transactions for this group
    txns = db.query(Transaction).filter(
        Transaction.group_id == group_id,
        Transaction.user_id == user.id
    ).order_by(Transaction.date_posted.desc()).all()
    
    if not txns:
        return {
            "group": {
                "id": group.id,
                "name": group.name,
                "stream_type": group.stream_type,
                "frequency": group.frequency,
                "calculated_trend_percent": 0,
                "adjusted_trend_percent": None,
                "trend_period": group.trend_period,
                "trend_duration_days": group.trend_duration_days,
                "trend_then": group.trend_then,
                "trend_then_percent": None
            },
            "periods": [],
            "trend_direction": "flat",
            "trend_percent": 0
        }
    
    # Group by period
    from collections import defaultdict
    periods = defaultdict(lambda: {"total": 0, "count": 0, "transactions": []})
    
    for t in txns:
        if not t.date_posted:
            continue
        try:
            d = datetime.strptime(t.date_posted, "%Y-%m-%d").date()
        except:
            continue
        if period == "week":
            # Get ISO week
            year, week, _ = d.isocalendar()
            key = f"{year}-W{week:02d}"
        else:  # month
            key = f"{d.year}-{d.month:02d}"
        
        periods[key]["total"] += float(t.amount_signed)
        periods[key]["count"] += 1
        periods[key]["transactions"].append({
            "id": t.id,
            "date": t.date_posted,
            "description": t.description,
            "amount": float(t.amount_signed)
        })
    
    # Sort periods
    sorted_periods = sorted(periods.items(), reverse=True)
    period_list = []
    for key, data in sorted_periods[:12]:  # Last 12 periods
        period_list.append({
            "period": key,
            "total": round(data["total"], 2),
            "count": data["count"],
            "transactions": data["transactions"]
        })
    
    # Calculate trend from periods
    if len(period_list) >= 2:
        recent_total = sum(p["total"] for p in period_list[:3]) / min(3, len(period_list))
        older_total = sum(p["total"] for p in period_list[3:6]) / max(1, min(3, len(period_list) - 3))
        
        if older_total != 0:
            change_percent = ((recent_total - older_total) / abs(older_total)) * 100
        else:
            change_percent = 0
        
        if change_percent > 5:
            trend_direction = "up"
        elif change_percent < -5:
            trend_direction = "down"
        else:
            trend_direction = "flat"
    else:
        change_percent = 0
        trend_direction = "flat"
    
    # Update calculated trend in DB
    group.calculated_trend_percent = change_percent
    db.commit()
    
    return {
        "group": {
            "id": group.id,
            "name": group.name,
            "stream_type": group.stream_type,
            "frequency": group.frequency,
            "calculated_trend_percent": round(change_percent, 1),
            "adjusted_trend_percent": float(group.adjusted_trend_percent) if group.adjusted_trend_percent else None,
            "trend_period": group.trend_period,
            "trend_duration_days": group.trend_duration_days,
            "trend_then": group.trend_then,
            "trend_then_percent": float(group.trend_then_percent) if group.trend_then_percent else None
        },
        "periods": period_list,
        "trend_direction": trend_direction,
        "trend_percent": round(change_percent, 1)
    }

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
            "total": g.get("net_amount", 0),
            "net_total": g.get("net_amount", 0),
            "trend": g.get("trend", "flat"),
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
            "total": g.get("net_amount", 0),
            "net_total": g.get("net_amount", 0),
            "trend": g.get("trend", "flat"),
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
