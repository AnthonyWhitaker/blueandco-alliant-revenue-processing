"""
Supplier file reader for the Revenue Processing Engine.

Reads raw supplier files (XLSX, CSV) and maps their columns to canonical
field names using the supplier's column map from the database.

Handles:
- Column name normalization (case, whitespace)
- Multiple possible column names for the same field (via supplier_column_map)
- Header row offset (some suppliers put data on row 2+)
- Sheet name selection
- Summary row stripping (rows where a required column is NaN)
"""

from pathlib import Path
from typing import Optional

import pandas as pd


def _normalize_col(name: str) -> str:
    """Normalize a column name for comparison: strip, lowercase, collapse spaces."""
    return " ".join(str(name).strip().lower().split())


def read_raw_grid(file_path: str, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """
    Read a sheet with no header interpretation — every cell as a grid position
    (row index, column index). Used by pivot_extractor.py for Tier C
    aggregate/pivot workpapers, where headers/labels can be anywhere on the
    sheet rather than a fixed header row.
    """
    return pd.read_excel(file_path, sheet_name=sheet_name or 0, header=None)


_PATTERN_PREFIX = "contains:"


def load_supplier_column_map(
    conn: object,
    supplier_profile_id: int,
) -> dict[str, str]:
    """
    Load the column map for a supplier as {source_column_normalized: canonical_field}.

    Exact-match entries only. Entries whose source_column starts with
    "contains:" (see load_supplier_column_map_patterns) are excluded here.
    """
    rows = conn.execute(
        "SELECT source_column, canonical_field FROM supplier_column_map WHERE supplier_profile_id = ?",
        (supplier_profile_id,),
    ).fetchall()
    return {
        _normalize_col(r["source_column"]): r["canonical_field"]
        for r in rows
        if not str(r["source_column"]).strip().lower().startswith(_PATTERN_PREFIX)
    }


def load_supplier_column_map_patterns(
    conn: object,
    supplier_profile_id: int,
) -> list[tuple[str, str]]:
    """
    Load "contains:" pattern rules for a supplier as [(substring_normalized, canonical_field), ...].

    Used for supplier files whose real header text changes between deposits
    in a way exact-name mapping can't handle (e.g. Labcorp's revenue column
    is literally named "3Q 2025 Total Revenue", "1Q 2026 Total Revenue", ...
    — the quarter/year label is baked into the column name every period).
    A column map row with source_column = "contains:Total Revenue" matches
    any raw column whose normalized text contains "total revenue".
    """
    rows = conn.execute(
        "SELECT source_column, canonical_field FROM supplier_column_map WHERE supplier_profile_id = ?",
        (supplier_profile_id,),
    ).fetchall()
    patterns = []
    for r in rows:
        raw = str(r["source_column"]).strip()
        if raw.lower().startswith(_PATTERN_PREFIX):
            substring = _normalize_col(raw[len(_PATTERN_PREFIX):])
            patterns.append((substring, r["canonical_field"]))
    return patterns


def read_supplier_file(
    file_path: str,
    supplier_profile: dict,
    conn: object,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Read a raw supplier file and return:
        (df_canonical, warnings)

    df_canonical: DataFrame with canonical field names as columns.
                  Only canonical columns that exist in the file are present.
                  All original columns are also retained with prefix 'raw_'.
    warnings:     List of warning strings (missing optional columns, etc.)

    Raises ValueError if required canonical fields are missing.
    """
    path = Path(file_path)
    supplier_id = supplier_profile["id"]
    header_row = supplier_profile.get("header_row") or 0
    sheet_name = supplier_profile.get("sheet_name") or 0
    skip_col = supplier_profile.get("skip_rows_where_col_null")
    max_read_columns = supplier_profile.get("max_read_columns")

    warnings: list[str] = []

    # Build read kwargs
    read_kwargs = dict(sheet_name=sheet_name, header=header_row, dtype=str)
    # max_read_columns is a hint to avoid reading 16K stray-fill columns,
    # but only applies if the file actually has that many columns — some
    # months the file is narrower (e.g. Carolon Ledger_1/2 vs Ledger_3).
    # We'll try with usecols first; if it fails, retry without.
    _usecols = None
    if max_read_columns:
        _usecols = range(0, int(max_read_columns))

    # Read the raw file
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        try:
            kw = {**read_kwargs, "usecols": _usecols} if _usecols else read_kwargs
            try:
                df = pd.read_excel(file_path, **kw)
            except ValueError as e:
                if _usecols and "out-of-bounds" in str(e):
                    df = pd.read_excel(file_path, **read_kwargs)
                else:
                    raise
        except Exception as e:
            raise ValueError(f"Cannot read Excel file: {e}") from e
    elif suffix == ".csv":
        try:
            df = pd.read_csv(file_path, header=header_row, dtype=str)
        except Exception as e:
            raise ValueError(f"Cannot read CSV file: {e}") from e
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    # Strip completely empty rows and columns
    df.dropna(how="all", inplace=True)
    df.dropna(axis=1, how="all", inplace=True)

    # Strip summary rows (e.g., Guerbet uses NaN JDE Number for totals)
    if skip_col:
        norm_skip = _normalize_col(skip_col)
        # Find the raw column that matches skip_col
        match_col = None
        for c in df.columns:
            if _normalize_col(str(c)) == norm_skip:
                match_col = c
                break
        if match_col:
            df = df[df[match_col].notna() & (df[match_col].str.strip() != "")]

    # Build column map
    col_map = load_supplier_column_map(conn, supplier_id)
    col_map_patterns = load_supplier_column_map_patterns(conn, supplier_id)

    # Map raw columns → canonical names. Exact matches take priority; if a
    # raw column isn't an exact match, fall back to "contains:" pattern
    # rules (e.g. Labcorp's fee column name embeds the quarter/year and
    # changes every file, so an exact-name map can't cover every period).
    rename = {}
    found_canonical: set[str] = set()
    for raw_col in df.columns:
        norm = _normalize_col(str(raw_col))
        if norm in col_map:
            canonical = col_map[norm]
            rename[raw_col] = canonical
            found_canonical.add(canonical)
            continue
        for substring, canonical in col_map_patterns:
            if canonical in found_canonical:
                continue
            if substring in norm:
                rename[raw_col] = canonical
                found_canonical.add(canonical)
                break

    # Keep original columns with 'raw_' prefix, add canonical columns
    df_out = df.copy()
    df_out.rename(columns=rename, inplace=True)

    # If the file has no per-row fee column at all — only a per-row sales/
    # revenue figure plus a single flat rate stated on the supplier's own
    # ledger (e.g. Labcorp: "3% Admin. Fee due") — derive fee_amount from
    # sales_amount * fee_rate rather than requiring an explicit fee column.
    from .constants import CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT
    fee_rate = supplier_profile.get("fee_rate")
    if fee_rate and CANONICAL_FEE_AMOUNT not in found_canonical and CANONICAL_SALES_AMOUNT in found_canonical:
        df_out[CANONICAL_FEE_AMOUNT] = pd.to_numeric(
            df_out[CANONICAL_SALES_AMOUNT], errors="coerce"
        ) * float(fee_rate)
        found_canonical.add(CANONICAL_FEE_AMOUNT)
        warnings.append(
            f"fee_amount derived as sales_amount * {fee_rate} (no fee column in file)"
        )

    # Check required canonical fields
    from .constants import REQUIRED_CANONICAL_FIELDS, REQUIRED_IDENTITY_FIELDS_ANY
    missing_required = [f for f in REQUIRED_CANONICAL_FIELDS if f not in found_canonical]
    if not any(f in found_canonical for f in REQUIRED_IDENTITY_FIELDS_ANY):
        missing_required.append(
            f"one of {REQUIRED_IDENTITY_FIELDS_ANY} (customer identity)"
        )
    if missing_required:
        raise ValueError(
            f"Required columns not found in file. Missing canonical fields: {missing_required}. "
            f"File columns: {list(df.columns)}"
        )

    # Warn about unmapped columns
    unmapped = [c for c in df.columns if c not in rename and _normalize_col(str(c)) not in col_map]
    if unmapped:
        warnings.append(f"Unmapped columns (will be ignored): {unmapped}")

    df_out.reset_index(drop=True, inplace=True)
    return df_out, warnings


def infer_column_map(
    df: pd.DataFrame,
    supplier_profile_id: int,
    conn: object,
) -> dict[str, str]:
    """
    Attempt to auto-infer a column mapping for a new supplier's file
    by fuzzy-matching column names against known canonical field names.

    Returns a dict of {source_column: suggested_canonical_field} for UI review.
    This is used in the Admin / onboarding workflow to pre-populate the mapping.
    """
    from thefuzz import fuzz
    from .constants import (
        CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
        CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
        CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
        CANONICAL_CONTRACT_NUMBER,
    )

    HINTS: dict[str, list[str]] = {
        CANONICAL_CUSTOMER_NAME: ["customer", "customer name", "customer_name", "name", "account", "facility"],
        CANONICAL_JDE_NUMBER:    ["jde", "jde customer", "jde number", "jde customer number", "customer number", "customer id"],
        CANONICAL_PROCESS_DATE:  ["process date", "process_date", "date", "transaction date", "period"],
        CANONICAL_CITY:          ["city", "billing city"],
        CANONICAL_STATE:         ["state", "billing state", "province"],
        CANONICAL_ZIP:           ["zip", "zip code", "postal", "postal code"],
        CANONICAL_ADDRESS:       ["address", "address line", "street"],
        CANONICAL_SALES_AMOUNT:  ["rebate", "rebate sales", "net sales", "sales", "volume", "rebate net sales"],
        CANONICAL_FEE_AMOUNT:    ["fee", "admin fee", "revenue", "amount", "rebate amount"],
        CANONICAL_PRODUCT_LINE:  ["product", "product line", "product line desc", "category"],
        CANONICAL_CONTRACT_NUMBER: ["contract", "contract number", "contract id"],
    }

    suggestions: dict[str, str] = {}
    used_canonicals: set[str] = set()

    for raw_col in df.columns:
        norm = _normalize_col(str(raw_col))
        best_canonical = None
        best_score = 0
        for canonical, hint_list in HINTS.items():
            if canonical in used_canonicals:
                continue
            for hint in hint_list:
                score = fuzz.ratio(norm, hint)
                if score > best_score:
                    best_score = score
                    best_canonical = canonical
        if best_score >= 70 and best_canonical:
            suggestions[str(raw_col)] = best_canonical
            used_canonicals.add(best_canonical)

    return suggestions
