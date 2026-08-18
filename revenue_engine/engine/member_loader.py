"""
Loads the Account List (member/facility master) into the member table.
Can be re-run to refresh the member table from a new Account List export.
"""

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from .normalizer import normalize_name, normalize_key, normalize_zip, normalize_state, normalize_city

# NOTE: member.canonical_key stores the SPACED normalized name (normalize_name output)
# so fuzzy matching (token_sort_ratio) works correctly.
# member_alias.canonical_key stores the NO-SPACES version (normalize_key output)
# for fast exact lookups. These two usages are intentionally different.


# Column names as they appear in the Account List.xlsx
# These are tried in order (case-insensitive) to handle slight name variations
_COL_NAMES = {
    "account_name":           ["account name", "name"],
    "premier_entity_code":    ["premier entity code", "pec", "premier_entity_code"],
    "alliant_entity_code":    ["alliant entity code", "alliant_entity_code", "entity code"],
    "account_id":             ["account id", "salesforce id", "facility id", "account_id"],
    "billing_city":           ["billing city", "city"],
    "billing_state":          ["billing state/province", "billing state", "state"],
    "billing_zip":            ["billing zip/postal code", "billing zip", "zip", "postal code"],
}


def _find_col(df_cols: list[str], candidates: list[str]) -> Optional[str]:
    """Find the first matching column name from a list of candidates."""
    norm_cols = {c.strip().lower(): c for c in df_cols}
    for candidate in candidates:
        if candidate.lower() in norm_cols:
            return norm_cols[candidate.lower()]
    return None


def load_account_list(
    conn: sqlite3.Connection,
    file_path: str,
    sheet_name: Optional[str] = None,
    replace_all: bool = False,
) -> tuple[int, int]:
    """
    Load the Account List XLSX into the member table.

    Args:
        conn:        Database connection
        file_path:   Path to Account List.xlsx
        sheet_name:  Sheet name (auto-detects if None)
        replace_all: If True, truncate member table first (full refresh)

    Returns:
        (inserted_count, skipped_count)
    """
    xl = pd.ExcelFile(file_path)
    if sheet_name is None:
        # Auto-detect: use first sheet whose name contains "account"
        for s in xl.sheet_names:
            if "account" in s.lower():
                sheet_name = s
                break
        if sheet_name is None:
            sheet_name = xl.sheet_names[0]

    df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
    df.dropna(how="all", inplace=True)

    cols = list(df.columns)

    def gc(key: str) -> Optional[str]:
        return _find_col(cols, _COL_NAMES[key])

    col_name    = gc("account_name")
    col_pec     = gc("premier_entity_code")
    col_alliant = gc("alliant_entity_code")
    col_acct_id = gc("account_id")
    col_city    = gc("billing_city")
    col_state   = gc("billing_state")
    col_zip     = gc("billing_zip")

    if not col_name:
        raise ValueError("Cannot find Account Name column in Account List file.")

    if replace_all:
        conn.execute("DELETE FROM member")
        conn.commit()

    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        name = str(row.get(col_name, "") or "").strip()
        if not name:
            skipped += 1
            continue

        pec     = str(row.get(col_pec, "") or "").strip() if col_pec else ""
        alliant = str(row.get(col_alliant, "") or "").strip() if col_alliant else ""
        acct_id = str(row.get(col_acct_id, "") or "").strip() if col_acct_id else None
        city    = str(row.get(col_city, "") or "").strip() if col_city else ""
        state   = str(row.get(col_state, "") or "").strip() if col_state else ""
        zip_raw = str(row.get(col_zip, "") or "").strip() if col_zip else ""

        # Determine effective entity code
        entity_code = pec if pec else alliant

        canonical = normalize_name(name)  # spaced version for fuzzy matching
        norm_city  = normalize_city(city)
        norm_state = normalize_state(state)
        norm_zip   = normalize_zip(zip_raw)

        try:
            conn.execute(
                """
                INSERT INTO member (
                    account_id, premier_entity_code, alliant_entity_code, entity_code,
                    name, canonical_key,
                    billing_city, billing_state, billing_zip,
                    norm_city, norm_state, norm_zip
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    premier_entity_code = excluded.premier_entity_code,
                    alliant_entity_code = excluded.alliant_entity_code,
                    entity_code         = excluded.entity_code,
                    name                = excluded.name,
                    canonical_key       = excluded.canonical_key,
                    billing_city        = excluded.billing_city,
                    billing_state       = excluded.billing_state,
                    billing_zip         = excluded.billing_zip,
                    norm_city           = excluded.norm_city,
                    norm_state          = excluded.norm_state,
                    norm_zip            = excluded.norm_zip,
                    updated_at          = CURRENT_TIMESTAMP
                """,
                (
                    acct_id or None,
                    pec or None,
                    alliant or None,
                    entity_code or None,
                    name,
                    canonical,
                    city or None,
                    state or None,
                    zip_raw or None,
                    norm_city or None,
                    norm_state or None,
                    norm_zip or None,
                ),
            )
            inserted += 1
        except Exception:
            skipped += 1

    conn.commit()
    return inserted, skipped
