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

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import openpyxl
import pandas as pd

# Column added to every frame read by read_supplier_file_detailed(): the row's
# 1-based row number in the source spreadsheet (as Excel shows it).
SOURCE_ROW_COL = "__source_row"


@dataclass
class ReadResult:
    df: pd.DataFrame                    # canonical + unmapped raw columns, plus SOURCE_ROW_COL
    warnings: list[str]
    # Rows removed by skip_rows_where_col_null (summary/total rows), renamed to
    # canonical columns like df. Fully empty rows are not kept.
    dropped: pd.DataFrame = field(default_factory=pd.DataFrame)


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


_STREAM_CHUNK = 100_000


def _cell_text(v) -> Optional[str]:
    """A cell as pd.read_excel(dtype=str) gives it: whole floats as ints, datetimes as 'YYYY-MM-DD HH:MM:SS'."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return str(pd.Timestamp(v))
    return str(v)


def _header_names(raw: tuple, width: int) -> list[str]:
    """pd.read_excel's names: blank -> 'Unnamed: i', repeats -> 'X.1', 'X.2'."""
    names, seen = [], {}
    for i in range(width):
        v = raw[i] if i < len(raw) else None
        name = f"Unnamed: {i}" if v is None else _cell_text(v)
        if name in seen:
            seen[name] += 1
            dup = f"{name}.{seen[name]}"
            while dup in seen:
                seen[name] += 1
                dup = f"{name}.{seen[name]}"
            seen[dup] = 0
            name = dup
        else:
            seen[name] = 0
        names.append(name)
    return names


def read_xlsx_streaming(
    file_path: str,
    sheet_name: Union[str, int] = 0,
    header_row: int = 0,
    max_columns: Optional[int] = None,
) -> pd.DataFrame:
    """
    Same frame as pd.read_excel(file_path, sheet_name, header=header_row, dtype=str), read row by row
    in chunks. pd.read_excel holds every cell of the sheet as a Python object at once (about 2 GB for
    a 1M-row Medline file); this stays well under 1 GB.
    """
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[sheet_name] if isinstance(sheet_name, int) else wb[sheet_name]
        rows = ws.iter_rows(values_only=True)
        for _ in range(header_row):
            next(rows, None)
        raw_header = next(rows, None)
        if raw_header is None:
            return pd.DataFrame(dtype="str")
        width = len(raw_header)
        while width and raw_header[width - 1] is None:
            width -= 1
        parts, buf, blank_run = [], [], []
        last_width = width

        def flush(chunk):
            cols = list(zip(*chunk)) if chunk else [()] * width
            parts.append(pd.DataFrame({i: pd.Series([_cell_text(v) for v in col], dtype="str")
                                       for i, col in enumerate(cols)}))

        for row in rows:
            # A row may be wider than the header (data in unnamed columns); pandas names those too.
            if len(row) > width and any(v is not None for v in row[width:]):
                last_width = max(last_width, max(i for i, v in enumerate(row) if v is not None) + 1)
            if all(v is None for v in row):
                blank_run.append(row)       # kept only if more data follows (pandas drops trailing blank rows)
                continue
            if blank_run:
                buf.extend(blank_run)
                blank_run = []
            buf.append(row)
            if len(buf) >= _STREAM_CHUNK:
                flush([tuple(r[:last_width]) + (None,) * (last_width - len(r)) for r in buf])
                buf = []
        if buf or not parts:
            flush([tuple(r[:last_width]) + (None,) * (last_width - len(r)) for r in buf])
    finally:
        wb.close()

    n = max_columns if max_columns else last_width
    parts = [p.reindex(columns=range(last_width)) for p in parts]
    df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    df.columns = _header_names(raw_header, last_width)
    if n < last_width:
        df = df.iloc[:, :n]
    return df


def read_supplier_file(
    file_path: str,
    supplier_profile: dict,
    conn: object,
) -> tuple[pd.DataFrame, list[str]]:
    """Like read_supplier_file_detailed, without the dropped rows or the source row column."""
    result = read_supplier_file_detailed(file_path, supplier_profile, conn)
    return result.df.drop(columns=[SOURCE_ROW_COL]), result.warnings


def read_supplier_file_detailed(
    file_path: str,
    supplier_profile: dict,
    conn: object,
) -> ReadResult:
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
            if suffix == ".xls":
                kw = {**read_kwargs, "usecols": _usecols} if _usecols else read_kwargs
                try:
                    df = pd.read_excel(file_path, **kw)
                except ValueError as e:
                    if _usecols and "out-of-bounds" in str(e):
                        df = pd.read_excel(file_path, **read_kwargs)
                    else:
                        raise
            else:
                # A file narrower than max_read_columns just reads all of its columns.
                df = read_xlsx_streaming(file_path, sheet_name, header_row,
                                         int(max_read_columns) if max_read_columns else None)
        except Exception as e:
            raise ValueError(f"Cannot read Excel file: {e}") from e
    elif suffix == ".csv":
        try:
            df = pd.read_csv(file_path, header=header_row, dtype=str)
        except Exception as e:
            raise ValueError(f"Cannot read CSV file: {e}") from e
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    # Spreadsheet row number of each data row (header row + 1, 1-based)
    data_cols = list(df.columns)
    df[SOURCE_ROW_COL] = df.index + header_row + 2

    # Strip completely empty rows and columns
    df.dropna(how="all", subset=data_cols, inplace=True)
    df.dropna(axis=1, how="all", inplace=True)
    dropped = df.iloc[0:0]

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
            keep = df[match_col].notna() & (df[match_col].str.strip() != "")
            dropped = df[~keep]
            df = df[keep]

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
    dropped = dropped.rename(columns=rename)

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
    unmapped = [c for c in df.columns
                if c not in rename and c != SOURCE_ROW_COL and _normalize_col(str(c)) not in col_map]
    if unmapped:
        warnings.append(f"Unmapped columns (will be ignored): {unmapped}")

    df_out.reset_index(drop=True, inplace=True)
    return ReadResult(df_out, warnings, dropped.reset_index(drop=True))


