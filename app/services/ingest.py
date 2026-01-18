"""
Transaction Ingestion Service - Interactive Parser

Smart parsing that:
1. Analyzes data structure automatically
2. If confident, parses and returns for confirmation
3. If uncertain, asks user to define field mappings
4. Learns custom parsing rules from user input
5. Always shows confirmation before saving
"""

from typing import List, Dict, Optional, Tuple, Any
from datetime import date, datetime
import re
import json

# Date patterns to try
DATE_PATTERNS = [
    # Full formats
    (r'(\d{1,2})/(\d{1,2})/(\d{4})', lambda m: date(int(m.group(3)), int(m.group(1)), int(m.group(2)))),
    (r'(\d{1,2})/(\d{1,2})/(\d{2})', lambda m: date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))),
    (r'(\d{4})-(\d{2})-(\d{2})', lambda m: date(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (r'(\d{1,2})-(\d{1,2})-(\d{4})', lambda m: date(int(m.group(3)), int(m.group(1)), int(m.group(2)))),
    # Month name formats
    (r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2}),?\s+(\d{4})', 'month_name'),
    (r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})', 'month_name_full'),
]

MONTH_MAP = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4, 'MAY': 5, 'JUNE': 6,
    'JULY': 7, 'AUGUST': 8, 'SEPTEMBER': 9, 'OCTOBER': 10, 'NOVEMBER': 11, 'DECEMBER': 12
}


def parse_date(s: str) -> Optional[date]:
    """Try to parse a date from string"""
    if not s:
        return None
    s = s.strip()
    
    for pattern, handler in DATE_PATTERNS:
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            if handler == 'month_name' or handler == 'month_name_full':
                month = MONTH_MAP.get(match.group(1).upper())
                day = int(match.group(2))
                year = int(match.group(3))
                try:
                    return date(year, month, day)
                except:
                    continue
            else:
                try:
                    return handler(match)
                except:
                    continue
    return None


def parse_amount(s: str) -> Optional[float]:
    """Parse amount - always returns positive value"""
    if not s:
        return None
    s = s.strip()
    
    # Remove currency symbols and formatting
    s = re.sub(r'[$€£¥]', '', s)
    s = s.replace(',', '')
    s = s.replace(' ', '')
    
    # Handle parentheses as negative (but we return positive)
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1]
    
    # Remove any negative sign
    s = s.lstrip('-+')
    
    # Try to parse
    try:
        val = float(s)
        return abs(val) if val != 0 else None
    except:
        return None


def is_amount_like(s: str) -> bool:
    """Check if string looks like an amount"""
    if not s:
        return False
    s = s.strip()
    # Remove currency and formatting
    cleaned = re.sub(r'[$€£¥,\s()]', '', s)
    cleaned = cleaned.lstrip('-+')
    try:
        float(cleaned)
        return True
    except:
        return False


def is_date_like(s: str) -> bool:
    """Check if string looks like a date"""
    return parse_date(s) is not None


def detect_separator(data: str) -> str:
    """Detect the field separator"""
    lines = data.strip().split('\n')
    
    # Count occurrences
    tab_count = sum(line.count('\t') for line in lines[:10])
    comma_count = sum(line.count(',') for line in lines[:10])
    pipe_count = sum(line.count('|') for line in lines[:10])
    
    if tab_count > comma_count and tab_count > pipe_count:
        return '\t'
    elif pipe_count > comma_count:
        return '|'
    elif comma_count > 0:
        return ','
    else:
        return '\t'  # Default to tab for whitespace-separated


def split_line(line: str, separator: str) -> List[str]:
    """Split a line by separator, preserving empty fields"""
    if separator == '\t':
        # For tabs, split and keep empties
        parts = line.split('\t')
        return [p.strip() for p in parts]
    elif separator == ',':
        # CSV - handle quoted fields
        parts = []
        current = ""
        in_quotes = False
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        parts.append(current.strip())
        return parts
    else:
        return [p.strip() for p in line.split(separator)]


