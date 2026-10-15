"""
Tier C (pivot_allocation) rules: where each total lives in a workpaper
(supplier_extraction_rule) and who it is allocated to (supplier_allocation_rule).

Business rules carried over from the legacy Admin › Tier C Rules tab:
  * renaming an extraction rule renames the allocation rules that reference it
  * an extraction rule can't be deleted while allocation rules reference it
  * method-irrelevant fields are stored as the same defaults the old form wrote
  * advisory notices: formula rule-name substring collisions, >100% / <100%
    percent allocations, more than one remainder row
"""

from typing import Optional

from engine.constants import JOB_TYPE_PIVOT_ALLOCATION
from engine.db import get_last_insert_id
from models.common import Notice
from models.tier_c import (
    AllocationRule, AllocationRuleDraft, AllocationRuleInput,
    ExtractionRule, ExtractionRuleInput, ExtractionRuleSaveResult,
)

from .errors import ConflictError, NotFoundError
from .members import get_member
from .rows import row_dict, rows_dicts
from .suppliers import get_supplier

_EXTRACTION_COLS = [
    "supplier_profile_id", "rule_name", "sheet_name", "extraction_method",
    "cell_address", "label_text", "label_match_mode", "label_match_threshold",
    "value_row_offset", "value_col_offset", "range_axis", "column_index",
    "column_letter", "header_row", "stop_label_text", "formula_expression",
    "is_sales_amount", "sort_order",
]
_ALLOCATION_COLS = [
    "supplier_profile_id", "extraction_rule_name", "member_id", "allocation_type",
    "allocation_value", "contract_number", "product_line", "is_active",
]
_ALLOCATION_SELECT = """
    SELECT a.*, m.name AS member_name, m.entity_code
    FROM supplier_allocation_rule a
    JOIN member m ON m.id = a.member_id
"""


def _require_pivot_supplier(conn, supplier_id: int) -> None:
    supplier = get_supplier(conn, supplier_id)
    if supplier.job_type != JOB_TYPE_PIVOT_ALLOCATION:
        raise ConflictError(
            f"Supplier '{supplier.name}' has job_type '{supplier.job_type}'; "
            "Tier C rules only apply to pivot_allocation suppliers."
        )


# ---------------------------------------------------------------------------
# Extraction rules
# ---------------------------------------------------------------------------

def list_extraction_rules(conn, supplier_id: int) -> list[ExtractionRule]:
    get_supplier(conn, supplier_id)
    rows = conn.execute(
        "SELECT * FROM supplier_extraction_rule WHERE supplier_profile_id = ? ORDER BY sort_order, id",
        (supplier_id,),
    ).fetchall()
    return [ExtractionRule.model_validate(r) for r in rows_dicts(rows)]