# Leading words that qualify which party a header is about ("Account City",
# "Ship-To State", "Member Name"); headers are also scored without them.
_HEADER_QUALIFIERS = re.compile(r"^(account|facility|member|customer|ship to|billing|bill to|report|payer|payor)\s+")
# Headers holding a rate rather than money are never suggested as an amount.
_RATE_HEADER = re.compile(r"\b(rate|pct|percent)\b|%")


def _normalize_header(name: str) -> str:
    """Like _normalize_col, but also splits CamelCase and treats _ - / . as spaces:
    'InvoiceDate' -> 'invoice date', 'Facility_Zip_Code' -> 'facility zip code'."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(name))
    return _normalize_col(re.sub(r"[_\-/.]+", " ", spaced))


def _header_variants(header: str, supplier_name: str) -> set[str]:
    """The header as written, without the supplier's own name ('Medline Account #'
    -> 'account #'), and without a leading qualifier ('Account City' -> 'city')."""
    variants = {_normalize_header(header)}
    supplier_words = set(_normalize_header(supplier_name).split()) if supplier_name else set()
    if supplier_words:
        kept = [w for w in _normalize_header(header).split() if w not in supplier_words]
        if kept:
            variants.add(" ".join(kept))
    for v in list(variants):
        stripped = _HEADER_QUALIFIERS.sub("", v)
        if stripped and stripped != v:
            variants.add(stripped)
    return variants


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
        CANONICAL_CONTRACT_NUMBER, CANONICAL_ENTITY_CODE_HINT,
    )

    HINTS: dict[str, list[str]] = {
        CANONICAL_CUSTOMER_NAME: ["customer", "customer name", "customer_name", "name", "account", "facility",
                                  "account name", "facility name", "member name"],
        CANONICAL_JDE_NUMBER:    ["jde", "jde customer", "jde number", "jde customer number", "customer number", "customer id",
                                  "account #", "account number", "customer #"],
        CANONICAL_PROCESS_DATE:  ["process date", "process_date", "date", "transaction date", "period",
                                  "invoice date", "sale date", "earned period"],
        CANONICAL_CITY:          ["city", "billing city"],
        CANONICAL_STATE:         ["state", "billing state", "province"],
        CANONICAL_ZIP:           ["zip", "zip code", "postal", "postal code"],
        CANONICAL_ADDRESS:       ["address", "address line", "street"],
        CANONICAL_SALES_AMOUNT:  ["rebate", "rebate sales", "net sales", "sales", "volume", "rebate net sales",
                                  "extended price", "extended price amount", "total sales dollars", "sales volume"],
        CANONICAL_FEE_AMOUNT:    ["fee", "admin fee", "revenue", "amount", "rebate amount",
                                  "fee amount", "gpo fee amount", "admin fee amount", "administration fee amount"],
        CANONICAL_PRODUCT_LINE:  ["product", "product line", "product line desc", "category",
                                  "product division", "product division code"],
        CANONICAL_CONTRACT_NUMBER: ["contract", "contract number", "contract id"],
        CANONICAL_ENTITY_CODE_HINT: ["premier entity code", "entity code", "pec", "gpo id", "gpo member number"],
    }
    amount_fields = {CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT}

    supplier_name = ""
    if conn is not None:
        row = conn.execute("SELECT name FROM supplier_profile WHERE id = ?", (supplier_profile_id,)).fetchone()
        supplier_name = row["name"] if row else ""

    # Score every (column, field) pair, then hand out the best pairs first, so a
    # column only gets a field no other column fits better (otherwise "GPO Fee
    # Rate", which comes first, would take fee_amount from "GPO Fee Amount").
    scored: list[tuple[int, int, str, str]] = []
    for position, raw_col in enumerate(df.columns):
        variants = _header_variants(str(raw_col), supplier_name)
        is_rate = bool(_RATE_HEADER.search(_normalize_header(str(raw_col))))
        for canonical, hint_list in HINTS.items():
            if is_rate and canonical in amount_fields:
                continue
            score = max(fuzz.ratio(v, hint) for v in variants for hint in hint_list)
            if score >= 70:
                scored.append((score, -position, str(raw_col), canonical))

    suggestions: dict[str, str] = {}
    for _, _, col, canonical in sorted(scored, reverse=True):
        if col not in suggestions and canonical not in suggestions.values():
            suggestions[col] = canonical

    # Keep the file's column order.
    order = {str(c): i for i, c in enumerate(df.columns)}
    return dict(sorted(suggestions.items(), key=lambda kv: order[kv[0]]))
