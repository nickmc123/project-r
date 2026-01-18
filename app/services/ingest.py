"""
Transaction Ingestion Service
Smart parsing of bank data, QuickBooks, and web-pasted data
SUPER ROBUST - handles messy copy-paste from bank websites
"""
import re
import csv
import io
from datetime import date, datetime
from typing import List, Dict, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from ..models import Transaction

MONTHS = {
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
        "%b %d %Y",
        "%B %d %Y",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except:
            continue
    
    # Try "JAN 13, 2026" or "JAN 13 2026" pattern
    match = re.match(r'^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$', s)
    if match:
        month_str, day, year = match.groups()
        month = MONTHS.get(month_str.lower())
        if month:
            return date(int(year), month, int(day))
    
    return None

def parse_amount(s: str) -> Optional[float]:
    """Parse amount string to float"""
    if not s:
        return None
    
    s = s.strip()
    
    # Skip if it's clearly not an amount
    if not s or s in ['-', '--', '—', 'N/A', 'n/a', '']:
        return None
    
    # Remove currency symbols and commas
    s = re.sub(r'[$€£,]', '', s)
    
    # Handle parentheses for negatives
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    
    # Handle trailing minus
    if s.endswith('-'):
        s = '-' + s[:-1]
    
    try:
        val = float(s)
        return val if val != 0 else None
    except:
        return None

def extract_date_from_line(line: str) -> Optional[date]:
    """Try to extract a date header from a line"""
    line = line.strip()
    
    # Pattern 1: "JAN 13, 2026" or "JANUARY 13, 2026" or "Jan 13 2026"
    match = re.match(r'^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$', line, re.IGNORECASE)
    if match:
        month_str, day, year = match.groups()
        month = MONTHS.get(month_str.lower())
        if month:
            return date(int(year), month, int(day))
    
    # Pattern 2: "01/13/2026" or "1/13/2026" or "01-13-2026"
    match = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$', line)
    if match:
        m, d, y = match.groups()
        y = int(y)
        if y < 100:
            y += 2000
        try:
            return date(y, int(m), int(d))
        except:
            pass
    
    # Pattern 3: "2026-01-13"
    match = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', line)
    if match:
        y, m, d = match.groups()
        try:
            return date(int(y), int(m), int(d))
        except:
            pass
    
    return None

def split_line_smart(line: str) -> List[str]:
    """
    Smart split that handles:
    - Tab separated
    - Multiple space separated
    - Single delimiter
    """
    # First try tab split
    if '\t' in line:
        parts = line.split('\t')
        return [p.strip() for p in parts if p.strip()]
    
    # Try splitting on 2+ spaces (common when tabs converted to spaces)
    parts = re.split(r'\s{2,}', line)
    if len(parts) >= 3:
        return [p.strip() for p in parts if p.strip()]
    
    # Try splitting on any whitespace and regrouping
    # This handles lines like: "ACH DEBIT     5,234.56     123,456.78"
    words = line.split()
    if len(words) >= 2:
        # Find amount-like tokens from the right
        amounts_right = []
        desc_words = []
        
        for word in reversed(words):
            amt = parse_amount(word)
            if amt is not None and len(amounts_right) < 3:
                amounts_right.insert(0, word)
            else:
                desc_words.insert(0, word)
        
        if amounts_right:
            return [' '.join(desc_words)] + amounts_right
    
    return [line]

def parse_transaction_line(line: str, current_date: Optional[date]) -> Optional[Dict]:
    """
    Parse a single transaction line.
    Returns dict with date, amount, description, balance or None
    """
    if not line.strip():
        return None
    
    parts = split_line_smart(line)
    
    if len(parts) < 2:
        return None
    
    # Check if first part is a date
    line_date = parse_date(parts[0])
    if line_date:
        # Date is in first column, shift everything
        txn_date = line_date
        parts = parts[1:]
    else:
        txn_date = current_date
    
    if not txn_date:
        return None
    
    if len(parts) < 2:
        return None
    
    # Find all amounts in the parts
    amounts = []
    desc_parts = []
    
    for i, part in enumerate(parts):
        amt = parse_amount(part)
        if amt is not None:
            amounts.append((i, amt, part))
        else:
            desc_parts.append(part)
    
    if not amounts:
        return None
    
    description = ' '.join(desc_parts).strip()
    
    # Determine transaction amount and balance
    # Common formats:
    # 1. [Desc] [Amount] [Balance]
    # 2. [Desc] [Debit] [Credit] [Balance]
    # 3. [Desc] [Debit] [Credit]
    # 4. [Date] [Desc] [Amount] [Balance]
    
    balance = None
    txn_amount = None
    
    if len(amounts) == 1:
        # Single amount - it's the transaction
        txn_amount = amounts[0][1]
    elif len(amounts) == 2:
        # Two amounts - could be (amount, balance) or (debit, credit)
        # If second is larger, probably (txn, balance)
        if abs(amounts[1][1]) > abs(amounts[0][1]) * 2:
            txn_amount = amounts[0][1]
            balance = amounts[1][1]
        else:
            # Assume debit/credit format
            # Whichever is non-zero
            if amounts[0][1] != 0:
                txn_amount = -abs(amounts[0][1])  # Debit is negative
            else:
                txn_amount = abs(amounts[1][1])   # Credit is positive
    elif len(amounts) >= 3:
        # Three or more: [debit, credit, balance] most likely
        # First non-zero of debit/credit is the transaction
        if amounts[0][1] and amounts[0][1] != 0:
            txn_amount = -abs(amounts[0][1])
        elif amounts[1][1] and amounts[1][1] != 0:
            txn_amount = abs(amounts[1][1])
        balance = amounts[-1][1]
    
    if txn_amount is None or txn_amount == 0:
        return None
    
    return {
        'date': txn_date,
        'amount': txn_amount,
        'description': description,
        'balance': balance
    }

def parse_web_pasted_data(text: str) -> Tuple[List[Dict], List[str]]:
    """
    Parse messy web-pasted bank data.
    Returns (transactions, debug_log)
    """
    lines = text.strip().split('\n')
    transactions = []
    debug_log = []
    current_date = None
    
    # First pass: find if there's a header row to skip
    skip_header = False
    for line in lines[:3]:
        lower = line.lower()
        if any(h in lower for h in ['description', 'debit', 'credit', 'balance', 'amount', 'transaction']):
            skip_header = True
            break
    
    started = False
    for i, line in enumerate(lines):
        line = line.strip()
        
        if not line:
            continue
        
        # Skip header row
        if skip_header and not started:
            lower = line.lower()
            if any(h in lower for h in ['description', 'debit', 'credit', 'balance', 'amount']):
                debug_log.append(f"Line {i}: SKIP HEADER: {line[:50]}")
                started = True
                continue
        
        started = True
        
        # Check for date header
        date_from_line = extract_date_from_line(line)
        if date_from_line:
            current_date = date_from_line
            debug_log.append(f"Line {i}: DATE HEADER: {current_date}")
            continue
        
        # Try to parse as transaction
        txn = parse_transaction_line(line, current_date)
        
        if txn:
            transactions.append(txn)
            debug_log.append(f"Line {i}: PARSED: {txn['description'][:30]} = ${txn['amount']:.2f}")
        else:
            # Check if this line has an inline date
            # Sometimes format is: "01/13/2026  Description  Amount  Balance"
            parts = split_line_smart(line)
            if parts and len(parts) >= 3:
                inline_date = parse_date(parts[0])
                if inline_date:
                    # Try parsing with this date
                    txn = parse_transaction_line('\t'.join(parts[1:]), inline_date)
                    if txn:
                        transactions.append(txn)
                        debug_log.append(f"Line {i}: INLINE DATE PARSED: {txn['description'][:30]} = ${txn['amount']:.2f}")
                        continue
            
            debug_log.append(f"Line {i}: SKIPPED: {line[:50]}")
    
    return transactions, debug_log

def ingest_bank_data(db: Session, user_id: int, content: str, raw_file_id: str = "") -> Dict:
    """
    Ingest bank data from various formats.
    This is the main entry point for data import.
    """
    # Normalize line endings
    text = content.replace('\r\n', '\n').replace('\r', '\n')
    
    transactions = []
    debug_log = []
    
    # Try CSV parsing first (if it looks like CSV)
    if ',' in text and ('\n' in text):
        try:
            reader = csv.DictReader(io.StringIO(text))
            headers = [h.lower().strip() for h in (reader.fieldnames or [])]
            
            if headers:  # Valid CSV with headers
                debug_log.append(f"CSV headers detected: {headers}")
                
                # Map headers to our fields
                date_col = next((h for h in headers if 'date' in h), None)
                desc_col = next((h for h in headers if 'description' in h or 'memo' in h or 'name' in h or 'payee' in h), None)
                amount_col = next((h for h in headers if h == 'amount'), None)
                debit_col = next((h for h in headers if 'debit' in h or 'withdrawal' in h or 'outflow' in h), None)
                credit_col = next((h for h in headers if 'credit' in h or 'deposit' in h or 'inflow' in h), None)
                balance_col = next((h for h in headers if 'balance' in h), None)
                
                debug_log.append(f"Mapped: date={date_col}, desc={desc_col}, amt={amount_col}, deb={debit_col}, cred={credit_col}")
                
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
                        debug_log.append(f"CSV row: {description[:30]} = ${amount:.2f}")
        except Exception as e:
            debug_log.append(f"CSV parsing failed: {str(e)}")
    
    # If CSV didn't work or found nothing, try web-pasted format
    if not transactions:
        debug_log.append("Trying web-pasted format...")
        transactions, paste_debug = parse_web_pasted_data(text)
        debug_log.extend(paste_debug)
    
    # Insert transactions with duplicate detection
    added = 0
    skipped = 0
    duplicates = 0
    
    for txn in transactions:
        # Check for duplicate (same date + amount + description)
        existing = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.date_posted == txn['date'].isoformat(),
            Transaction.amount_signed == txn['amount'],
            Transaction.description == txn['description']
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
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
        "duplicates": duplicates,
        "total_parsed": len(transactions),
        "debug": debug_log[-20:]  # Last 20 log entries
    }

# Keep old function name for compatibility
def ingest_bank_csv(db: Session, user_id: int, content: bytes, raw_file_id: str = "") -> Dict:
    """Legacy wrapper"""
    text = content.decode('utf-8', errors='ignore')
    return ingest_bank_data(db, user_id, text, raw_file_id)

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
    duplicates = 0
    
    for txn in transactions:
        existing = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.date_posted == txn['date'].isoformat(),
            Transaction.amount_signed == txn['amount'],
            Transaction.description == txn['description']
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
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
    
    db.commit()
    
    return {"added": added, "duplicates": duplicates}
