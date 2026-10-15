"""Supplier profiles (supplier_profile)."""

from typing import Optional

from engine.db import execute_upsert, get_last_insert_id
from models.enums import JobType
from models.suppliers import Supplier, SupplierFormInput, SupplierInput

from .errors import ConflictError, NotFoundError
from .rows import row_dict, rows_dicts

_FORM_FIELDS = [
    "name", "description", "contract_id", "revenue_type", "keep_return_default",
    "payor_filter_name", "payment_id_source", "skip_rows_where_col_null", "sheet_name",
    "header_row", "max_read_columns", "job_type", "requires_manual_confirmation",
]
_ALL_FIELDS = _FORM_FIELDS + [
    "fee_rate", "fuzzy_zip_state_threshold", "fuzzy_city_state_threshold",
    "fuzzy_state_threshold", "fuzzy_nationwide_threshold",
]


def list_suppliers(conn, job_type: Optional[JobType] = None) -> list[Supplier]:
    if job_type:
        rows = conn.execute(
            "SELECT * FROM supplier_profile WHERE job_type = ? ORDER BY name", (job_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM supplier_profile ORDER BY name").fetchall()
    return [Supplier.model_validate(r) for r in rows_dicts(rows)]


def get_supplier(conn, supplier_id: int) -> Supplier:
    row = row_dict(conn.execute("SELECT * FROM supplier_profile WHERE id = ?", (supplier_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Supplier {supplier_id} not found.")
    return Supplier.model_validate(row)


def _values(data: SupplierFormInput, fields: list[str]) -> list:
    values = []
    for f in fields:
        v = getattr(data, f)
        if isinstance(v, bool):
            v = int(v)
        elif isinstance(v, str) and v == "" and f != "name":
            v = None
        values.append(v)
    return values


def _name_taken(conn, name: str, exclude_id: Optional[int] = None) -> bool:
    row = conn.execute("SELECT id FROM supplier_profile WHERE name = ?", (name,)).fetchone()
    return bool(row) and row["id"] != exclude_id


def create_supplier(conn, data: SupplierInput) -> Supplier:
    if _name_taken(conn, data.name):
        raise ConflictError(f"A supplier named '{data.name}' already exists.")
    cols = ", ".join(_ALL_FIELDS)
    marks = ", ".join("?" for _ in _ALL_FIELDS)
    cur = conn.execute(f"INSERT INTO supplier_profile ({cols}) VALUES ({marks})", _values(data, _ALL_FIELDS))
    conn.commit()
    return get_supplier(conn, get_last_insert_id(conn, cur))


def update_supplier(conn, supplier_id: int, data: SupplierInput) -> Supplier:
    get_supplier(conn, supplier_id)
    if _name_taken(conn, data.name, exclude_id=supplier_id):
        raise ConflictError(f"A supplier named '{data.name}' already exists.")
    sets = ", ".join(f"{f} = ?" for f in _ALL_FIELDS)
    conn.execute(
        f"UPDATE supplier_profile SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        _values(data, _ALL_FIELDS) + [supplier_id],
    )
    conn.commit()
    return get_supplier(conn, supplier_id)


def upsert_supplier_by_name(conn, data: SupplierFormInput) -> None:
    """Legacy Admin form semantics: insert, or update the same-named supplier's
    form fields only (fee_rate and fuzzy thresholds are left as they are)."""
    cols = ", ".join(_FORM_FIELDS)
    marks = ", ".join("?" for _ in _FORM_FIELDS)
    updates = ",\n".join(f"{f} = excluded.{f}" for f in _FORM_FIELDS if f != "name")
    execute_upsert(
        conn,
        f"""
        INSERT INTO supplier_profile ({cols})
        VALUES ({marks})
        ON CONFLICT(name) DO UPDATE SET
            {updates},
            updated_at = CURRENT_TIMESTAMP
        """,
        _values(data, _FORM_FIELDS),
    )
    conn.commit()