def analyze_data_structure(data: str) -> Dict[str, Any]:
    """
    Analyze the data and return structure info.
    Returns confidence level and detected fields.
    """
    lines = [l for l in data.strip().split('\n') if l.strip()]
    if not lines:
        return {"confidence": 0, "error": "No data provided"}
    
    separator = detect_separator(data)
    
    # Analyze first 20 lines to understand structure
    sample_rows = []
    header_row = None
    data_rows = []
    
    for i, line in enumerate(lines[:30]):
        parts = split_line(line, separator)
        
        # Check if this is a date header line (just a date)
        if len(parts) == 1 and is_date_like(parts[0]):
            continue
            
        # Check if this is a column header row
        if i == 0:
            # Headers typically have text that aren't numbers or dates
            non_numeric = sum(1 for p in parts if p and not is_amount_like(p) and not is_date_like(p))
            if non_numeric >= len(parts) // 2:
                header_row = parts
                continue
        
        if parts and any(p.strip() for p in parts):
            sample_rows.append(parts)
            if len(data_rows) < 20:
                data_rows.append({"line": i + 1, "raw": line, "parts": parts})
    
    if not sample_rows:
        return {"confidence": 0, "error": "No parseable rows found"}
    
    # Analyze columns
    num_cols = max(len(row) for row in sample_rows)
    column_analysis = []
    
    for col_idx in range(num_cols):
        col_values = [row[col_idx] if col_idx < len(row) else "" for row in sample_rows]
        non_empty = [v for v in col_values if v.strip()]
        
        # Determine column type
        date_count = sum(1 for v in non_empty if is_date_like(v))
        amount_count = sum(1 for v in non_empty if is_amount_like(v))
        
        col_type = "unknown"
        confidence = 0
        
        if len(non_empty) == 0:
            col_type = "empty"
            confidence = 100
        elif date_count > len(non_empty) * 0.7:
            col_type = "date"
            confidence = int(date_count / len(non_empty) * 100)
        elif amount_count > len(non_empty) * 0.7:
            col_type = "amount"
            confidence = int(amount_count / len(non_empty) * 100)
        else:
            col_type = "text"
            confidence = 80
        
        # Check header name hints
        header_name = header_row[col_idx].upper() if header_row and col_idx < len(header_row) else ""
        if any(h in header_name for h in ['DATE', 'POSTED', 'TRANS']):
            col_type = "date"
            confidence = 95
        elif any(h in header_name for h in ['DEBIT', 'WITHDRAWAL', 'PAYMENT', 'CHARGE']):
            col_type = "debit"
            confidence = 95
        elif any(h in header_name for h in ['CREDIT', 'DEPOSIT', 'RECEIPT']):
            col_type = "credit"
            confidence = 95
        elif any(h in header_name for h in ['AMOUNT', 'TOTAL']):
            col_type = "amount"
            confidence = 90
        elif any(h in header_name for h in ['BALANCE', 'RUNNING']):
            col_type = "balance"
            confidence = 95
        elif any(h in header_name for h in ['DESC', 'MEMO', 'PAYEE', 'NAME', 'DETAIL']):
            col_type = "description"
            confidence = 95
        
        column_analysis.append({
            "index": col_idx,
            "header": header_row[col_idx] if header_row and col_idx < len(header_row) else f"Column {col_idx + 1}",
            "type": col_type,
            "confidence": confidence,
            "sample_values": non_empty[:5]
        })
    
    # Calculate overall confidence
    identified_types = [c["type"] for c in column_analysis if c["type"] not in ["unknown", "empty"]]
    has_date = any(c["type"] == "date" for c in column_analysis)
    has_amount = any(c["type"] in ["amount", "debit", "credit"] for c in column_analysis)
    has_description = any(c["type"] in ["description", "text"] for c in column_analysis)
    
    overall_confidence = 0
    if has_date and has_amount and has_description:
        overall_confidence = 90
    elif has_amount and has_description:
        overall_confidence = 70
    elif has_amount:
        overall_confidence = 50
    else:
        overall_confidence = 20
    
    return {
        "confidence": overall_confidence,
        "separator": separator,
        "has_header": header_row is not None,
        "header_row": header_row,
        "num_columns": num_cols,
        "total_rows": len(lines),
        "data_rows": len(sample_rows),
        "columns": column_analysis,
        "sample_data": data_rows[:10],
        "needs_user_input": overall_confidence < 70
    }


