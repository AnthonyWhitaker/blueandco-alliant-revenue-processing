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

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


def _normalize_col(name: str) -> str:
    """Normalize a column name for comparison: strip, lowercase, collapse spaces."""
    return " ".join(str(name).strip().lower().split())


def load_supplier_column_map(
    conn: sqlite3.Connection,
    supplier_profile_id: int,
) -> dict[str, str]:
    """
    Load the column map for a supplier as {source_column_normalized: canonical_field}.
    """
    rows = conn.execute(
        "SELECT source_column, canonical_field FROM supplier_column_map WHERE supplier_profile_id = ?",
        (supplier_profile_id,),
    ).fetchall()
    return {_normalize_col(r["source_column"]): r["canonical_field"] for r in rows}


def read_supplier_file(
    file_path: str,
    supplier_profile: dict,
    conn: sqlite3.Connection,
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

    warnings: list[str] = []

    # Read the raw file
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        try:
            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=header_row,
                dtype=str,
            )
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

    # Map raw columns → canonical names
    rename = {}
    found_canonical: set[str] = set()
    for raw_col in df.columns:
        norm = _normalize_col(str(raw_col))
        if norm in col_map:
            canonical = col_map[norm]
            rename[raw_col] = canonical
            found_canonical.add(canonical)

    # Keep original columns with 'raw_' prefix, add canonical columns
    df_out = df.copy()
    df_out.rename(columns=rename, inplace=True)

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
    unmapped = [c for c in df.columns if _normalize_col(str(c)) not in col_map]
    if unmapped:
        warnings.append(f"Unmapped columns (will be ignored): {unmapped}")

    df_out.reset_index(drop=True, inplace=True)
    return df_out, warnings


def infer_column_map(
    df: pd.DataFrame,
    supplier_profile_id: int,
    conn: sqlite3.Connection,
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
