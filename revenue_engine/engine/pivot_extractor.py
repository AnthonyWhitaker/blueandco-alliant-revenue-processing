"""
Tier C aggregate/pivot total extraction for the Revenue Processing Engine.

Suppliers configured with supplier_profile.job_type = 'pivot_allocation'
deliver files with no facility-level rows to match — a single lump-sum
total, or a handful of named totals scattered across an arbitrarily-shaped
summary/pivot sheet. This module locates and extracts those totals, driven
entirely by supplier_extraction_rule records (never by supplier name).

Public entry point: extract_totals(file_path, supplier_profile_id, conn)
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .constants import (
    EXTRACT_NAMED_CELL, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
    EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_MANUAL_ENTRY,
)
from .file_reader import read_raw_grid


class ExtractionError(Exception):
    """Raised when a rule's total cannot be located or is ambiguous."""


@dataclass
class ExtractionResult:
    values: dict[str, float] = field(default_factory=dict)
    is_sales_amount: dict[str, bool] = field(default_factory=dict)
    manual_entry_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_extraction_rules(conn: object, supplier_profile_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM supplier_extraction_rule WHERE supplier_profile_id = ? ORDER BY sort_order, id",
        (supplier_profile_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _col_letter_to_index(letter: str) -> int:
    letter = letter.strip().upper()
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _cell_address_to_rc(addr: str) -> tuple[int, int]:
    """'E8' -> (row=7, col=4), 0-based."""
    m = re.match(r"^([A-Za-z]+)(\d+)$", addr.strip())
    if not m:
        raise ExtractionError(f"Invalid cell address: {addr!r}")
    col_letters, row_digits = m.groups()
    return int(row_digits) - 1, _col_letter_to_index(col_letters)


def _label_matches(cell_value, label_text: str, mode: str, threshold: int) -> bool:
    if pd.isna(cell_value):
        return False
    cell_str = str(cell_value).strip()
    target = str(label_text).strip()
    if mode == "exact":
        return cell_str.casefold() == target.casefold()
    if mode == "contains":
        return target.casefold() in cell_str.casefold()
    if mode == "startswith":
        return cell_str.casefold().startswith(target.casefold())
    if mode == "fuzzy":
        from rapidfuzz import fuzz
        return fuzz.ratio(cell_str.casefold(), target.casefold()) >= threshold
    raise ExtractionError(f"Unknown label_match_mode: {mode!r}")


def _find_label_cells(grid: pd.DataFrame, label_text: str, mode: str, threshold: int) -> list[tuple[int, int]]:
    hits = []
    for r in range(grid.shape[0]):
        for c in range(grid.shape[1]):
            if _label_matches(grid.iat[r, c], label_text, mode, threshold):
                hits.append((r, c))
    return hits


def _extract_named_cell(grid: pd.DataFrame, rule: dict) -> float:
    r, c = _cell_address_to_rc(rule["cell_address"])
    if r >= grid.shape[0] or c >= grid.shape[1]:
        raise ExtractionError(
            f"Cell {rule['cell_address']} is out of bounds for sheet shape {grid.shape}"
        )
    val = grid.iat[r, c]
    if pd.isna(val):
        raise ExtractionError(f"Cell {rule['cell_address']} is blank")
    return float(val)


def _extract_label_lookup(grid: pd.DataFrame, rule: dict) -> float:
    mode = rule.get("label_match_mode") or "exact"
    threshold = rule.get("label_match_threshold") or 90
    hits = _find_label_cells(grid, rule["label_text"], mode, threshold)
    if not hits:
        raise ExtractionError(f"Label {rule['label_text']!r} not found on sheet")
    if len(hits) > 1:
        raise ExtractionError(
            f"Label {rule['label_text']!r} matched {len(hits)} cells: {hits} — ambiguous"
        )
    r, c = hits[0]
    value_r = r + (rule.get("value_row_offset") if rule.get("value_row_offset") is not None else 1)
    value_c = c + (rule.get("value_col_offset") or 0)
    if value_r >= grid.shape[0] or value_c >= grid.shape[1] or value_r < 0 or value_c < 0:
        raise ExtractionError(
            f"Value offset from label {rule['label_text']!r} at ({r},{c}) is out of bounds"
        )
    val = grid.iat[value_r, value_c]
    if pd.isna(val):
        raise ExtractionError(f"Value cell for label {rule['label_text']!r} is blank")
    return float(val)


def _extract_label_range_sum(grid: pd.DataFrame, rule: dict) -> float:
    mode = rule.get("label_match_mode") or "exact"
    threshold = rule.get("label_match_threshold") or 90
    hits = _find_label_cells(grid, rule["label_text"], mode, threshold)
    if not hits:
        raise ExtractionError(f"Label {rule['label_text']!r} not found on sheet")
    if len(hits) > 1:
        raise ExtractionError(
            f"Label {rule['label_text']!r} matched {len(hits)} cells: {hits} — ambiguous"
        )
    r, c = hits[0]
    axis = rule.get("range_axis") or "column"
    stop_label = rule.get("stop_label_text")
    total = 0.0
    if axis == "column":
        rr = r + 1
        while rr < grid.shape[0]:
            val = grid.iat[rr, c]
            if pd.isna(val):
                break
            if stop_label and _label_matches(val, stop_label, mode, threshold):
                break
            if isinstance(val, (int, float)):
                total += float(val)
            rr += 1
    else:
        cc = c + 1
        while cc < grid.shape[1]:
            val = grid.iat[r, cc]
            if pd.isna(val):
                break
            if stop_label and _label_matches(val, stop_label, mode, threshold):
                break
            if isinstance(val, (int, float)):
                total += float(val)
            cc += 1
    return total


def _extract_column_sum(grid: pd.DataFrame, rule: dict) -> float:
    if rule.get("column_index") is not None:
        col_idx = int(rule["column_index"])
    elif rule.get("column_letter"):
        col_idx = _col_letter_to_index(rule["column_letter"])
    else:
        raise ExtractionError("column_sum rule requires column_index or column_letter")
    header_row = rule.get("header_row") or 0
    if col_idx >= grid.shape[1]:
        raise ExtractionError(f"Column index {col_idx} out of bounds for sheet shape {grid.shape}")
    series = grid.iloc[header_row + 1:, col_idx]
    numeric = pd.to_numeric(series, errors="coerce")
    return float(numeric.sum(skipna=True))


def _extract_formula(rule: dict, values_so_far: dict[str, float]) -> float:
    expr = rule["formula_expression"]
    # Whitelisted grammar only: rule names, +, -, *, /, parentheses, numbers.
    # Sort longer names first so a name that's a substring of another isn't
    # partially replaced.
    tokens = sorted(values_so_far.keys(), key=len, reverse=True)
    safe_expr = expr
    for name in tokens:
        if name in safe_expr:
            safe_expr = safe_expr.replace(name, f"({values_so_far[name]!r})")
    if not re.fullmatch(r"[0-9\.\+\-\*/\(\)\s]+", safe_expr):
        raise ExtractionError(
            f"Formula {expr!r} references an unknown rule name or disallowed token "
            f"(resolved to {safe_expr!r})"
        )
    try:
        return float(eval(safe_expr, {"__builtins__": {}}, {}))
    except Exception as e:
        raise ExtractionError(f"Could not evaluate formula {expr!r}: {e}")


def extract_totals(
    file_path: str,
    supplier_profile_id: int,
    conn: object,
    manual_totals: Optional[dict[str, float]] = None,
) -> ExtractionResult:
    """
    Run all supplier_extraction_rule records for a supplier against a raw
    file, returning the extracted {rule_name: value} plus any manual_entry
    rule names that still need a human-entered value (manual_totals holds
    the ones entered at upload, by rule name), and warnings for anything
    that failed (never raises — failures show up as missing rule_name keys
    plus a warning, so callers can decide whether that blocks the job).
    """
    rules = load_extraction_rules(conn, supplier_profile_id)
    if not rules:
        raise ExtractionError(
            f"No supplier_extraction_rule records configured for supplier_profile_id={supplier_profile_id}"
        )

    result = ExtractionResult()
    grids_by_sheet: dict[Optional[str], pd.DataFrame] = {}

    # formula rules must run after all others, regardless of sort_order,
    # since they reference other rules' already-extracted values.
    formula_rules = [r for r in rules if r["extraction_method"] == EXTRACT_FORMULA]
    direct_rules = [r for r in rules if r["extraction_method"] != EXTRACT_FORMULA]

    for rule in direct_rules:
        method = rule["extraction_method"]
        rule_name = rule["rule_name"]
        if method == EXTRACT_MANUAL_ENTRY:
            if manual_totals and manual_totals.get(rule_name) is not None:
                result.values[rule_name] = float(manual_totals[rule_name])
            else:
                result.manual_entry_rules.append(rule_name)
            result.is_sales_amount[rule_name] = bool(rule.get("is_sales_amount"))
            continue
        sheet_key = rule.get("sheet_name")
        try:
            if sheet_key not in grids_by_sheet:
                grids_by_sheet[sheet_key] = read_raw_grid(file_path, sheet_key)
            grid = grids_by_sheet[sheet_key]

            if method == EXTRACT_NAMED_CELL:
                value = _extract_named_cell(grid, rule)
            elif method == EXTRACT_LABEL_LOOKUP:
                value = _extract_label_lookup(grid, rule)
            elif method == EXTRACT_LABEL_RANGE_SUM:
                value = _extract_label_range_sum(grid, rule)
            elif method == EXTRACT_COLUMN_SUM:
                value = _extract_column_sum(grid, rule)
            else:
                raise ExtractionError(f"Unknown extraction_method: {method!r}")

            result.values[rule_name] = value
            result.is_sales_amount[rule_name] = bool(rule.get("is_sales_amount"))
        except ExtractionError as e:
            result.warnings.append(f"[{rule_name}] {e}")

    for rule in formula_rules:
        rule_name = rule["rule_name"]
        try:
            value = _extract_formula(rule, result.values)
            result.values[rule_name] = value
            result.is_sales_amount[rule_name] = bool(rule.get("is_sales_amount"))
        except ExtractionError as e:
            result.warnings.append(f"[{rule_name}] {e}")

    return result


def preview_extraction(file_path: str, supplier_profile_id: int, conn: object) -> ExtractionResult:
    """Alias used by the Admin/Upload UI's dry-run preview — identical
    logic to extract_totals(), kept as a separate name so the UI's intent
    (test config against a sample file, no job created) is explicit."""
    return extract_totals(file_path, supplier_profile_id, conn)
