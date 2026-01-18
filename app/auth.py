"""Authentication"""
import os
import hashlib
import secrets
from datetime import datetime, timedelta
from fastapi import HTTPException, Depends, Header
from sqlalchemy.orm import Session
from .db import get_db
from .models import User

SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

def hash_pw(password: str) -> str:
    """Hash password with salt"""
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}:{hashed.hex()}"

def verify_pw(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    try:
        salt, hashed = password_hash.split(":")
        new_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return new_hash.hex() == hashed
    except:
        return False

def create_token(user_id: int) -> str:
    """Create simple JWT-like token"""
    import base64
    import json
    import hmac
    
    payload = {
        "user_id": user_id,
        "exp": (datetime.utcnow() + timedelta(days=30)).isoformat()
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    signature = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), 'sha256').hexdigest()
    return f"{payload_b64}.{signature}"

def decode_token(token: str) -> dict:
    """Decode and verify token"""
    import base64
    import json
    import hmac
    
    try:
        payload_b64, signature = token.split(".")
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), 'sha256').hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid token")
        
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Check expiration
        exp = datetime.fromisoformat(payload["exp"])
        if datetime.utcnow() > exp:
            raise HTTPException(status_code=401, detail="Token expired")
        
        return payload
    except HTTPException:
        raise
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    """Get current user from token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Handle "Bearer <token>" format
    token = authorization
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    
    payload = decode_token(token)
    user = db.query(User).filter(User.id == payload["user_id"]).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user
