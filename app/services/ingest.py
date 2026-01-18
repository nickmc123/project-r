"""
Transaction Ingestion Service
Smart parsing of bank data, QuickBooks, and web-pasted data
"""
import re
import csv
import io
from datetime import date, datetime
from typing import List, Dict, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from ..models import Transaction

def parse_date(s: str) -> Optional[date]:
    """Parse various date formats"""
    if not s:
        return None
    
    s = s.strip()
    
    # Try common formats
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%d/%m/%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except:
            continue
    
    return None

def parse_amount(s: str) -> Optional[float]:
    """Parse amount string to float"""
    if not s:
        return None
    
    s = s.strip()
    
    # Remove currency symbols and commas
    s = re.sub(r'[$,]', '', s)
    
    # Handle parentheses for negatives
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    
    try:
        return float(s)
    except:
        return None

def ingest_bank_csv(db: Session, user_id: int, content: bytes, raw_file_id: str = "") -> Dict:
    """
    Ingest bank CSV data.
    Handles various formats including web-pasted data.
    """
    text = content.decode('utf-8', errors='ignore')
    
    # Detect format and parse
    transactions = []
    
    # Try CSV first
    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.lower().strip() for h in (reader.fieldnames or [])]
        
        # Map headers to our fields
        date_col = next((h for h in headers if 'date' in h), None)
        desc_col = next((h for h in headers if 'description' in h or 'memo' in h or 'name' in h), None)
        amount_col = next((h for h in headers if h == 'amount'), None)
        debit_col = next((h for h in headers if 'debit' in h or 'withdrawal' in h), None)
        credit_col = next((h for h in headers if 'credit' in h or 'deposit' in h), None)
        balance_col = next((h for h in headers if 'balance' in h), None)
        
        for row in reader:
            row_lower = {k.lower().strip(): v for k, v in row.items()}
            
            txn_date = parse_date(row_lower.get(date_col, '')) if date_col else None
            description = row_lower.get(desc_col, '') if desc_col else ''
            
            # Determine amount
            amount = None
            if amount_col and row_lower.get(amount_col):
                amount = parse_amount(row_lower.get(amount_col))
            elif debit_col or credit_col:
                debit = parse_amount(row_lower.get(debit_col, '')) if debit_col else None
                credit = parse_amount(row_lower.get(credit_col, '')) if credit_col else None
                if debit and debit > 0:
                    amount = -abs(debit)
                elif credit and credit > 0:
                    amount = abs(credit)
            
            balance = parse_amount(row_lower.get(balance_col, '')) if balance_col else None
            
            if txn_date and amount:
                transactions.append({
                    'date': txn_date,
                    'amount': amount,
                    'description': description.strip(),
                    'balance': balance
                })
    except Exception as e:
        # Try line-by-line parsing for web-pasted data
        transactions = parse_web_pasted_data(text)
    
    # Insert transactions
    added = 0
    skipped = 0
    
    for txn in transactions:
        try:
            t = Transaction(
                user_id=user_id,
                date_posted=txn['date'].isoformat(),
                amount_signed=txn['amount'],
                description=txn['description'],
                balance=txn.get('balance'),
                raw_file_id=raw_file_id
            )
            db.add(t)
            db.flush()
            added += 1
        except IntegrityError:
            db.rollback()
            skipped += 1
    
    db.commit()
    
    return {
        "added": added,
        "skipped": skipped,
        "total_parsed": len(transactions)
    }

def parse_web_pasted_data(text: str) -> List[Dict]:
    """Parse messy web-pasted bank data with date headers"""
    lines = text.strip().split('\n')
    transactions = []
    current_date = None
    
    # Date header pattern (e.g., "JAN 13, 2026" or "January 13, 2026")
    date_header_pattern = re.compile(
        r'^([A-Z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$', 
        re.IGNORECASE
    )
    
    months = {
        'jan': 1, 'january': 1,
        'feb': 2, 'february': 2,
        'mar': 3, 'march': 3,
        'apr': 4, 'april': 4,
        'may': 5,
        'jun': 6, 'june': 6,
        'jul': 7, 'july': 7,
        'aug': 8, 'august': 8,
        'sep': 9, 'sept': 9, 'september': 9,
        'oct': 10, 'october': 10,
        'nov': 11, 'november': 11,
        'dec': 12, 'december': 12
    }
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Check for date header
        match = date_header_pattern.match(line)
        if match:
            month_str, day, year = match.groups()
            month = months.get(month_str.lower())
            if month:
                current_date = date(int(year), month, int(day))
            continue
        
        if not current_date:
            continue
        
        # Try to parse transaction line
        # Format: Description\tDebit\tCredit\tBalance (tab-separated)
        parts = line.split('\t')
        
        if len(parts) >= 2:
            description = parts[0].strip()
            
            # Find amounts in remaining parts
            amounts = []
            for p in parts[1:]:
                amt = parse_amount(p)
                if amt is not None:
                    amounts.append(amt)
            
            if amounts:
                # Assume last is balance, first non-zero is transaction
                balance = amounts[-1] if len(amounts) > 1 else None
                
                # Determine transaction amount
                txn_amount = None
                if len(amounts) >= 3:
                    # Debit, Credit, Balance format
                    if amounts[0] > 0:
                        txn_amount = -amounts[0]  # Debit
                    elif amounts[1] > 0:
                        txn_amount = amounts[1]  # Credit
                elif len(amounts) == 2:
                    # Amount, Balance or Debit/Credit
                    txn_amount = amounts[0]
                elif len(amounts) == 1:
                    txn_amount = amounts[0]
                
                if txn_amount and txn_amount != 0:
                    transactions.append({
                        'date': current_date,
                        'amount': txn_amount,
                        'description': description,
                        'balance': balance
                    })
    
    return transactions

def ingest_quickbooks_data(db: Session, user_id: int, data: Dict) -> Dict:
    """
    Ingest QuickBooks data via API.
    Expects data from QuickBooks Online API.
    """
    transactions = []
    
    # Handle QueryResponse format
    if 'QueryResponse' in data:
        purchases = data['QueryResponse'].get('Purchase', [])
        deposits = data['QueryResponse'].get('Deposit', [])
        
        for p in purchases:
            txn_date = parse_date(p.get('TxnDate', ''))
            total = p.get('TotalAmt', 0)
            desc = p.get('PrivateNote', '') or p.get('DocNumber', '') or 'Purchase'
            
            if txn_date and total:
                transactions.append({
                    'date': txn_date,
                    'amount': -abs(float(total)),
                    'description': desc
                })
        
        for d in deposits:
            txn_date = parse_date(d.get('TxnDate', ''))
            total = d.get('TotalAmt', 0)
            desc = d.get('PrivateNote', '') or 'Deposit'
            
            if txn_date and total:
                transactions.append({
                    'date': txn_date,
                    'amount': abs(float(total)),
                    'description': desc
                })
    
    # Insert
    added = 0
    skipped = 0
    
    for txn in transactions:
        try:
            t = Transaction(
                user_id=user_id,
                date_posted=txn['date'].isoformat(),
                amount_signed=txn['amount'],
                description=txn['description'],
                raw_file_id='quickbooks'
            )
            db.add(t)
            db.flush()
            added += 1
        except IntegrityError:
            db.rollback()
            skipped += 1
    
    db.commit()
    
    return {"added": added, "skipped": skipped}
