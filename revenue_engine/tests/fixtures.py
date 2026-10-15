"""
Synthetic test data shared by tests/conftest.py and scripts/check_mssql.py.
Invented for tests only (no real member or supplier data). No pytest import,
so it also runs inside the production image.
"""

from pathlib import Path
from typing import Optional

import openpyxl

from engine.db import get_last_insert_id
from engine.member_loader import create_member

SUPPLIER_NAME = "Acme Supply"
FILE_NAME = "Acme Supply 2025.07.02 300.00 Ledger.xlsx"

COLUMN_MAP = {
    "Customer": "customer_name",
    "JDE": "jde_number",
    "City": "city",
    "State": "state",
    "Zip": "zip",
    "Fee": "fee_amount",
    "Sales": "sales_amount",
    "Process Date": "process_date",
}

FILE_ROWS = [
    # Customer, JDE, City, State, Zip, Fee, Sales, Process Date
    ("General Hospital", "J100", "Austin", "TX", "78701", 100.00, 1000.00, "2025-06-15"),
    ("General Hospital", "J100", "Austin", "TX", "78701", 50.00, 500.00, "2025-06-20"),
    ("Mercy Medical Center", "J200", "Dallas", "TX", "75201", 120.00, 1200.00, "2025-06-10"),
    ("Zyxw Qvbn Clinic", "J300", "Santa Fe", "NM", "87501", 30.00, 300.00, "2025-06-11"),
]


def make_supplier(conn, name: str = SUPPLIER_NAME, job_type: str = "row_level",
                  column_map: Optional[dict[str, str]] = None) -> int:
    cur = conn.execute(
        "INSERT INTO supplier_profile (name, contract_id, payor_filter_name, job_type) VALUES (?, ?, ?, ?)",
        (name, "CON-1", name, job_type),
    )
    supplier_id = get_last_insert_id(conn, cur)
    for src, canon in (column_map or {}).items():
        conn.execute(
            "INSERT INTO supplier_column_map (supplier_profile_id, source_column, canonical_field) VALUES (?, ?, ?)",
            (supplier_id, src, canon),
        )
    conn.commit()
    return supplier_id


def make_members(conn) -> dict[str, int]:
    return {
        "general": create_member(conn, "General Hospital", account_id="SF-GEN",
                                 premier_entity_code="EC001", city="Austin", state="TX", zip_code="78701"),
        "mercy": create_member(conn, "Mercy Medical Center", account_id="SF-MER",
                               premier_entity_code="EC002", city="Dallas", state="TX", zip_code="75201"),
        "desert": create_member(conn, "Desert Regional Health", account_id="SF-DES",
                                premier_entity_code="EC003", city="Santa Fe", state="NM", zip_code="87501"),
    }


def write_supplier_file(path: Path, rows=FILE_ROWS) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(COLUMN_MAP.keys()))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def create_job_row(conn, supplier_id: int, file_path: Path, file_name: str = FILE_NAME) -> int:
    cur = conn.execute(
        """
        INSERT INTO revenue_job (supplier_profile_id, original_file_name, file_path, status, date_received, contract_id)
        VALUES (?, ?, ?, 'uploaded', '2025-07-02', 'CON-1')
        """,
        (supplier_id, file_name, str(file_path)),
    )
    conn.commit()
    return get_last_insert_id(conn, cur)
