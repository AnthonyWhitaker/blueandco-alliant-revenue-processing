"""
Database connection and initialization for the Revenue Processing Engine.
Uses SQLite for local/prototype use. Designed to be swapped for Azure SQL.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "revenue.db"
_SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Return a SQLite connection with row_factory set to sqlite3.Row
    so columns can be accessed by name.
    """
    path = db_path or os.environ.get("REVENUE_DB_PATH", str(_DEFAULT_DB_PATH))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Initialize the database: create tables if they don't exist.
    Returns an open connection.
    """
    conn = get_connection(db_path)
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    return conn


def dict_from_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(zip(row.keys(), tuple(row)))


def rows_to_dicts(rows) -> list[dict]:
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict_from_row(r) for r in rows]