def parse_with_mapping(data: str, mapping: Dict[str, Any]) -> Tuple[List[Dict], List[str]]:
    """
    Parse data using user-defined field mapping.
    
    mapping = {
        "separator": "\t",
        "has_header": true,
        "skip_lines": 0,
        "fields": {
            "date": {"column": 0},  # or {"column": null} if date is in header
            "amount": {"column": 2},
            "description": {"column": 1},
            "sign": {"column": 3, "debit_value": "DR", "credit_value": "CR"},  # or {"default": "debit"}
            "balance": {"column": 4},  # optional
            "account": {"column": null},  # optional
            "category": {"column": null}  # optional
        },
        "custom_fields": [
            {"name": "reference", "column": 5}
        ]
    }
    """
    lines = data.strip().split('\n')
    transactions = []
    debug_log = []
    
    separator = mapping.get("separator", "\t")
    has_header = mapping.get("has_header", False)
    skip_lines = mapping.get("skip_lines", 0)
    fields = mapping.get("fields", {})
    custom_fields = mapping.get("custom_fields", [])
    
    current_date = date.today()
    
    for i, line in enumerate(lines):
        if i < skip_lines:
            continue
        if has_header and i == skip_lines:
            continue
            
        line = line.strip()
        if not line:
            continue
        
        parts = split_line(line, separator)
        
        # Check if this is a standalone date header
        if len(parts) == 1:
            parsed = parse_date(parts[0])
            if parsed:
                current_date = parsed
                debug_log.append(f"Line {i+1}: Date header -> {current_date}")
                continue
        
        # Extract fields
        txn = {"raw_line": line, "line_number": i + 1}
        
        # Date
        date_cfg = fields.get("date", {})
        if date_cfg.get("column") is not None:
            col = date_cfg["column"]
            if col < len(parts):
                txn["date"] = parse_date(parts[col]) or current_date
            else:
                txn["date"] = current_date
        else:
            txn["date"] = current_date
        
        # Amount (always positive)
        amount_cfg = fields.get("amount", {})
        amount = None
        if amount_cfg.get("column") is not None:
            col = amount_cfg["column"]
            if col < len(parts):
                amount = parse_amount(parts[col])
        
        if amount is None:
            debug_log.append(f"Line {i+1}: No amount found, skipping")
            continue
        
        txn["amount"] = amount
        
        # Sign (debit/credit)
        sign_cfg = fields.get("sign", {})
        is_debit = True  # Default to debit
        
        if sign_cfg.get("column") is not None:
            col = sign_cfg["column"]
            if col < len(parts):
                val = parts[col].upper().strip()
                if sign_cfg.get("credit_value") and val == sign_cfg["credit_value"].upper():
                    is_debit = False
                elif sign_cfg.get("debit_value") and val == sign_cfg["debit_value"].upper():
                    is_debit = True
                # Also check for common patterns
                elif val in ["CR", "C", "CREDIT", "DEP", "DEPOSIT"]:
                    is_debit = False
                elif val in ["DR", "D", "DEBIT", "WD", "WITHDRAWAL"]:
                    is_debit = True
        elif sign_cfg.get("default") == "credit":
            is_debit = False
        elif sign_cfg.get("debit_column") is not None and sign_cfg.get("credit_column") is not None:
            # Separate debit/credit columns
            debit_col = sign_cfg["debit_column"]
            credit_col = sign_cfg["credit_column"]
            debit_val = parse_amount(parts[debit_col]) if debit_col < len(parts) else None
            credit_val = parse_amount(parts[credit_col]) if credit_col < len(parts) else None
            if credit_val and not debit_val:
                is_debit = False
                txn["amount"] = credit_val
            elif debit_val:
                txn["amount"] = debit_val
        
        # Apply sign
        txn["signed_amount"] = -txn["amount"] if is_debit else txn["amount"]
        txn["type"] = "debit" if is_debit else "credit"
        
        # Description
        desc_cfg = fields.get("description", {})
        if desc_cfg.get("column") is not None:
            col = desc_cfg["column"]
            if col < len(parts):
                txn["description"] = parts[col]
            else:
                txn["description"] = ""
        elif desc_cfg.get("columns"):
            # Multiple columns combined
            txn["description"] = " ".join(parts[c] for c in desc_cfg["columns"] if c < len(parts))
        else:
            # Find first non-date, non-amount text
            txn["description"] = ""
            for p in parts:
                if p and not is_amount_like(p) and not is_date_like(p):
                    txn["description"] = p
                    break
        
        # Balance (optional)
        balance_cfg = fields.get("balance", {})
        if balance_cfg.get("column") is not None:
            col = balance_cfg["column"]
            if col < len(parts):
                txn["balance"] = parse_amount(parts[col])
        
        # Account (optional)
        account_cfg = fields.get("account", {})
        if account_cfg.get("column") is not None:
            col = account_cfg["column"]
            if col < len(parts):
                txn["account"] = parts[col]
        elif account_cfg.get("default"):
            txn["account"] = account_cfg["default"]
        
        # Category (optional)
        category_cfg = fields.get("category", {})
        if category_cfg.get("column") is not None:
            col = category_cfg["column"]
            if col < len(parts):
                txn["category"] = parts[col]
        
        # Custom fields
        for cf in custom_fields:
            if cf.get("column") is not None and cf["column"] < len(parts):
                txn[cf["name"]] = parts[cf["column"]]
        
        transactions.append(txn)
        debug_log.append(f"Line {i+1}: Parsed {txn['type']} ${txn['amount']:.2f}")
    
    return transactions, debug_log


