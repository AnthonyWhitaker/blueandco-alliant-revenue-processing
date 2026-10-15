"""
Shared fixtures: a fresh SQLite database per test, plus small synthetic
suppliers/members/files (tests/fixtures.py) run through the real pipeline.
"""

import pytest

from engine.db import init_db
from engine.processor import process_job

from .fixtures import (  # noqa: F401  (re-exported for test modules)
    COLUMN_MAP, FILE_NAME, FILE_ROWS, SUPPLIER_NAME,
    create_job_row, make_members, make_supplier, write_supplier_file,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    db_path = tmp_path / "revenue.db"
    monkeypatch.setenv("REVENUE_DB_PATH", str(db_path))
    connection = init_db(str(db_path))
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn, tmp_path):
    """A supplier with a column map, three members, and one ledger entry."""
    supplier_id = make_supplier(conn, column_map=COLUMN_MAP)
    members = make_members(conn)
    conn.execute(
        "INSERT INTO payment_ledger (payor, payment_received_date, deposit_amount, sf_payment_id) VALUES (?, ?, ?, ?)",
        (SUPPLIER_NAME, "2025-07-02", 300.00, "PAY-1"),
    )
    conn.commit()
    return {"supplier_id": supplier_id, "members": members}


@pytest.fixture
def processed_job(conn, seeded, tmp_path):
    """Runs the real row-level pipeline: 3 matched rows, 1 unmatched customer."""
    file_path = write_supplier_file(tmp_path / FILE_NAME)
    job_id = create_job_row(conn, seeded["supplier_id"], file_path)
    result = process_job(conn=conn, job_id=job_id)
    return {**seeded, "job_id": job_id, "result": result, "file_path": file_path}
