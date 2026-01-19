"""
Transaction Categorization Service
Groups similar transactions and allows user categorization with offset support
"""
import re
import statistics
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models import Transaction, TransactionGroup, CATEGORIES

def normalize_description(desc: str) -> str:
    """Normalize description for grouping"""
    # Remove numbers, dates, reference IDs
    normalized = re.sub(r'\d+', '', desc)
    normalized = re.sub(r'[#*]', '', normalized)
    normalized = normalized.upper().strip()
    # Remove extra whitespace
    normalized = ' '.join(normalized.split())
    return normalized[:100]  # Limit length

def calculate_frequency(dates: List[date]) -> str:
    """Determine frequency from list of dates"""
    if len(dates) < 2:
        return "uncommon"
    
    dates = sorted(dates)
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    avg_gap = statistics.mean(gaps) if gaps else 30
    
    if avg_gap <= 2:
        return "daily"
    elif avg_gap <= 9:
        return "weekly"
    elif avg_gap <= 18:
        return "semimonthly"
    elif avg_gap <= 35:
        return "monthly"
    else:
        return "uncommon"

def get_typical_timing(dates: List[date]) -> Tuple[Optional[int], Optional[int]]:
    """Get typical day of month and day of week"""
    if not dates:
        return None, None
    
    days_of_month = [d.day for d in dates]
    days_of_week = [d.weekday() for d in dates]
    
    typical_dom = None
    typical_dow = None
    
    try:
        typical_dom = statistics.mode(days_of_month)
    except:
        if days_of_month:
            typical_dom = int(statistics.mean(days_of_month))
    
    try:
        typical_dow = statistics.mode(days_of_week)
    except:
        pass
    
    return typical_dom, typical_dow

def auto_categorize_transactions(db: Session, user_id: int) -> Dict:
    """
    Automatically group similar transactions.
    Returns suggested groups for user review.
    """
    # Get all uncategorized transactions
    transactions = db.query(Transaction).filter(
        Transaction.user_id == user_id,
        Transaction.group_id == None
    ).order_by(Transaction.date_posted.desc()).all()
    
    if not transactions:
        return {"groups": [], "unassigned_count": 0}
    
    # Group by normalized description
    desc_groups: Dict[str, List[Transaction]] = defaultdict(list)
    
    for txn in transactions:
        key = normalize_description(txn.description)
        if key:  # Only group non-empty descriptions
            desc_groups[key].append(txn)
        else:
            desc_groups["_UNKNOWN_"].append(txn)
    
    # Create suggested groups
    suggested_groups = []
    
    for desc_key, txns in desc_groups.items():
        if desc_key == "_UNKNOWN_":
            continue
        
        amounts = [abs(float(t.amount_signed)) for t in txns]
        dates = [datetime.strptime(t.date_posted, "%Y-%m-%d").date() for t in txns]
        
        # Determine stream type (majority)
        debits = sum(1 for t in txns if float(t.amount_signed) < 0)
        credits = sum(1 for t in txns if float(t.amount_signed) > 0)
        stream_type = "outflow" if debits > credits else "inflow"
        
        # Check for mixed (potential offsets)
        has_mixed = debits > 0 and credits > 0
        
        typical_dom, typical_dow = get_typical_timing(dates)
        
        avg_amount = statistics.mean(amounts) if amounts else 0
        total_amount = sum(float(t.amount_signed) for t in txns)
        
        suggested_groups.append({
            "temp_id": f"suggested_{len(suggested_groups)}",
            "suggested_name": desc_key[:50],
            "sample_description": txns[0].description if txns else "",
            "transaction_ids": [t.id for t in txns],
            "count": len(txns),
            "stream_type": stream_type,
            "has_mixed_signs": has_mixed,
            "frequency": calculate_frequency(dates),
            "avg_amount": round(avg_amount, 2),
            "total_amount": round(total_amount, 2),
            "typical_day_of_month": typical_dom,
            "typical_day_of_week": typical_dow,
            "date_range": {
                "earliest": min(dates).isoformat() if dates else None,
                "latest": max(dates).isoformat() if dates else None
            }
        })
    
    # Sort by total absolute amount (most impactful first)
    suggested_groups.sort(key=lambda g: -abs(g["total_amount"]))
    
    return {
        "groups": suggested_groups,
        "unassigned_count": len(desc_groups.get("_UNKNOWN_", []))
    }