def auto_parse_data(data: str) -> Tuple[List[Dict], Dict[str, Any], List[str]]:
    """
    Automatically parse data if confident, otherwise return analysis for user input.
    
    Returns: (transactions, analysis, debug_log)
    - If confident: transactions will be populated
    - If not confident: transactions will be empty, analysis.needs_user_input = True
    """
    analysis = analyze_data_structure(data)
    debug_log = []
    
    if analysis.get("error"):
        return [], analysis, [analysis["error"]]
    
    if analysis["needs_user_input"]:
        debug_log.append("Parser confidence too low - needs user input")
        return [], analysis, debug_log
    
    # Build mapping from analysis
    mapping = {
        "separator": analysis["separator"],
        "has_header": analysis["has_header"],
        "skip_lines": 0,
        "fields": {}
    }
    
    # Find each field type
    for col in analysis["columns"]:
        col_type = col["type"]
        col_idx = col["index"]
        
        if col_type == "date" and "date" not in mapping["fields"]:
            mapping["fields"]["date"] = {"column": col_idx}
        elif col_type == "debit":
            if "sign" not in mapping["fields"]:
                mapping["fields"]["sign"] = {"debit_column": col_idx}
            else:
                mapping["fields"]["sign"]["debit_column"] = col_idx
            if "amount" not in mapping["fields"]:
                mapping["fields"]["amount"] = {"column": col_idx}
        elif col_type == "credit":
            if "sign" not in mapping["fields"]:
                mapping["fields"]["sign"] = {"credit_column": col_idx}
            else:
                mapping["fields"]["sign"]["credit_column"] = col_idx
            if "amount" not in mapping["fields"]:
                mapping["fields"]["amount"] = {"column": col_idx}
        elif col_type == "amount" and "amount" not in mapping["fields"]:
            mapping["fields"]["amount"] = {"column": col_idx}
        elif col_type == "balance":
            mapping["fields"]["balance"] = {"column": col_idx}
        elif col_type in ["description", "text"] and "description" not in mapping["fields"]:
            mapping["fields"]["description"] = {"column": col_idx}
    
    # If we have separate debit/credit columns, handle sign logic
    sign_cfg = mapping["fields"].get("sign", {})
    if sign_cfg.get("debit_column") is not None and sign_cfg.get("credit_column") is not None:
        # Remove amount if it points to debit column
        if mapping["fields"].get("amount", {}).get("column") == sign_cfg["debit_column"]:
            del mapping["fields"]["amount"]
    
    # Validate we have minimum required fields
    if "amount" not in mapping["fields"] and "sign" not in mapping["fields"]:
        analysis["needs_user_input"] = True
        debug_log.append("Could not identify amount column")
        return [], analysis, debug_log
    
    transactions, parse_log = parse_with_mapping(data, mapping)
    debug_log.extend(parse_log)
    
    analysis["detected_mapping"] = mapping
    
    return transactions, analysis, debug_log


