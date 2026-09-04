"""
Database connection and initialization for the Revenue Processing Engine.

Supports two backends, selected automatically by environment variable:
  - SQLite  (default, local dev) — REVENUE_DB_PATH or data/revenue.db
  - Azure SQL via pyodbc         — set AZURE_SQL_CONNECTION_STRING

Both backends expose the same cursor/row interface: rows support
``row["column_name"]``, ``row[0]``, ``dict(row)``, and ``row.keys()``.
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "revenue.db"
_SCHEMA_DIR = Path(__file__).parent.parent / "db"


def is_mssql() -> bool:
    return bool(os.environ.get("AZURE_SQL_CONNECTION_STRING"))


# ---------------------------------------------------------------------------
# pyodbc → sqlite3-compatible row / cursor / connection wrappers
# ---------------------------------------------------------------------------

class DictRow:
    """Makes a pyodbc.Row behave like sqlite3.Row."""
    __slots__ = ("_data", "_columns")

    def __init__(self, row, columns):
        self._data = tuple(row)
        self._columns = columns

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[key]
        try:
            return self._data[self._columns.index(key)]
        except ValueError:
            raise KeyError(key)

    def __contains__(self, key):
        return key in self._columns

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return list(self._columns)

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class DictCursor:
    """Wraps a pyodbc cursor so fetchone/fetchall return DictRow objects."""

    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def _columns(self):
        if self._cursor.description:
            return [col[0] for col in self._cursor.description]
        return []

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return DictRow(row, self._columns())

    def fetchall(self):
        cols = self._columns()
        return [DictRow(r, cols) for r in self._cursor.fetchall()]

    def __iter__(self):
        cols = self._columns()
        for r in self._cursor:
            yield DictRow(r, cols)


class DictConnection:
    """Wraps a pyodbc connection so every execute returns a DictCursor."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql, params=None):
        cursor = self._conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return DictCursor(cursor)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def get_connection(db_path: Optional[str] = None):
    """
    Return a database connection.
    Uses AZURE_SQL_CONNECTION_STRING when set, otherwise SQLite.
    """
    if is_mssql():
        return _get_pyodbc_connection()
    return _get_sqlite_connection(db_path)


def _get_sqlite_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("REVENUE_DB_PATH", str(_DEFAULT_DB_PATH))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _get_pyodbc_connection() -> DictConnection:
    import pyodbc
    conn_str = os.environ["AZURE_SQL_CONNECTION_STRING"]
    raw = pyodbc.connect(conn_str, autocommit=False)
    return DictConnection(raw)


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

def init_db(db_path: Optional[str] = None):
    """
    Initialize the database (create tables if they don't exist).
    Returns an open connection.
    """
    conn = get_connection(db_path)

    if is_mssql():
        schema_sql = (_SCHEMA_DIR / "schema_mssql.sql").read_text(encoding="utf-8")
        for batch in schema_sql.split("\nGO\n"):
            batch = batch.strip()
            if batch:
                conn.execute(batch)
        conn.commit()
    else:
        schema_sql = (_SCHEMA_DIR / "schema.sql").read_text(encoding="utf-8")
        if isinstance(conn, sqlite3.Connection):
            conn.executescript(schema_sql)
        else:
            conn.execute(schema_sql)
        conn.commit()

    # Lightweight schema migrations for existing databases
    _safe_add_column(conn, "supplier_profile", "max_read_columns", "INTEGER")
    _safe_add_column(conn, "supplier_profile", "job_type", "TEXT NOT NULL DEFAULT 'row_level'")
    _safe_add_column(conn, "supplier_profile", "requires_manual_confirmation", "INTEGER NOT NULL DEFAULT 0")

    return conn


def _safe_add_column(conn, table: str, column: str, col_type: str) -> None:
    """Add a column to a table if it doesn't already exist."""
    if is_mssql():
        exists = conn.execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? AND COLUMN_NAME = ?",
            (table, column),
        ).fetchone()
        if exists:
            return
        try:
            conn.execute(f"ALTER TABLE {table} ADD {column} {col_type}")
            conn.commit()
        except Exception:
            conn.rollback()
        return

    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except Exception:
        pass  # column already exists


# ---------------------------------------------------------------------------
# Cross-DB helpers
# ---------------------------------------------------------------------------

def execute_select(conn, sql_sqlite: str, params=None):
    """
    Execute a SELECT that may end in a SQLite-style trailing ``LIMIT N``.
    For MSSQL, ``LIMIT N`` is not valid syntax, so it's rewritten as
    ``SELECT TOP N ...`` automatically. For SQLite the SQL runs unchanged.
    """
    sql = sql_sqlite
    if is_mssql():
        m = re.search(r"\bLIMIT\s+(\d+)\s*$", sql.strip(), re.IGNORECASE)
        if m:
            n = m.group(1)
            stripped = sql[: m.start()].rstrip()
            sql = re.sub(
                r"^(\s*SELECT\s+)(DISTINCT\s+)?",
                lambda mo: f"{mo.group(1)}TOP {n} {mo.group(2) or ''}",
                stripped,
                count=1,
                flags=re.IGNORECASE,
            )
    if params:
        return conn.execute(sql, params)
    return conn.execute(sql)


