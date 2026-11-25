"""
Text normalization utilities for the Alliant Revenue Processing Engine.
Extracted and generalized from guerbet_processor.py.
"""

import re
import unicodedata
from typing import Optional

from .address_abbreviations import ADDRESS_ABBREV, HASH_UNIT, UNITS
from .name_abbreviations import NAME_ABBREV
from .constants import LEGAL_SUFFIXES, STATE_ABBREV, STATE_FULL_TO_ABBREV


def normalize_name(name: Optional[str]) -> str:
    """
    Normalize a facility/customer name (fuzzy matching, aliases, gpo_key name comparison).
    - Converts to ASCII, uppercases, '&' -> 'AND'
    - Strips punctuation (except spaces)
    - Removes common legal suffixes (INC, LLC, LP, CORP, etc.)
    - Expands abbreviations (engine/name_abbreviations.py): HOSP -> HOSPITAL, MC -> MEDICAL CENTER, ST -> SAINT
    - Collapses whitespace
    """
    if not name or not isinstance(name, str):
        return ""
    # Normalize unicode to ASCII approximation
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.upper().replace("&", " AND ")
    # Remove legal suffixes
    for suffix in LEGAL_SUFFIXES:
        text = re.sub(suffix, "", text)
    # Remove punctuation except spaces
    text = re.sub(r"[^\w\s]", " ", text)
    # Expand abbreviations (engine/name_abbreviations.py), collapsing whitespace
    return " ".join(NAME_ABBREV.get(w, w) for w in text.split())


def normalize_address(address: Optional[object]) -> str:
    """
    Normalize a street address for comparison (member_match ship-to sites, gpo_key verification).
    - ASCII, upper case, '&' -> 'AND', punctuation becomes spaces
    - Suffixes, directions and unit words spelled one way (engine/address_abbreviations.py):
      PARKWAY -> PKWY, EAST -> E, SUITE -> STE; a bare '#' before a number is a suite ('#219' -> 'STE 219')
    """
    if address is None or (isinstance(address, float) and address != address):   # None / NaN
        return ""
    text = unicodedata.normalize("NFKD", str(address)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Z0-9#]+", " ", text.upper().replace("&", " AND ").replace("#", " # "))
    words: list[str] = []
    for w in text.split():
        if w == "#":
            if words and words[-1] in UNITS.values():   # 'STE #5', 'APT #5': the unit word already says it
                continue
            w = HASH_UNIT
        words.append(ADDRESS_ABBREV.get(w, w))
    return " ".join(words)


def normalize_zip(z: Optional[object]) -> str:
    """
    A 5-digit ZIP code from the usual spellings: '12345', '12345-6789', 12345.0, '123456789'.
    Leading zeros Excel dropped are put back: 2134 -> '02134', '21341234' (ZIP+4) -> '02134'.
    Anything without digits (blank, 'N/A') -> ''.
    """
    if z is None or (isinstance(z, float) and z != z):
        return ""
    s = str(z).strip()
    if re.fullmatch(r"\d+\.0+", s):               # 12345.0 from a float cell
        s = s.split(".")[0]
    digits = re.match(r"\d+", s)                    # the first run: '78701-1234' -> '78701'
    if not digits:
        return ""
    d = digits.group()
    if len(d) <= 5:
        return d.zfill(5)
    if len(d) <= 9:                                  # ZIP+4 written without the dash
        return d.zfill(9)[:5]
    return d[:5]


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


def parse_amount(value: Optional[object]) -> Optional[float]:
    """
    A money/number cell as a float; None when blank or not a number.
    Accepts what Excel shows for currency typed in as text: '$1,234.50', ' $26.00 ',
    '-$26.00', '$-26.00', accounting negatives '(1,234.50)' and a trailing minus '26.00-'.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if value != value else float(value)   # NaN
    s = str(value).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    elif s.endswith("-"):
        negative, s = True, s[:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    if f != f:   # NaN
        return None
    return -f if negative else f


def quarter_start(timeframe: Optional[str]) -> Optional[str]:
    """'3Q26' -> '2026-07-01' (first day of the quarter); None for blank or anything not XQYY."""
    m = re.fullmatch(r"([1-4])Q(\d{2})", (timeframe or "").strip().upper())
    if not m:
        return None
    return f"20{m.group(2)}-{(int(m.group(1)) - 1) * 3 + 1:02d}-01"


def parse_date(value: Optional[object], dayfirst: bool = False) -> Optional[str]:
    """
    Parse a date value into ISO format (YYYY-MM-DD).
    Accepts datetime objects, pandas Timestamps, or date strings.
    Returns None if unparseable.
    """
    if value is None:
        return None
    import pandas as pd
    # Handle quarter-label strings like "Q4 2025" / "4Q2025" (Harbinger, Zier)
    if isinstance(value, str):
        m = re.match(r"^\s*Q?([1-4])\s*Q?\s*[- ]?\s*(\d{4})\s*$", value.strip(), re.IGNORECASE)
        if m:
            quarter, year = int(m.group(1)), int(m.group(2))
            month = (quarter - 1) * 3 + 1
            return f"{year}-{month:02d}-01"
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


def parse_date_from_filename(filename: str) -> Optional[str]:
    """
    Extract a date from a filename like:
        'Guerbet 2025.07.02 4231.37 Ledger.xlsx'
    Looks for a YYYY.MM.DD or YYYY-MM-DD token.
    Returns an ISO date string (YYYY-MM-DD), or None if not found/invalid.
    """
    m = re.search(r"\b((?:19|20)\d{2})[.\-](\d{1,2})[.\-](\d{1,2})\b", filename)
    if not m:
        return None
    try:
        from datetime import date
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def guess_supplier_from_filename(filename: str, supplier_names: list[str]) -> Optional[str]:
    """
    Guess which supplier a filename belongs to by matching the filename
    stem against known supplier profile names.

    Prefers an exact case-insensitive prefix match (e.g. 'Guerbet 2025...'
    -> 'Guerbet'); falls back to fuzzy partial-ratio matching (threshold 80).
    Returns the matched supplier name, or None if no confident match found.
    """
    from thefuzz import fuzz

    stem = re.sub(r"\.[a-zA-Z]{2,5}$", "", filename).strip().lower()

    for name in supplier_names:
        if stem.startswith(name.strip().lower()):
            return name

    best_name, best_score = None, 0
    for name in supplier_names:
        score = fuzz.partial_ratio(name.strip().lower(), stem)
        if score > best_score:
            best_score, best_name = score, name

    return best_name if best_score >= 80 else None


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