def get_extraction_rule(conn, rule_id: int) -> ExtractionRule:
    row = row_dict(conn.execute("SELECT * FROM supplier_extraction_rule WHERE id = ?", (rule_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Extraction rule {rule_id} not found.")
    return ExtractionRule.model_validate(row)


def _extraction_values(supplier_id: int, d: ExtractionRuleInput) -> list:
    m = d.extraction_method
    is_label = m in ("label_lookup", "label_range_sum")
    return [
        supplier_id,
        d.rule_name,
        d.sheet_name or None,
        m,
        d.cell_address if m == "named_cell" else None,
        d.label_text if is_label else None,
        d.label_match_mode if is_label else None,
        d.label_match_threshold if is_label and d.label_match_mode == "fuzzy" else 90,
        d.value_row_offset if m == "label_lookup" else 1,
        d.value_col_offset if m == "label_lookup" else 0,
        d.range_axis if m == "label_range_sum" else "column",
        d.column_index if m == "column_sum" and d.column_letter is None else None,
        d.column_letter.upper() if m == "column_sum" and d.column_letter else None,
        d.header_row if m == "column_sum" else 0,
        (d.stop_label_text or None) if m == "label_range_sum" else None,
        d.formula_expression if m == "formula" else None,
        int(d.is_sales_amount),
        d.sort_order,
    ]


def formula_name_notices(rule_names: list[str]) -> list[Notice]:
    """Rule names that are substrings of other names make formula references ambiguous."""
    return [
        Notice(level="warning",
               message=f"Rule name {a!r} is a substring of {b!r} — formula references can collide. "
                       "Consider distinct names.")
        for a in rule_names for b in rule_names if a != b and a in b
    ]


def save_extraction_rule(
    conn, supplier_id: int, data: ExtractionRuleInput, rule_id: Optional[int] = None,
) -> ExtractionRuleSaveResult:
    """Create (rule_id None) or update an extraction rule."""
    _require_pivot_supplier(conn, supplier_id)
    existing = get_extraction_rule(conn, rule_id) if rule_id is not None else None
    if existing and existing.supplier_profile_id != supplier_id:
        raise NotFoundError(f"Extraction rule {rule_id} not found for supplier {supplier_id}.")

    clash = conn.execute(
        "SELECT id FROM supplier_extraction_rule WHERE supplier_profile_id = ? AND rule_name = ?",
        (supplier_id, data.rule_name),
    ).fetchone()
    if clash and (existing is None or clash["id"] != existing.id):
        raise ConflictError(f"An extraction rule named '{data.rule_name}' already exists for this supplier.")

    values = _extraction_values(supplier_id, data)
    renamed = 0
    notices: list[Notice] = []
    if existing:
        sets = ", ".join(f"{c} = ?" for c in _EXTRACTION_COLS)
        conn.execute(f"UPDATE supplier_extraction_rule SET {sets} WHERE id = ?", values + [existing.id])
        if existing.rule_name != data.rule_name:
            cur = conn.execute(
                "UPDATE supplier_allocation_rule SET extraction_rule_name = ? "
                "WHERE supplier_profile_id = ? AND extraction_rule_name = ?",
                (data.rule_name, supplier_id, existing.rule_name),
            )
            renamed = cur.rowcount
            if renamed:
                notices.append(Notice(
                    level="warning",
                    message=f"Renamed — updated {renamed} allocation rule(s) referencing the old name. "
                            f"Check any 'formula' rules referencing {existing.rule_name!r} manually.",
                ))
        saved_id = existing.id
    else:
        cols = ", ".join(_EXTRACTION_COLS)
        marks = ", ".join("?" for _ in _EXTRACTION_COLS)
        cur = conn.execute(f"INSERT INTO supplier_extraction_rule ({cols}) VALUES ({marks})", values)
        saved_id = get_last_insert_id(conn, cur)
    conn.commit()

    if data.extraction_method == "formula":
        notices += formula_name_notices([r.rule_name for r in list_extraction_rules(conn, supplier_id)])
    return ExtractionRuleSaveResult(
        rule=get_extraction_rule(conn, saved_id), renamed_allocation_refs=renamed, notices=notices,
    )


def delete_extraction_rule(conn, rule_id: int) -> None:
    rule = get_extraction_rule(conn, rule_id)
    n_refs = conn.execute(
        "SELECT COUNT(*) FROM supplier_allocation_rule WHERE supplier_profile_id = ? AND extraction_rule_name = ?",
        (rule.supplier_profile_id, rule.rule_name),
    ).fetchone()[0]
    if n_refs:
        raise ConflictError(
            f"Cannot delete: {n_refs} allocation rule(s) reference '{rule.rule_name}'. Delete those first."
        )
    conn.execute("DELETE FROM supplier_extraction_rule WHERE id = ?", (rule_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Allocation rules
# ---------------------------------------------------------------------------

def list_allocation_rules(conn, supplier_id: int) -> list[AllocationRule]:
    get_supplier(conn, supplier_id)
    rows = conn.execute(
        _ALLOCATION_SELECT + " WHERE a.supplier_profile_id = ? ORDER BY a.extraction_rule_name, m.name",
        (supplier_id,),
    ).fetchall()
    return [AllocationRule.model_validate(r) for r in rows_dicts(rows)]


def get_allocation_rule(conn, rule_id: int) -> AllocationRule:
    row = row_dict(conn.execute(_ALLOCATION_SELECT + " WHERE a.id = ?", (rule_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Allocation rule {rule_id} not found.")
    return AllocationRule.model_validate(row)


def save_allocation_rule(
    conn, supplier_id: int, data: AllocationRuleInput, rule_id: Optional[int] = None,
) -> AllocationRule:
    """Create (rule_id None) or update an allocation rule."""
    _require_pivot_supplier(conn, supplier_id)
    existing = get_allocation_rule(conn, rule_id) if rule_id is not None else None
    if existing and existing.supplier_profile_id != supplier_id:
        raise NotFoundError(f"Allocation rule {rule_id} not found for supplier {supplier_id}.")
    get_member(conn, data.member_id)
    if not conn.execute(
        "SELECT 1 FROM supplier_extraction_rule WHERE supplier_profile_id = ? AND rule_name = ?",
        (supplier_id, data.extraction_rule_name),
    ).fetchone():
        raise ConflictError(f"No extraction rule named '{data.extraction_rule_name}' exists for this supplier.")
    clash = conn.execute(
        "SELECT id FROM supplier_allocation_rule "
        "WHERE supplier_profile_id = ? AND extraction_rule_name = ? AND member_id = ?",
        (supplier_id, data.extraction_rule_name, data.member_id),
    ).fetchone()
    if clash and (existing is None or clash["id"] != existing.id):
        raise ConflictError("That member already has an allocation row for this extraction rule.")

    values = [
        supplier_id, data.extraction_rule_name, data.member_id, data.allocation_type,
        data.allocation_value if data.allocation_type != "remainder" else None,
        data.contract_number or None, data.product_line or None, int(data.is_active),
    ]
    if existing:
        sets = ", ".join(f"{c} = ?" for c in _ALLOCATION_COLS)
        conn.execute(f"UPDATE supplier_allocation_rule SET {sets} WHERE id = ?", values + [existing.id])
        saved_id = existing.id
    else:
        cols = ", ".join(_ALLOCATION_COLS)
        marks = ", ".join("?" for _ in _ALLOCATION_COLS)
        cur = conn.execute(f"INSERT INTO supplier_allocation_rule ({cols}) VALUES ({marks})", values)
        saved_id = get_last_insert_id(conn, cur)
    conn.commit()
    return get_allocation_rule(conn, saved_id)


def delete_allocation_rule(conn, rule_id: int) -> None:
    get_allocation_rule(conn, rule_id)
    conn.execute("DELETE FROM supplier_allocation_rule WHERE id = ?", (rule_id,))
    conn.commit()


def allocation_notices(saved: list[AllocationRule], draft: AllocationRuleDraft) -> list[Notice]:
    """Advisory checks for an unsaved allocation rule against the supplier's saved rules."""
    others = [
        r for r in saved
        if r.extraction_rule_name == draft.extraction_rule_name and r.id != draft.rule_id
    ]
    notices: list[Notice] = []
    if draft.allocation_type == "remainder" and any(r.allocation_type == "remainder" for r in others):
        notices.append(Notice(
            level="warning",
            message=f"Extraction rule '{draft.extraction_rule_name}' already has a remainder row — "
                    "only the first is used at runtime.",
        ))
    pct_sum = sum(r.allocation_value or 0 for r in others if r.allocation_type == "percent" and r.is_active)
    if draft.allocation_type == "percent" and draft.allocation_value:
        pct_sum += draft.allocation_value
    if pct_sum > 100:
        notices.append(Notice(
            level="warning",
            message=f"Active percent allocations for '{draft.extraction_rule_name}' now sum to {pct_sum:.1f}% (>100%).",
        ))
    elif 0 < pct_sum < 100 and not others and draft.allocation_type != "remainder":
        notices.append(Notice(
            level="info",
            message=f"Percent allocations for '{draft.extraction_rule_name}' sum to {pct_sum:.1f}% — "
                    "add a remainder row or more rules to cover the full total.",
        ))
    return notices


def validate_allocation_draft(conn, supplier_id: int, draft: AllocationRuleDraft) -> list[Notice]:
    return allocation_notices(list_allocation_rules(conn, supplier_id), draft)
