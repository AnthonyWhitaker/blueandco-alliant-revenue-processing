"""
Text normalization utilities for the Alliant Revenue Processing Engine.
Extracted and generalized from guerbet_processor.py.
"""

import re
import unicodedata
from typing import Optional

from .constants import LEGAL_SUFFIXES, STATE_ABBREV, STATE_FULL_TO_ABBREV


def normalize_name(name: Optional[str]) -> str:
    """
    Normalize a facility/customer name for fuzzy matching.
    - Converts to ASCII, uppercases
    - Strips punctuation (except spaces)
    - Removes common legal suffixes (INC, LLC, LP, CORP, etc.)
    - Collapses whitespace
    """
    if not name or not isinstance(name, str):
        return ""
    # Normalize unicode to ASCII approximation
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    # Remove legal suffixes
    for suffix in LEGAL_SUFFIXES:
        text = re.sub(suffix, "", text)
    # Remove punctuation except spaces
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_zip(z: Optional[object]) -> str:
    """
    Extract a clean 5-digit ZIP code from various formats.
    Handles: '12345', '12345-6789', 12345.0, '012345', etc.
    """
    if z is None:
        return ""
    s = str(z).strip()
    # Remove trailing .0 from float representation
    s = re.sub(r"\.0$", "", s)
    # Extract first 5 digits
    digits = re.sub(r"\D", "", s)
    return digits[:5] if len(digits) >= 5 else digits


def normalize_state(state: Optional[str]) -> str:
    """
    Normalize state to full uppercase name.
    Handles both abbreviations (TX) and full names (Texas).
    """
    if not state or not isinstance(state, str):
        return ""
    s = state.strip().upper()
    if s in STATE_ABBREV:
        return STATE_ABBREV[s]
    # Already a full name
    if s in STATE_FULL_TO_ABBREV:
        return s
    # Try matching full name with leading/trailing noise
    for full in STATE_FULL_TO_ABBREV:
        if full in s:
            return full
    return s


def normalize_city(city: Optional[str]) -> str:
    """
    Normalize a city name: uppercase, strip whitespace.
    """
    if not city or not isinstance(city, str):
        return ""
    return city.strip().upper()


def normalize_key(name: Optional[str]) -> str:
    """
    Produce a canonical key for database storage and exact lookups.
    Same as normalize_name but also removes all spaces.
    Used for company_alias.canonical_key and member.canonical_key.
    """
    n = normalize_name(name)
    return re.sub(r"\s+", "", n)


def parse_date(value: Optional[object], dayfirst: bool = False) -> Optional[str]:
    """
    Parse a date value into ISO format (YYYY-MM-DD).
    Accepts datetime objects, pandas Timestamps, or date strings.
    Returns None if unparseable.
    """
    if value is None:
        return None
    import pandas as pd
    try:
        ts = pd.to_datetime(value, dayfirst=dayfirst)
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None


def first_of_month(date_str: Optional[str]) -> Optional[str]:
    """
    Given an ISO date string, return the first day of that month.
    e.g., '2025-07-15' → '2025-07-01'
    """
    if not date_str:
        return None
    try:
        from datetime import date
        parts = date_str.split("-")
        return f"{parts[0]}-{parts[1]}-01"
    except Exception:
        return None


def get_quarter_label(date_str: Optional[str]) -> Optional[str]:
    """
    Return a quarter label like '3Q2025' from an ISO date string.
    """
    if not date_str:
        return None
    try:
        from datetime import date
        parts = date_str.split("-")
        year = int(parts[0])
        month = int(parts[1])
        quarter = (month - 1) // 3 + 1
        return f"{quarter}Q{year}"
    except Exception:
        return None


def parse_deposit_amount_from_filename(filename: str) -> Optional[float]:
    """
    Extract deposit amount from a filename like:
        'Guerbet 2025.07.02 4231.37 Ledger.xlsx'
    Returns None if no amount found.

    Strategy:
    1. Strip the file extension
    2. Remove date-like tokens (YYYY.MM.DD or YYYY.MM)
    3. Find tokens matching N.NN or NN.NN or NNN.NN or NNNN.NN
    4. Return the first such token that parses as a positive float
    """
    # Strip extension
    stem = re.sub(r"\.[a-zA-Z]{2,5}$", "", filename)
    # Remove date patterns: YYYY.MM.DD or YYYY.MM (4-digit year followed by .MM)
    stem = re.sub(r"\b(19|20)\d{2}\.\d{1,2}(?:\.\d{1,2})?\b", "", stem)
    # Now find remaining decimal numbers with exactly 2 decimal places
    pattern = r"\b(\d{1,7}\.\d{2})\b"
    candidates = re.findall(pattern, stem)
    for c in candidates:
        try:
            val = float(c)
            if val > 0:
                return val
        except ValueError:
            continue
    return None