def infer_sign_from_description(description: str) -> str:
    """Infer if transaction is debit or credit from description keywords"""
    desc_upper = description.upper()
    
    credit_keywords = [
        'DEPOSIT', 'DIRECT DEP', 'CREDIT', 'REFUND', 'TRANSFER IN',
        'WIRE IN', 'ACH CREDIT', 'INTEREST', 'DIVIDEND', 'REVERSAL',
        'REBATE', 'CASHBACK', 'REIMBURSEMENT'
    ]
    
    debit_keywords = [
        'WITHDRAWAL', 'DEBIT', 'CHECK', 'PURCHASE', 'PAYMENT',
        'TRANSFER OUT', 'WIRE OUT', 'ACH DEBIT', 'FEE', 'CHARGE',
        'ATM', 'POS', 'BILL PAY'
    ]
    
    for kw in credit_keywords:
        if kw in desc_upper:
            return "credit"
    
    for kw in debit_keywords:
        if kw in desc_upper:
            return "debit"
    
    return "debit"  # Default


# Legacy compatibility functions
def parse_web_pasted_data(data: str) -> Tuple[List[Dict], List[str]]:
    """Legacy function - returns (transactions, debug_log)"""
    transactions, analysis, debug_log = auto_parse_data(data)
    
    # If auto-parse failed but we have sample data, try harder
    if not transactions and analysis.get("sample_data"):
        debug_log.append("Attempting fallback line-by-line parse...")
        transactions = []
        current_date = date.today()
        
        for line in data.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Check for standalone date
            parsed_date = parse_date(line)
            if parsed_date and len(line) < 30:
                current_date = parsed_date
                continue
            
            # Find all amounts in line
            amounts = re.findall(r'[$]?[\d,]+\.?\d*', line)
            amount = None
            for a in amounts:
                parsed = parse_amount(a)
                if parsed and parsed > 0:
                    amount = parsed
                    break
            
            if not amount:
                continue
            
            # Description is everything that's not a number
            desc = re.sub(r'[$]?[\d,]+\.?\d*', '', line).strip()
            desc = re.sub(r'\s+', ' ', desc)
            
            # Infer sign
            sign = infer_sign_from_description(desc)
            
            transactions.append({
                "date": current_date,
                "amount": amount,
                "signed_amount": -amount if sign == "debit" else amount,
                "description": desc,
                "type": sign
            })
    
    return transactions, debug_log


def ingest_bank_data(db, user_id: int, data: str, raw_file_id: str = None) -> Dict[str, Any]:
    """Ingest bank data into database"""
    from ..models import Transaction
    
    transactions, debug_log = parse_web_pasted_data(data)
    
    saved = 0
    duplicates = 0
    
    for txn in transactions:
        # Check for duplicate
        existing = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.date == txn["date"],
            Transaction.amount == txn.get("signed_amount", txn["amount"]),
            Transaction.description == txn.get("description", "")
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
        new_txn = Transaction(
            user_id=user_id,
            date=txn["date"],
            amount=txn.get("signed_amount", txn["amount"]),
            description=txn.get("description", ""),
            balance=txn.get("balance"),
            raw_file_id=raw_file_id
        )
        db.add(new_txn)
        saved += 1
    
    db.commit()
    
    return {
        "saved": saved,
        "duplicates": duplicates,
        "total_parsed": len(transactions)
    }


def ingest_bank_csv(db, user_id: int, file_content: str, filename: str) -> Dict[str, Any]:
    """Ingest CSV file"""
    return ingest_bank_data(db, user_id, file_content, raw_file_id=filename)


def ingest_quickbooks_data(db, user_id: int, qb_data: List[Dict]) -> Dict[str, Any]:
    """Ingest QuickBooks formatted data"""
    from ..models import Transaction
    
    saved = 0
    duplicates = 0
    
    for item in qb_data:
        txn_date = item.get("date")
        if isinstance(txn_date, str):
            txn_date = parse_date(txn_date) or date.today()
        
        amount = item.get("amount", 0)
        description = item.get("description") or item.get("memo") or item.get("name") or ""
        
        # Check for duplicate
        existing = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.date == txn_date,
            Transaction.amount == amount,
            Transaction.description == description
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
        new_txn = Transaction(
            user_id=user_id,
            date=txn_date,
            amount=amount,
            description=description,
            balance=item.get("balance"),
            account=item.get("account"),
            category=item.get("category"),
            raw_file_id="quickbooks"
        )
        db.add(new_txn)
        saved += 1
    
    db.commit()
    
    return {
        "saved": saved,
        "duplicates": duplicates,
        "total_parsed": len(qb_data)
    }
