"""
Transaction Ingestion Service - Intelligent Parser

This parser analyzes pasted data to discover its structure, then builds
custom extraction rules. It handles messy web-copied bank data with various formats.

Strategy:
1. Analyze the entire dataset to detect patterns
2. Identify columns: which have dates? amounts? descriptions?
3. Detect debit/credit column layout vs single amount column
4. Handle date headers vs inline dates
5. Extract transactions using discovered rules
"""
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple
import re
from decimal import Decimal, InvalidOperation


# ============================================================================
# PATTERN DEFINITIONS
# ============================================================================

# Date patterns - ordered by specificity
DATE_PATTERNS = [
    # Full formats with year
    (r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', 'MDY'),           # 01/15/2026
    (r'\b(\d{1,2})-(\d{1,2})-(\d{4})\b', 'MDY'),           # 01-15-2026
    (r'\b(\d{4})/(\d{1,2})/(\d{1,2})\b', 'YMD'),           # 2026/01/15
    (r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', 'YMD'),           # 2026-01-15
    # Month name formats
    (r'\b([A-Z]{3})\s+(\d{1,2}),?\s*(\d{4})\b', 'MnDY'),   # JAN 15, 2026
    (r'\b(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\b', 'DMnY'),     # 15 JAN 2026
    (r'\b([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\b', 'MnDY'),     # JAN 15 2026
    # Short formats (assume current/recent year)
    (r'\b(\d{1,2})/(\d{1,2})/(\d{2})\b', 'MDy'),           # 01/15/26
    (r'\b(\d{1,2})-(\d{1,2})-(\d{2})\b', 'MDy'),           # 01-15-26
    (r'\b([A-Z]{3})\s+(\d{1,2})\b', 'MnD'),                # JAN 15
]

MONTH_NAMES = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4, 'JUNE': 6,
    'JULY': 7, 'AUGUST': 8, 'SEPTEMBER': 9, 'OCTOBER': 10, 'NOVEMBER': 11, 'DECEMBER': 12
}

# Amount pattern - matches monetary values
AMOUNT_PATTERN = re.compile(r'^[\$\-\+]?\s*[\(\-]?\s*\$?\s*[\d,]+\.?\d*\s*\)?$')
AMOUNT_EXTRACT = re.compile(r'[\(\-]?\s*\$?\s*([\d,]+\.?\d*)\s*\)?')


# ============================================================================
# STRUCTURE ANALYSIS
# ============================================================================

def analyze_data_structure(raw_data: str) -> Dict[str, Any]:
    """
    Analyze the pasted data to discover its structure.
    Returns information about columns, separators, date positions, etc.
    """
    analysis = {
        'separator': None,           # 'tab', 'comma', 'multi-space', 'mixed'
        'has_header_row': False,     # First row is column headers
        'date_style': None,          # 'header', 'inline', 'column'
        'column_count': 0,
        'column_types': [],          # List of detected types per column
        'amount_columns': [],        # Indices of amount columns
        'description_column': None,  # Index of description column
        'date_column': None,         # Index if dates are in a column
        'balance_column': None,      # Index of balance column
        'debit_credit_layout': False, # True if separate debit/credit columns
        'sample_rows': [],           # Sample parsed rows for debugging
    }
    
    lines = [l for l in raw_data.strip().split('\n') if l.strip()]
    if not lines:
        return analysis
    
    # Step 1: Detect separator
    analysis['separator'] = detect_separator(lines)
    
    # Step 2: Split lines into columns
    rows = []
    for line in lines:
        cols = split_line(line, analysis['separator'])
        if cols:
            rows.append(cols)
    
    if not rows:
        return analysis
    
    # Step 3: Detect if first row is headers
    if rows:
        analysis['has_header_row'] = is_header_row(rows[0])
    
    # Step 4: Get hints from headers if available
    header_hints = {}
    if analysis['has_header_row'] and rows:
        header_hints = get_column_hints_from_headers(rows[0])
    
    # Step 5: Analyze each column across all rows
    data_rows = rows[1:] if analysis['has_header_row'] else rows
    if data_rows:
        max_cols = max(len(r) for r in data_rows)
        analysis['column_count'] = max_cols
        analysis['column_types'] = analyze_columns(data_rows, max_cols)
        
        # Override with header hints where available
        for col_idx, hint_type in header_hints.items():
            if col_idx < len(analysis['column_types']):
                if hint_type == 'debit':
                    analysis['column_types'][col_idx] = 'amount'
                    if col_idx not in analysis['amount_columns']:
                        analysis['amount_columns'].append(col_idx)
                    analysis['debit_credit_layout'] = True
                elif hint_type == 'credit':
                    analysis['column_types'][col_idx] = 'amount'
                    if col_idx not in analysis['amount_columns']:
                        analysis['amount_columns'].append(col_idx)
                    analysis['debit_credit_layout'] = True
                else:
                    analysis['column_types'][col_idx] = hint_type
        
        # Sort amount columns for consistent debit/credit ordering
        analysis['amount_columns'].sort()
        
        # Identify specific columns (after hint processing)
        for i, col_type in enumerate(analysis['column_types']):
            if col_type == 'date' and analysis['date_column'] is None:
                analysis['date_column'] = i
            elif col_type == 'amount' and i not in analysis['amount_columns']:
                analysis['amount_columns'].append(i)
            elif col_type == 'description':
                if analysis['description_column'] is None:
                    analysis['description_column'] = i
            elif col_type == 'balance' and analysis['balance_column'] is None:
                analysis['balance_column'] = i
    
    # Step 5: Check for date headers (standalone date lines)
    analysis['date_style'] = detect_date_style(lines, analysis)
    
    # Step 6: Check for debit/credit layout
    if len(analysis['amount_columns']) >= 2:
        analysis['debit_credit_layout'] = detect_debit_credit_layout(data_rows, analysis['amount_columns'])
    
    # Sample rows for debugging
    analysis['sample_rows'] = rows[:5]
    
    return analysis


def detect_separator(lines: List[str]) -> str:
    """Detect what separator is used in the data."""
    tab_count = sum(1 for l in lines if '\t' in l)
    space_count = sum(1 for l in lines if '  ' in l)  # Multi-space
    
    # Count commas that are field separators (not in numbers)
    # A field-separator comma has non-digit on at least one side
    def count_field_commas(line: str) -> int:
        count = 0
        for i, c in enumerate(line):
            if c == ',':
                before = line[i-1] if i > 0 else ''
                after = line[i+1] if i < len(line)-1 else ''
                # If comma is not between digits, it's a field separator
                if not (before.isdigit() and after.isdigit()):
                    count += 1
        return count
    
    # Lines with 2+ field commas are CSV
    csv_lines = sum(1 for l in lines if count_field_commas(l) >= 2)
    
    total = len(lines)
    if total == 0:
        return 'space'
    
    # If most lines have tabs, use tab separator
    if tab_count / total > 0.5:
        return 'tab'
    # If most lines have multiple field commas, use comma
    if csv_lines / total > 0.5:
        return 'comma'
    # If most lines have multi-spaces, use that
    if space_count / total > 0.3:
        return 'multi-space'
    
    return 'space'


def split_line(line: str, separator: str) -> List[str]:
    """Split a line based on detected separator."""
    if separator == 'tab':
        return [c.strip() for c in line.split('\t')]
    elif separator == 'comma':
        # Handle CSV properly (quoted strings may contain commas)
        parts = []
        current = ''
        in_quotes = False
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                parts.append(current.strip().strip('"'))
                current = ''
            else:
                current += char
        parts.append(current.strip().strip('"'))
        return parts
    elif separator == 'multi-space':
        # Split on 2+ spaces, but be smarter about it
        # First try 3+ spaces (more reliable field separator)
        parts = re.split(r'\s{3,}', line.strip())
        if len(parts) >= 3:
            # Check for suspicious large gaps that might indicate empty columns
            result = []
            for p in parts:
                p = p.strip()
                if p:
                    # Check if this part has a large internal gap (empty column merged)
                    # Pattern: "number    number" with many spaces between
                    gap_match = re.search(r'(\d[\d,\.]*)\s{6,}(\d[\d,\.]*)', p)
                    if gap_match:
                        # Split this into separate values with empty between
                        before = p[:gap_match.start()] + gap_match.group(1)
                        after = gap_match.group(2) + p[gap_match.end():]
                        result.append(before.strip())
                        result.append('')  # Empty column
                        result.append(after.strip())
                    else:
                        result.append(p)
            return result
        # Fall back to 2+ spaces
        parts = re.split(r'\s{2,}', line.strip())
        return [p.strip() for p in parts if p.strip()]
    else:
        # Single space - return whole line for further processing
        return [line.strip()]


def is_header_row(row: List[str]) -> bool:
    """Check if a row looks like column headers."""
    header_keywords = {'date', 'description', 'amount', 'debit', 'credit', 'balance', 
                      'memo', 'transaction', 'type', 'category', 'check', 'ref',
                      'withdrawal', 'deposit', 'posted', 'effective', 'payee', 'name'}
    
    # If most cells match header keywords, it's a header row
    matches = sum(1 for cell in row if cell.lower().strip() in header_keywords)
    return matches >= 2 or (len(row) > 0 and matches / len(row) > 0.4)


def get_column_hints_from_headers(header_row: List[str]) -> Dict[str, int]:
    """Extract column type hints from header names."""
    hints = {}
    header_map = {
        'date': 'date', 'posted': 'date', 'effective': 'date',
        'description': 'description', 'memo': 'description', 'payee': 'description', 'name': 'description',
        'debit': 'debit', 'withdrawal': 'debit', 'payment': 'debit',
        'credit': 'credit', 'deposit': 'credit',
        'amount': 'amount',
        'balance': 'balance', 'running': 'balance',
    }
    
    for i, header in enumerate(header_row):
        header_lower = header.lower().strip()
        for keyword, col_type in header_map.items():
            if keyword in header_lower:
                hints[i] = col_type
                break
    
    return hints


def analyze_columns(rows: List[List[str]], max_cols: int) -> List[str]:
    """Analyze each column to determine its type."""
    column_types = []
    
    for col_idx in range(max_cols):
        values = []
        for row in rows:
            if col_idx < len(row):
                values.append(row[col_idx])
        
        col_type = detect_column_type(values, col_idx, max_cols)
        column_types.append(col_type)
    
    return column_types


def detect_column_type(values: List[str], col_idx: int, total_cols: int) -> str:
    """Determine what type of data is in this column."""
    if not values:
        return 'unknown'
    
    non_empty = [v for v in values if v.strip()]
    total_values = len(values)
    empty_count = total_values - len(non_empty)
    
    if not non_empty:
        return 'empty'
    
    # Count how many look like dates
    date_count = sum(1 for v in non_empty if looks_like_date(v))
    
    # Count how many look like amounts
    amount_count = sum(1 for v in non_empty if looks_like_amount(v))
    
    # Count how many are purely text (descriptions)
    text_count = sum(1 for v in non_empty if looks_like_description(v))
    
    total = len(non_empty)
    
    # If most are dates, it's a date column
    if date_count / total > 0.7:
        return 'date'
    
    # If most are amounts, check if it's balance (usually last) or amount
    if amount_count / total > 0.7:
        # Last column with amounts is often balance (largest values)
        if col_idx == total_cols - 1:
            return 'balance'
        return 'amount'
    
    # If most are text, it's a description
    if text_count / total > 0.5:
        return 'description'
    
    # Sparse column with some amounts and many empties = debit or credit column
    if amount_count > 0:
        # If more than 30% empty and some amounts, it's likely a debit/credit column
        if empty_count / total_values > 0.3:
            return 'amount'
        # If amounts exist but not dominant, still might be amount column
        if amount_count / total > 0.3:
            if col_idx == total_cols - 1:
                return 'balance'
            return 'amount'
    
    return 'unknown'


def looks_like_date(s: str) -> bool:
    """Check if string looks like a date."""
    s = s.strip().upper()
    for pattern, _ in DATE_PATTERNS:
        if re.search(pattern, s, re.IGNORECASE):
            return True
    return False


def looks_like_amount(s: str) -> bool:
    """Check if string looks like a monetary amount."""
    s = s.strip()
    if not s:
        return False
    # Remove currency symbols and check if it's a number
    cleaned = re.sub(r'[\$,\(\)\-\+\s]', '', s)
    try:
        float(cleaned)
        return True
    except:
        return False


def looks_like_description(s: str) -> bool:
    """Check if string looks like a transaction description."""
    s = s.strip()
    if not s:
        return False
    # Descriptions typically have letters and are longer
    if len(s) < 3:
        return False
    # Should have some letters
    if not re.search(r'[A-Za-z]', s):
        return False
    # Should not be mostly numbers
    digits = sum(1 for c in s if c.isdigit())
    if digits / len(s) > 0.5:
        return False
    return True


def detect_date_style(lines: List[str], analysis: Dict) -> str:
    """Detect how dates appear in the data."""
    # Check for standalone date lines (date headers)
    date_only_lines = 0
    for line in lines:
        line = line.strip()
        if looks_like_date(line) and len(line) < 30:
            # Line is primarily a date
            date_only_lines += 1
    
    if date_only_lines > 0:
        return 'header'
    
    if analysis['date_column'] is not None:
        return 'column'
    
    return 'inline'


def detect_debit_credit_layout(rows: List[List[str]], amount_cols: List[int]) -> bool:
    """
    Detect if amount columns are debit/credit (one filled, one empty per row).
    """
    if len(amount_cols) < 2:
        return False
    
    # Check if for each row, typically only one amount column has a value
    exclusive_count = 0
    for row in rows:
        values = []
        for col_idx in amount_cols[:2]:  # Check first two amount columns
            if col_idx < len(row):
                val = row[col_idx].strip()
                values.append(bool(val and looks_like_amount(val)))
            else:
                values.append(False)
        
        # XOR - exactly one should be true
        if values[0] != values[1]:
            exclusive_count += 1
    
    # If most rows have exclusive values, it's debit/credit layout
    return exclusive_count / max(len(rows), 1) > 0.6


# ============================================================================
# DATE PARSING
# ============================================================================

def parse_date(s: str, year_hint: int = None) -> Optional[date]:
    """Parse a date string into a date object."""
    s = s.strip().upper()
    
    if year_hint is None:
        year_hint = datetime.now().year
    
    for pattern, format_type in DATE_PATTERNS:
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            groups = match.groups()
            try:
                if format_type == 'MDY':
                    return date(int(groups[2]), int(groups[0]), int(groups[1]))
                elif format_type == 'YMD':
                    return date(int(groups[0]), int(groups[1]), int(groups[2]))
                elif format_type == 'MnDY':
                    month = MONTH_NAMES.get(groups[0].upper()[:3], 1)
                    return date(int(groups[2]), month, int(groups[1]))
                elif format_type == 'DMnY':
                    month = MONTH_NAMES.get(groups[1].upper()[:3], 1)
                    return date(int(groups[2]), month, int(groups[0]))
                elif format_type == 'MDy':
                    year = int(groups[2])
                    year = year + 2000 if year < 100 else year
                    return date(year, int(groups[0]), int(groups[1]))
                elif format_type == 'MnD':
                    month = MONTH_NAMES.get(groups[0].upper()[:3], 1)
                    day = int(groups[1])
                    return date(year_hint, month, day)
            except (ValueError, KeyError):
                continue
    
    return None


# ============================================================================
# AMOUNT PARSING
# ============================================================================

def parse_amount(s: str) -> Optional[float]:
    """Parse an amount string into a float."""
    if not s or not s.strip():
        return None
    
    s = s.strip()
    
    # Detect negative indicators
    is_negative = False
    if '(' in s and ')' in s:  # Accounting format (1,234.56)
        is_negative = True
    if s.startswith('-') or s.endswith('-'):
        is_negative = True
    if s.upper().startswith('DR') or 'DEBIT' in s.upper():
        is_negative = True
    
    # Detect positive indicators
    if s.startswith('+'):
        is_negative = False
    if s.upper().startswith('CR') or 'CREDIT' in s.upper():
        is_negative = False
    
    # Extract the numeric value
    match = AMOUNT_EXTRACT.search(s)
    if match:
        num_str = match.group(1).replace(',', '')
        try:
            value = float(num_str)
            return -value if is_negative else value
        except ValueError:
            pass
    
    return None


def infer_sign_from_description(description: str) -> int:
    """
    Infer whether a transaction is debit (-1) or credit (+1) from description keywords.
    Returns 0 if unsure.
    """
    desc_upper = description.upper()
    
    # Strong debit indicators (money going out)
    debit_keywords = [
        'DEBIT', 'WITHDRAWAL', 'PAYMENT', 'CHECK ', 'CHK ', 'BILL PAY',
        'ACH DEBIT', 'WIRE OUT', 'TRANSFER OUT', 'PURCHASE', 'POS ',
        'ATM WITHDRAWAL', 'FEE ', ' FEE', 'CHARGE', 'EXPENSE', 'PAID',
        'VISA ', 'MASTERCARD', 'AMEX ', 'DDA PUR', 'BILL ',
        'ELECTRIC', 'GAS ', 'WATER ', 'RENT ', 'INSURANCE', 'TAX ',
        'UTILITY', 'PHONE ', 'INTERNET', 'SUBSCRIPTION'
    ]
    
    # Strong credit indicators (money coming in)
    credit_keywords = [
        'CREDIT', 'DEPOSIT', 'ACH CREDIT', 'WIRE IN', 'TRANSFER IN',
        'DIRECT DEP', 'PAYROLL', 'REFUND', 'REVERSAL', 'REBATE',
        'INTEREST', 'DIVIDEND', 'RECEIVED', 'INCOMING', 'REVENUE',
        'CLIENT REV', 'CUSTOMER', 'SALE', ' IN ', 'WIRE FROM'
    ]
    
    # Check credits first (more specific patterns)
    for kw in credit_keywords:
        if kw in desc_upper:
            return 1
    
    for kw in debit_keywords:
        if kw in desc_upper:
            return -1
    
    return 0


# ============================================================================
# MAIN PARSING FUNCTION
# ============================================================================

def parse_web_pasted_data(raw_data: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Main entry point: Parse messy web-pasted bank data.
    
    Returns:
        - List of transactions: [{date, amount, description, balance?}, ...]
        - List of debug messages
    """
    debug = []
    transactions = []
    
    if not raw_data or not raw_data.strip():
        return [], ['No data provided']
    
    # Step 1: Analyze structure
    analysis = analyze_data_structure(raw_data)
    debug.append(f"STRUCTURE: separator={analysis['separator']}, cols={analysis['column_count']}")
    debug.append(f"COLUMNS: {analysis['column_types']}")
    debug.append(f"DATE STYLE: {analysis['date_style']}, DEBIT/CREDIT: {analysis['debit_credit_layout']}")
    debug.append(f"DESC COL: {analysis['description_column']}, AMT COLS: {analysis['amount_columns']}, BAL COL: {analysis['balance_column']}")
    
    # Step 2: Parse based on detected structure
    lines = raw_data.strip().split('\n')
    current_date = None
    year_hint = datetime.now().year
    
    # Skip header if detected
    start_idx = 1 if analysis['has_header_row'] else 0
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        # Check if this line is a date header
        if analysis['date_style'] == 'header' and looks_like_date(line) and len(line) < 30:
            parsed_date = parse_date(line, year_hint)
            if parsed_date:
                current_date = parsed_date
                debug.append(f"Line {i}: DATE HEADER -> {current_date}")
                continue
        
        # Skip if this is the header row
        if i == 0 and analysis['has_header_row']:
            debug.append(f"Line {i}: SKIPPED (header row)")
            continue
        
        # Parse the line
        txn = parse_transaction_row(line, analysis, current_date, year_hint)
        
        if txn:
            transactions.append(txn)
            sign = '+' if txn['amount'] >= 0 else ''
            debug.append(f"Line {i}: PARSED: {txn['description'][:30]} = {sign}${txn['amount']:.2f}")
        else:
            if len(line) > 10:  # Only log significant lines
                debug.append(f"Line {i}: SKIPPED: {line[:50]}")
    
    debug.append(f"TOTAL: {len(transactions)} transactions parsed")
    
    return transactions, debug


def parse_transaction_row(line: str, analysis: Dict, current_date: date, year_hint: int) -> Optional[Dict[str, Any]]:
    """
    Parse a single transaction row using the detected structure.
    """
    # Split the line
    cols = split_line(line, analysis['separator'])
    
    if not cols or len(cols) == 0:
        return None
    
    # If we only got one column or very few columns, try aggressive parsing
    if len(cols) <= 2:
        return parse_single_column_line(line, current_date, year_hint)
    
    # Extract components based on detected structure
    txn_date = current_date
    description = None
    amount = None
    balance = None
    
    # Get date from column if detected
    if analysis['date_column'] is not None and analysis['date_column'] < len(cols):
        col_text = cols[analysis['date_column']]
        parsed = parse_date(col_text, year_hint)
        if parsed:
            txn_date = parsed
            # Check if this column also contains description (date + text)
            # Extract the non-date part
            remaining = col_text
            for pattern, _ in DATE_PATTERNS:
                match = re.match(pattern, col_text, re.IGNORECASE)
                if match:
                    remaining = col_text[match.end():].strip()
                    break
            if remaining and looks_like_description(remaining):
                description = remaining
    
    # Get description from dedicated column if we don't have one yet
    if not description:
        if analysis['description_column'] is not None and analysis['description_column'] < len(cols):
            description = cols[analysis['description_column']].strip()
        else:
            # Find the first text column that isn't date or amount
            for i, col in enumerate(cols):
                col_clean = col.strip()
                if not col_clean:
                    continue
                # Skip if it's purely a date
                if looks_like_date(col_clean) and len(col_clean) < 15:
                    continue
                # Skip if it's purely an amount
                if looks_like_amount(col_clean):
                    continue
                # Check if it has a date prefix we should strip
                for pattern, _ in DATE_PATTERNS:
                    match = re.match(pattern, col_clean, re.IGNORECASE)
                    if match:
                        col_clean = col_clean[match.end():].strip()
                        break
                if col_clean and len(col_clean) > 2:
                    description = col_clean
                    break
    
    # Get amount - handle debit/credit layout
    if analysis['debit_credit_layout'] and len(analysis['amount_columns']) >= 2:
        debit_col = analysis['amount_columns'][0]
        credit_col = analysis['amount_columns'][1]
        
        debit_val = None
        credit_val = None
        
        if debit_col < len(cols):
            debit_val = parse_amount(cols[debit_col])
        if credit_col < len(cols):
            credit_val = parse_amount(cols[credit_col])
        
        if debit_val is not None and debit_val > 0:
            amount = -abs(debit_val)  # Debits are negative (money out)
        elif credit_val is not None and credit_val > 0:
            amount = abs(credit_val)  # Credits are positive (money in)
        elif debit_val is not None:
            amount = -abs(debit_val)
        elif credit_val is not None:
            amount = abs(credit_val)
    else:
        # Single amount column or first amount found
        for col_idx in analysis['amount_columns']:
            if col_idx < len(cols):
                parsed = parse_amount(cols[col_idx])
                if parsed is not None:
                    amount = parsed
                    break
        
        # If still no amount, scan all columns
        if amount is None:
            for col in cols:
                if looks_like_amount(col) and not looks_like_date(col):
                    parsed = parse_amount(col)
                    if parsed is not None:
                        amount = parsed
                        break
    
    # Get balance
    if analysis['balance_column'] is not None and analysis['balance_column'] < len(cols):
        balance = parse_amount(cols[analysis['balance_column']])
    
    # Validate we have minimum required data
    if amount is None:
        return None
    
    if not description:
        # Last resort: build from available columns
        desc_parts = []
        for i, col in enumerate(cols):
            if i != analysis['balance_column'] and i not in analysis['amount_columns']:
                col_clean = col.strip()
                # Skip pure dates
                if looks_like_date(col_clean) and len(col_clean) < 15:
                    continue
                if col_clean:
                    desc_parts.append(col_clean)
        description = ' '.join(desc_parts) if desc_parts else 'Transaction'
    
    # If amount doesn't have a clear sign and we don't have debit/credit layout,
    # try to infer sign from description
    if amount > 0 and not analysis['debit_credit_layout']:
        inferred_sign = infer_sign_from_description(description)
        if inferred_sign == -1:
            amount = -abs(amount)
    
    if txn_date is None:
        txn_date = date.today()
    
    result = {
        'date': txn_date.isoformat(),
        'amount': round(amount, 2),
        'description': description,
    }
    
    if balance is not None:
        result['balance'] = round(balance, 2)
    
    return result


def parse_single_column_line(line: str, current_date: date, year_hint: int) -> Optional[Dict[str, Any]]:
    """
    Parse a line that came as a single column (no clear separators).
    Try to extract date, description, and amount from the text.
    """
    # Try to find a date at the start
    txn_date = current_date
    remaining = line
    
    for pattern, format_type in DATE_PATTERNS:
        match = re.match(pattern, line, re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(0), year_hint)
            if parsed:
                txn_date = parsed
                remaining = line[match.end():].strip()
                break
    
    # Find amounts - look for monetary patterns
    # Pattern: optional sign, optional $, digits with optional commas, optional decimal
    amount_pattern = r'[\$\-\+]?\s*\(?\s*\$?\s*[\d,]+\.?\d{0,2}\s*\)?'
    
    amounts = []
    amount_spans = []
    for match in re.finditer(amount_pattern, remaining):
        text = match.group().strip()
        # Skip if it's just a small number that might be part of description
        if not re.search(r'[\$\(\)]', text):  # No currency indicators
            # Must have significant digits or decimal point
            cleaned = re.sub(r'[,\s\-\+]', '', text)
            if len(cleaned) < 2 or (len(cleaned) < 4 and '.' not in text):
                continue
        
        val = parse_amount(text)
        if val is not None and abs(val) >= 0.01:
            amounts.append(val)
            amount_spans.append((match.start(), match.end()))
    
    if not amounts:
        return None
    
    # Last amount is likely balance if there are multiple and it's larger
    if len(amounts) >= 2 and abs(amounts[-1]) > abs(amounts[0]) * 2:
        amount = amounts[0]
        balance = amounts[-1]
        desc_end = amount_spans[0][0]
    elif len(amounts) >= 2:
        # First amount is transaction, last is balance
        amount = amounts[0]
        balance = amounts[-1]
        desc_end = amount_spans[0][0]
    else:
        amount = amounts[0]
        balance = None
        desc_end = amount_spans[0][0]
    
    # Description is everything before the first amount
    description = remaining[:desc_end].strip()
    
    # Clean up description - remove trailing punctuation and spaces
    description = re.sub(r'[\s\-_\.]+$', '', description)
    description = re.sub(r'\s+', ' ', description).strip()
    
    if not description:
        description = 'Transaction'
    
    if txn_date is None:
        txn_date = date.today()
    
    result = {
        'date': txn_date.isoformat(),
        'amount': round(amount, 2),
        'description': description,
    }
    
    if balance is not None:
        result['balance'] = round(balance, 2)
    
    return result


# ============================================================================
# DUPLICATE DETECTION
# ============================================================================

def filter_duplicates(new_txns: List[Dict], existing_txns: List[Dict]) -> List[Dict]:
    """Filter out transactions that already exist."""
    # Create a set of (date, amount, description_prefix) for existing
    existing_set = set()
    for txn in existing_txns:
        key = (
            txn.get('date', ''),
            round(txn.get('amount', 0), 2),
            txn.get('description', '')[:20].upper()
        )
        existing_set.add(key)
    
    filtered = []
    for txn in new_txns:
        key = (
            txn.get('date', ''),
            round(txn.get('amount', 0), 2),
            txn.get('description', '')[:20].upper()
        )
        if key not in existing_set:
            filtered.append(txn)
    
    return filtered