def create_group(db: Session, user_id: int, name: str, description: str,
                 category: str, stream_type: str, frequency: str,
                 transaction_ids: List[int], allow_offsets: bool = False) -> TransactionGroup:
    """Create a new transaction group and assign transactions"""
    
    # Create group
    group = TransactionGroup(
        user_id=user_id,
        name=name,
        description=description,
        category=category,
        stream_type=stream_type,
        frequency=frequency,
        allow_offsets=allow_offsets
    )
    db.add(group)
    db.flush()
    
    # Assign transactions
    if transaction_ids:
        db.query(Transaction).filter(
            Transaction.id.in_(transaction_ids),
            Transaction.user_id == user_id
        ).update({Transaction.group_id: group.id}, synchronize_session=False)
    
    # Update stats
    update_group_stats(db, group.id)
    
    db.commit()
    db.refresh(group)
    
    return group

def update_group_stats(db: Session, group_id: int):
    """Recalculate group statistics"""
    group = db.query(TransactionGroup).filter(TransactionGroup.id == group_id).first()
    if not group:
        return
    
    txns = db.query(Transaction).filter(Transaction.group_id == group_id).all()
    
    if not txns:
        group.avg_amount = 0
        group.net_amount = 0
        group.transaction_count = 0
        group.offset_count = 0
        return
    
    amounts = [abs(float(t.amount_signed)) for t in txns]
    net = sum(float(t.amount_signed) for t in txns)
    
    # Count offsets (transactions with opposite sign to stream_type)
    if group.stream_type == "outflow":
        offset_count = sum(1 for t in txns if float(t.amount_signed) > 0)
    else:
        offset_count = sum(1 for t in txns if float(t.amount_signed) < 0)
    
    group.avg_amount = statistics.mean(amounts) if amounts else 0
    group.net_amount = net
    group.transaction_count = len(txns)
    group.offset_count = offset_count
    
    # Update timing
    dates = [datetime.strptime(t.date_posted, "%Y-%m-%d").date() for t in txns]
    dom, dow = get_typical_timing(dates)
    group.typical_day_of_month = dom
    group.typical_day_of_week = dow

def move_transactions_to_group(db: Session, user_id: int, 
                               transaction_ids: List[int], 
                               target_group_id: int,
                               mark_as_offset: bool = False):
    """Move transactions to a different group, optionally marking as offsets"""
    
    # Get source groups to update stats later
    source_groups = db.query(Transaction.group_id).filter(
        Transaction.id.in_(transaction_ids),
        Transaction.user_id == user_id,
        Transaction.group_id != None
    ).distinct().all()
    source_group_ids = [g[0] for g in source_groups]
    
    # Update transactions
    db.query(Transaction).filter(
        Transaction.id.in_(transaction_ids),
        Transaction.user_id == user_id
    ).update({
        Transaction.group_id: target_group_id,
        Transaction.is_offset: mark_as_offset
    }, synchronize_session=False)
    
    db.commit()
    
    # Update stats for affected groups
    for gid in source_group_ids:
        update_group_stats(db, gid)
    update_group_stats(db, target_group_id)
    
    db.commit()

def get_groups_for_user(db: Session, user_id: int) -> List[Dict]:
    """Get all groups for a user with stats"""
    groups = db.query(TransactionGroup).filter(
        TransactionGroup.user_id == user_id
    ).order_by(TransactionGroup.stream_type, TransactionGroup.name).all()
    
    return [
        {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "category": g.category,
            "stream_type": g.stream_type,
            "frequency": g.frequency,
            "avg_amount": float(g.avg_amount) if g.avg_amount else 0,
            "net_amount": float(g.net_amount) if g.net_amount else 0,
            "transaction_count": g.transaction_count,
            "offset_count": g.offset_count,
            "allow_offsets": g.allow_offsets,
            "typical_day_of_month": g.typical_day_of_month,
            "typical_day_of_week": g.typical_day_of_week,
            "trend": g.trend or "flat",
            "calculated_trend_percent": float(g.calculated_trend_percent) if g.calculated_trend_percent else 0,
            "adjusted_trend_percent": float(g.adjusted_trend_percent) if g.adjusted_trend_percent else None,
            "trend_period": g.trend_period or "month",
            "trend_duration_days": g.trend_duration_days,
            "trend_then": g.trend_then,
            "trend_then_percent": float(g.trend_then_percent) if g.trend_then_percent else None
        }
        for g in groups
    ]

def get_group_transactions(db: Session, user_id: int, group_id: int) -> List[Dict]:
    """Get all transactions in a group"""
    txns = db.query(Transaction).filter(
        Transaction.user_id == user_id,
        Transaction.group_id == group_id
    ).order_by(Transaction.date_posted.desc()).all()
    
    return [
        {
            "id": t.id,
            "date": t.date_posted,
            "amount": float(t.amount_signed),
            "description": t.description,
            "is_offset": t.is_offset
        }
        for t in txns
    ]
