"""SQLite → SQL Server rewrites in engine/db.py, checked without a SQL Server."""

import pytest

from engine.db import execute_select


class CapturingConn:
    def __init__(self):
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql
        return self


@pytest.fixture
def mssql(monkeypatch):
    monkeypatch.setenv("AZURE_SQL_CONNECTION_STRING", "Driver=fake")


def test_limit_becomes_top_with_leading_whitespace(mssql):
    """FINDINGS F18: offsets from the stripped SQL were applied to the unstripped SQL,
    cutting the end off any query that started with a newline."""
    conn = CapturingConn()
    execute_select(conn, """
        SELECT sf_payment_id, payment_received_date
        FROM payment_ledger
        ORDER BY payment_received_date DESC LIMIT 1
    """)
    assert conn.sql.startswith("SELECT TOP 1 sf_payment_id")
    assert conn.sql.endswith("ORDER BY payment_received_date DESC")


def test_limit_with_distinct(mssql):
    conn = CapturingConn()
    execute_select(conn, "SELECT DISTINCT name FROM member ORDER BY name LIMIT 20")
    # SQL Server requires DISTINCT before TOP.
    assert conn.sql == "SELECT DISTINCT TOP 20 name FROM member ORDER BY name"


def test_sqlite_sql_is_untouched(monkeypatch):
    monkeypatch.delenv("AZURE_SQL_CONNECTION_STRING", raising=False)
    conn = CapturingConn()
    sql = "\n SELECT 1 LIMIT 5\n"
    execute_select(conn, sql)
    assert conn.sql == sql


def test_upsert_merge_references_target_alias(mssql):
    """FINDINGS F19: the MERGE referenced the table name, which isn't bound inside MERGE."""
    from engine.db import execute_upsert
    conn = CapturingConn()
    execute_upsert(conn, """
        INSERT INTO jde_member_history (supplier_profile_id, jde_number, member_id)
        VALUES (?, ?, ?)
        ON CONFLICT(supplier_profile_id, jde_number)
        DO UPDATE SET
            member_id = excluded.member_id,
            occurrence_count = occurrence_count + 1,
            updated_at = CURRENT_TIMESTAMP
    """, (1, "J1", 2))
    assert "occurrence_count = tgt.occurrence_count + 1" in conn.sql
    assert "member_id = src.member_id" in conn.sql
    assert "GETUTCDATE()" in conn.sql