def get_last_insert_id(conn, cursor) -> int:
    """Get the auto-generated ID from the most recent INSERT."""
    if is_mssql():
        # SCOPE_IDENTITY() is scoped to the SQL *batch* that produced it, so
        # it comes back NULL once called from a separate conn.execute() call
        # (a new batch). @@IDENTITY is session-scoped instead of
        # batch-scoped, which is what we need here since there are no
        # triggers in this schema that could insert into another identity
        # column and make @@IDENTITY point at the wrong table.
        row = conn.execute("SELECT @@IDENTITY AS id").fetchone()
        return int(row[0])
    return cursor.lastrowid


def execute_insert_ignore(conn, sql_sqlite: str, params=None):
    """
    Execute an INSERT that silently skips on unique-constraint violation.
    *sql_sqlite* should be the SQLite ``INSERT OR IGNORE`` statement;
    for MSSQL the same statement is run inside a try/except.
    """
    if is_mssql():
        plain_sql = sql_sqlite.replace("INSERT OR IGNORE", "INSERT")
        try:
            conn.execute(plain_sql, params)
        except Exception:
            pass
    else:
        conn.execute(sql_sqlite, params)


def execute_upsert(conn, sql_sqlite: str, params=None):
    """
    Execute an INSERT ... ON CONFLICT ... DO UPDATE statement.
    For MSSQL, splits into INSERT (try) / UPDATE (except) automatically
    by parsing the SQLite upsert SQL.

    Falls back to a plain try-insert / except-do-nothing if parsing fails.
    """
    if not is_mssql():
        conn.execute(sql_sqlite, params)
        return

    import re
    m = re.search(
        r"INSERT\s+INTO\s+(\S+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*"
        r"ON\s+CONFLICT\s*\(([^)]+)\)\s*DO\s+UPDATE\s+SET\s+(.+)",
        sql_sqlite,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        try:
            conn.execute(sql_sqlite.split("ON CONFLICT")[0].strip(), params)
        except Exception:
            pass
        return

    table = m.group(1)
    insert_cols = [c.strip() for c in m.group(2).split(",")]
    conflict_cols = [c.strip() for c in m.group(4).split(",")]
    update_clause = m.group(5).strip()

    update_clause = re.sub(r"\bexcluded\.(\w+)", r"src.\1", update_clause)
    update_clause = re.sub(
        r"\b(?<!\.)occurrence_count\b",
        f"{table}.occurrence_count",
        update_clause,
    )
    update_clause = update_clause.replace("CURRENT_TIMESTAMP", "GETUTCDATE()")

    placeholders = m.group(3).strip()
    src_cols = ", ".join(
        f"{p.strip()} AS [{c}]" for p, c in zip(placeholders.split(","), insert_cols)
    )
    on_clause = " AND ".join(
        f"tgt.[{c}] = src.[{c}]" for c in conflict_cols
    )
    insert_col_list = ", ".join(f"[{c}]" for c in insert_cols)
    insert_val_list = ", ".join(f"src.[{c}]" for c in insert_cols)

    merge_sql = (
        f"MERGE [{table}] AS tgt "
        f"USING (SELECT {src_cols}) AS src "
        f"ON {on_clause} "
        f"WHEN MATCHED THEN UPDATE SET {update_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_col_list}) VALUES ({insert_val_list});"
    )
    conn.execute(merge_sql, params)


def execute_insert_or_replace(conn, sql_sqlite: str, params=None):
    """
    Execute an INSERT OR REPLACE.  For MSSQL, does DELETE + INSERT.
    """
    if not is_mssql():
        conn.execute(sql_sqlite, params)
        return

    import re
    m = re.search(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\S+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        sql_sqlite,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        try:
            plain = sql_sqlite.replace("INSERT OR REPLACE", "INSERT")
            conn.execute(plain, params)
        except Exception:
            pass
        return

    table = m.group(1)
    cols = [c.strip() for c in m.group(2).split(",")]
    placeholders = m.group(3).strip()

    plain_insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    try:
        conn.execute(plain_insert, params)
    except Exception:
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        update_sql = f"UPDATE {table} SET {set_clause} WHERE {cols[0]} = ? AND {cols[1]} = ?"
        conn.execute(update_sql, list(params) + [params[0], params[1]])


# ---------------------------------------------------------------------------
# Row helpers (unchanged API)
# ---------------------------------------------------------------------------

def dict_from_row(row) -> dict:
    """Convert a sqlite3.Row or DictRow to a plain dict."""
    return dict(zip(row.keys(), tuple(row)))


def rows_to_dicts(rows) -> list[dict]:
    """Convert a list of Row objects to a list of dicts."""
    return [dict_from_row(r) for r in rows]
