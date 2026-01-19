"""
PROJECT-R FORECASTING ENGINE
Pure deterministic - no AI involvement in calculations
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import statistics
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models import Transaction, TransactionGroup, ScheduleRule, TrendSentiment, CalendarDay

def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def today_date() -> date:
    return date.today()

def start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def trimmed_mean(values, trim=0.1):
    if not values:
        return 0.0
    v = sorted(values)
    n = len(v)
    k = int(n * trim)
    v2 = v[k:n-k] if n - 2*k > 0 else v
    return float(sum(v2) / max(1, len(v2)))

def get_bank_holidays_2026() -> List[date]:
    """US Federal bank holidays"""
    return [
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Presidents Day
        date(2026, 5, 25),  # Memorial Day
        date(2026, 7, 3),   # Independence Day observed
        date(2026, 9, 7),   # Labor Day
        date(2026, 10, 12), # Columbus Day
        date(2026, 11, 11), # Veterans Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas
    ]

def is_business_day(d: date, holidays: List[date] = None) -> bool:
    """Check if date is a business day"""
    if holidays is None:
        holidays = get_bank_holidays_2026()
    return d.weekday() < 5 and d not in holidays

def next_business_day(d: date, holidays: List[date] = None) -> date:
    """Get next business day"""
    if holidays is None:
        holidays = get_bank_holidays_2026()
    while not is_business_day(d, holidays):
        d += timedelta(days=1)
    return d

def expand_schedule_rule(rule: ScheduleRule, start: date, end: date) -> List[Tuple[date, float, str]]:
    """Expand a schedule rule into specific dates and amounts"""
    events = []
    params = json.loads(rule.rule_params_json) if rule.rule_params_json else {}
    amount = float(rule.amount)
    name = rule.name
    
    if rule.rule_type == "monthly":
        day = int(params.get("day", 1))
        roll = params.get("roll", "none")
        cur = date(start.year, start.month, 1)
        
        while cur <= end:
            try:
                d = date(cur.year, cur.month, min(day, 28))
                if day > 28:
                    # Handle end of month
                    next_month = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
                    last_day = (next_month - timedelta(days=1)).day
                    d = date(cur.year, cur.month, min(day, last_day))
            except:
                d = cur
            
            if roll in ("prev_business_day", "previous_business_day"):
                while not is_business_day(d):
                    d -= timedelta(days=1)
            elif roll in ("next_business_day",):
                d = next_business_day(d)
            
            if start <= d <= end:
                events.append((d, amount, name))
            
            # Next month
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
    
    elif rule.rule_type == "semi_monthly":
        day1 = int(params.get("day1", 1))
        day2 = params.get("day2", 15)
        if day2 == "last":
            day2 = 31
        else:
            day2 = int(day2)
        
        cur = date(start.year, start.month, 1)
        while cur <= end:
            for day in [day1, day2]:
                try:
                    d = date(cur.year, cur.month, min(day, 28))
                except:
                    continue
                
                if start <= d <= end:
                    events.append((d, amount, name))
            
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
    
    elif rule.rule_type == "weekly":
        weekday = int(params.get("weekday", 0))
        d = start
        while d <= end:
            if d.weekday() == weekday:
                events.append((d, amount, name))
            d += timedelta(days=1)
    
    elif rule.rule_type == "fixed_dates":
        dates = params.get("dates", [])
        for date_str in dates:
            try:
                d = parse_date(date_str)
                if start <= d <= end:
                    events.append((d, amount, name))
            except:
                continue
    
    return events

def analyze_trends(db: Session, user_id: int, group_id: int) -> Dict:
    """Analyze historical trends for a group"""
    txns = db.query(Transaction).filter(
        Transaction.user_id == user_id,
        Transaction.group_id == group_id
    ).order_by(Transaction.date_posted).all()
    
    if len(txns) < 4:
        return {"trend": "flat", "pct_per_month": 0, "confidence": "low"}
    
    # Group by month
    monthly = {}
    for t in txns:
        month_key = t.date_posted[:7]  # YYYY-MM
        if month_key not in monthly:
            monthly[month_key] = 0
        monthly[month_key] += abs(float(t.amount_signed))
    
    if len(monthly) < 2:
        return {"trend": "flat", "pct_per_month": 0, "confidence": "low"}
    
    # Calculate month-over-month changes
    months = sorted(monthly.keys())
    changes = []
    for i in range(1, len(months)):
        prev = monthly[months[i-1]]
        curr = monthly[months[i]]
        if prev > 0:
            pct_change = (curr - prev) / prev * 100
            changes.append(pct_change)
    
    if not changes:
        return {"trend": "flat", "pct_per_month": 0, "confidence": "low"}
    
    avg_change = statistics.mean(changes)
    std_dev = statistics.stdev(changes) if len(changes) > 1 else 0
    
    # Determine trend
    if avg_change > 3:
        trend = "up"
    elif avg_change < -3:
        trend = "down"
    else:
        trend = "flat"
    
    # Confidence based on consistency
    if std_dev < 5:
        confidence = "high"
    elif std_dev < 15:
        confidence = "medium"
    else:
        confidence = "low"
    
    return {
        "trend": trend,
        "pct_per_month": round(avg_change, 2),
        "std_dev": round(std_dev, 2),
        "confidence": confidence,
        "months_analyzed": len(months)
    }

def compute_forecast(db: Session, user_id: int, horizon_days: int = 90,
                     starting_balance: Optional[float] = None,
                     apply_sentiments: bool = True) -> Dict:
    """
    Compute cash flow forecast.
    Returns both baseline and sentiment-adjusted projections.
    """
    today = today_date()
    end_date = today + timedelta(days=horizon_days)
    
    # Get all transactions for history analysis
    txns = db.query(Transaction).filter(
        Transaction.user_id == user_id
    ).order_by(Transaction.date_posted).all()
    
    if not txns:
        return {
            "error": "No transactions uploaded yet",
            "series": [],
            "baseline_series": [],
            "summary": {}
        }
    
    # Get groups for categorized analysis
    groups = db.query(TransactionGroup).filter(
        TransactionGroup.user_id == user_id
    ).all()
    
    group_by_id = {g.id: g for g in groups}
    
    # Calculate starting balance (sum of all transactions or provided)
    if starting_balance is None:
        starting_balance = sum(float(t.amount_signed) for t in txns)
    
    # Group transactions by category for rate calculation
    group_daily_rates = {}
    
    for group in groups:
        group_txns = [t for t in txns if t.group_id == group.id]
        if not group_txns:
            continue
        
        # Get last 90 days of data
        recent_txns = [t for t in group_txns 
                       if parse_date(t.date_posted) >= today - timedelta(days=90)]
        
        if not recent_txns:
            recent_txns = group_txns[-30:]  # Use last 30 transactions if no recent
        
        # Calculate weighted daily rate
        # 50% weight on last 2 weeks, 50% on prior period
        two_weeks_ago = today - timedelta(days=14)
        
        recent_amounts = [abs(float(t.amount_signed)) for t in recent_txns 
                         if parse_date(t.date_posted) >= two_weeks_ago]
        older_amounts = [abs(float(t.amount_signed)) for t in recent_txns 
                        if parse_date(t.date_posted) < two_weeks_ago]
        
        recent_days = max(1, 14)
        older_days = max(1, 76)  # 90 - 14
        
        recent_daily = sum(recent_amounts) / recent_days if recent_amounts else 0
        older_daily = sum(older_amounts) / older_days if older_amounts else 0
        
        weighted_daily = (recent_daily * 0.5 + older_daily * 0.5)
        
        group_daily_rates[group.id] = {
            "rate": weighted_daily,
            "stream_type": group.stream_type,
            "frequency": group.frequency,
            "category": group.category
        }
    
    # Get schedule rules
    schedule_rules = db.query(ScheduleRule).filter(
        ScheduleRule.user_id == user_id
    ).all()
    
    # Expand scheduled events
    scheduled_events = []
    for rule in schedule_rules:
        events = expand_schedule_rule(rule, today, end_date)
        for event_date, amount, name in events:
            scheduled_events.append({
                "date": event_date,
                "amount": amount,
                "name": name,
                "group_id": rule.group_id
            })
    
    # Get trend sentiments - first from TransactionGroup columns, then TrendSentiment table
    sentiments = {}
    if apply_sentiments:
        # First check TransactionGroup columns for user adjustments
        for group in groups:
            if group.adjusted_trend_percent is not None:
                # Use absolute value of the percentage - direction field controls sign
                change_pct = abs(float(group.adjusted_trend_percent))
                direction = group.trend or "flat"
                
                # Map trend values to forecast direction
                # "up" = continue increasing at this rate
                # "down" = reverse/decline at this rate
                # "flat" = no change from baseline
                if direction == "up":
                    sentiments[group.id] = {
                        "direction": "continue",
                        "change_pct": change_pct
                    }
                elif direction == "down":
                    sentiments[group.id] = {
                        "direction": "reverse",
                        "change_pct": change_pct
                    }
                # "flat" = no sentiment adjustment (baseline projection used)
        
        # Also check TrendSentiment table as fallback
        sent_rows = db.query(TrendSentiment).filter(
            TrendSentiment.user_id == user_id
        ).all()
        for s in sent_rows:
            if s.group_id not in sentiments:  # Don't override group settings
                sentiments[s.group_id] = {
                    "direction": s.expected_direction,
                    "change_pct": abs(float(s.expected_change_pct))
                }
    
    # Build daily projection
    baseline_daily = {}
    adjusted_daily = {}
    
    current_date = today
    baseline_balance = starting_balance
    adjusted_balance = starting_balance
    
    # Track high/low points
    baseline_high = {"date": today, "balance": baseline_balance}
    baseline_low = {"date": today, "balance": baseline_balance}
    adjusted_high = {"date": today, "balance": adjusted_balance}
    adjusted_low = {"date": today, "balance": adjusted_balance}
    
    holidays = get_bank_holidays_2026()
    
    while current_date <= end_date:
        day_inflow = 0.0
        day_outflow = 0.0
        day_inflow_adj = 0.0
        day_outflow_adj = 0.0
        
        is_working = is_business_day(current_date, holidays)
        
        # Apply daily rates for each group
        for group_id, rate_info in group_daily_rates.items():
            if rate_info["frequency"] in ("daily", "weekly") and is_working:
                daily_rate = rate_info["rate"]
                
                # Adjust for weekly (divide by 5 working days)
                if rate_info["frequency"] == "weekly":
                    daily_rate = daily_rate / 5
                
                # Apply sentiment adjustment
                adjustment = 1.0
                if group_id in sentiments:
                    sent = sentiments[group_id]
                    months_out = (current_date - today).days / 30
                    if sent["direction"] == "continue":
                        adjustment = 1 + (sent["change_pct"] / 100 * months_out)
                    elif sent["direction"] == "reverse":
                        adjustment = 1 - (sent["change_pct"] / 100 * months_out)
                    # flatten = no change
                
                if rate_info["stream_type"] == "inflow":
                    day_inflow += daily_rate
                    day_inflow_adj += daily_rate * adjustment
                else:
                    day_outflow += daily_rate
                    day_outflow_adj += daily_rate * adjustment
        
        # Apply scheduled events for this date
        for event in scheduled_events:
            if event["date"] == current_date:
                if event["amount"] > 0:
                    day_inflow += event["amount"]
                    day_inflow_adj += event["amount"]
                else:
                    day_outflow += abs(event["amount"])
                    day_outflow_adj += abs(event["amount"])
        
        # Update balances
        baseline_balance += day_inflow - day_outflow
        adjusted_balance += day_inflow_adj - day_outflow_adj
        
        # Track high/low
        if baseline_balance > baseline_high["balance"]:
            baseline_high = {"date": current_date, "balance": baseline_balance}
        if baseline_balance < baseline_low["balance"]:
            baseline_low = {"date": current_date, "balance": baseline_balance}
        
        if adjusted_balance > adjusted_high["balance"]:
            adjusted_high = {"date": current_date, "balance": adjusted_balance}
        if adjusted_balance < adjusted_low["balance"]:
            adjusted_low = {"date": current_date, "balance": adjusted_balance}
        
        baseline_daily[current_date] = {
            "balance": round(baseline_balance, 2),
            "inflow": round(day_inflow, 2),
            "outflow": round(day_outflow, 2)
        }
        
        adjusted_daily[current_date] = {
            "balance": round(adjusted_balance, 2),
            "inflow": round(day_inflow_adj, 2),
            "outflow": round(day_outflow_adj, 2)
        }
        
        current_date += timedelta(days=1)
    
    # Build weekly series
    baseline_series = []
    adjusted_series = []
    
    week_start = start_of_week(today)
    while week_start <= end_date:
        week_end = week_start + timedelta(days=6)
        
        # Baseline week
        week_inflow = sum(baseline_daily.get(week_start + timedelta(days=i), {}).get("inflow", 0) 
                         for i in range(7))
        week_outflow = sum(baseline_daily.get(week_start + timedelta(days=i), {}).get("outflow", 0) 
                          for i in range(7))
        end_bal = baseline_daily.get(min(week_end, end_date), {}).get("balance", 0)
        
        baseline_series.append({
            "week": week_start.isoformat(),
            "inflow": round(week_inflow, 2),
            "outflow": round(week_outflow, 2),
            "balance_end": round(end_bal, 2)
        })
        
        # Adjusted week
        week_inflow_adj = sum(adjusted_daily.get(week_start + timedelta(days=i), {}).get("inflow", 0) 
                             for i in range(7))
        week_outflow_adj = sum(adjusted_daily.get(week_start + timedelta(days=i), {}).get("outflow", 0) 
                              for i in range(7))
        end_bal_adj = adjusted_daily.get(min(week_end, end_date), {}).get("balance", 0)
        
        adjusted_series.append({
            "week": week_start.isoformat(),
            "inflow": round(week_inflow_adj, 2),
            "outflow": round(week_outflow_adj, 2),
            "balance_end": round(end_bal_adj, 2)
        })
        
        week_start += timedelta(days=7)
    
    # Calculate 30-day profit (net change)
    thirty_days = today + timedelta(days=30)
    day_30_balance = baseline_daily.get(thirty_days, {}).get("balance", starting_balance)
    monthly_profit = day_30_balance - starting_balance
    
    return {
        "starting_balance": round(starting_balance, 2),
        "horizon_days": horizon_days,
        "baseline_series": baseline_series,
        "adjusted_series": adjusted_series if apply_sentiments and sentiments else None,
        "summary": {
            "baseline": {
                "high_point": {
                    "date": baseline_high["date"].isoformat(),
                    "balance": round(baseline_high["balance"], 2)
                },
                "low_point": {
                    "date": baseline_low["date"].isoformat(),
                    "balance": round(baseline_low["balance"], 2)
                },
                "ending_balance": round(list(baseline_daily.values())[-1]["balance"], 2),
                "monthly_profit": round(monthly_profit, 2)
            }
        }
    }
