"""Dashboard stats, member aliases, and reference metadata."""

from engine.constants import (
    ALLOC_FIXED_AMOUNT, ALLOC_PERCENT, ALLOC_REMAINDER,
    CANONICAL_ACCOUNT_ID_HINT, CANONICAL_ADDRESS, CANONICAL_CITY, CANONICAL_CONTRACT_NUMBER,
    CANONICAL_CUSTOMER_NAME, CANONICAL_ENTITY_CODE_HINT, CANONICAL_FEE_AMOUNT, CANONICAL_JDE_NUMBER,
    CANONICAL_PROCESS_DATE, CANONICAL_PRODUCT_LINE, CANONICAL_SALES_AMOUNT, CANONICAL_STATE, CANONICAL_ZIP,
    EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
    EXTRACT_MANUAL_ENTRY, EXTRACT_NAMED_CELL, JOB_TYPE_PIVOT_ALLOCATION, JOB_TYPE_ROW_LEVEL,
)
from engine.db import execute_select, get_last_insert_id
from engine.normalizer import normalize_key
from models.misc import Alias, AliasInput, DashboardStats, Meta

from .errors import ConflictError
from .jobs import UPLOAD_EXTENSIONS
from .members import get_member
from .rows import row_dict, rows_dicts
from .suppliers import get_supplier

CANONICAL_FIELDS = [
    CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
    CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
    CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
    CANONICAL_CONTRACT_NUMBER, CANONICAL_ENTITY_CODE_HINT, CANONICAL_ACCOUNT_ID_HINT,
]

_ALIAS_SELECT = """
    SELECT a.id, a.member_id, m.name AS member_name, m.entity_code, a.raw_name, a.jde_number,
           a.supplier_profile_id, sp.name AS supplier_name, a.approved_by, a.approved_at
    FROM member_alias a
    JOIN member m ON m.id = a.member_id
    LEFT JOIN supplier_profile sp ON sp.id = a.supplier_profile_id
"""


def get_dashboard_stats(conn) -> DashboardStats:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return DashboardStats(
        active_members=count("SELECT COUNT(*) FROM member WHERE is_active = 1"),
        suppliers=count("SELECT COUNT(*) FROM supplier_profile"),
        jobs=count("SELECT COUNT(*) FROM revenue_job"),
        pending_reviews=count("SELECT COUNT(*) FROM member_review_item WHERE status = 'pending'"),
    )


def get_meta() -> Meta:
    return Meta(
        canonical_fields=CANONICAL_FIELDS,
        revenue_types=["Admin Fee", "Rebate", "Other"],
        keep_return_options=["Keep", "Return"],
        payment_id_sources=["ledger", "manual", "column"],
        job_types=[JOB_TYPE_ROW_LEVEL, JOB_TYPE_PIVOT_ALLOCATION],
        extraction_methods=[
            EXTRACT_NAMED_CELL, EXTRACT_LABEL_LOOKUP, EXTRACT_LABEL_RANGE_SUM,
            EXTRACT_COLUMN_SUM, EXTRACT_FORMULA, EXTRACT_MANUAL_ENTRY,
        ],
        label_match_modes=["exact", "contains", "startswith", "fuzzy"],
        range_axes=["column", "row"],
        allocation_types=[ALLOC_PERCENT, ALLOC_FIXED_AMOUNT, ALLOC_REMAINDER],
        alias_scopes=["supplier", "global", "job_only"],
        upload_extensions=UPLOAD_EXTENSIONS,
    )


def list_aliases(conn, limit: int = 200) -> list[Alias]:
    rows = execute_select(conn, _ALIAS_SELECT + f" ORDER BY m.name, a.raw_name LIMIT {int(limit)}").fetchall()
    return [Alias.model_validate(r) for r in rows_dicts(rows)]


def create_alias(conn, data: AliasInput, approved_by: str = "admin") -> Alias:
    get_member(conn, data.member_id)
    if data.supplier_profile_id is not None:
        get_supplier(conn, data.supplier_profile_id)
    key = normalize_key(data.raw_name)
    if data.supplier_profile_id is None:
        clash = conn.execute(
            "SELECT 1 FROM member_alias WHERE supplier_profile_id IS NULL AND canonical_key = ?", (key,)
        ).fetchone()
    else:
        clash = conn.execute(
            "SELECT 1 FROM member_alias WHERE supplier_profile_id = ? AND canonical_key = ?",
            (data.supplier_profile_id, key),
        ).fetchone()
    if clash:
        raise ConflictError(f"An alias matching '{data.raw_name}' already exists for that scope.")
    cur = conn.execute(
        """
        INSERT INTO member_alias
            (member_id, supplier_profile_id, raw_name, canonical_key, jde_number, approved_by, approved_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (data.member_id, data.supplier_profile_id, data.raw_name, key, data.jde_number or None, approved_by),
    )
    conn.commit()
    alias_id = get_last_insert_id(conn, cur)
    return Alias.model_validate(row_dict(conn.execute(_ALIAS_SELECT + " WHERE a.id = ?", (alias_id,)).fetchone()))
