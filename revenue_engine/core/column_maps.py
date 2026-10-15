"""Supplier column mappings (supplier_column_map) and auto-inference from a sample file."""

from pathlib import Path

import pandas as pd

from engine.db import execute_insert_or_replace
from engine.file_reader import infer_column_map
from models.column_maps import ColumnMapping, ColumnMappingInput, InferredMapping, InferredMappingInput

from .errors import InvalidInputError
from .rows import row_dict, rows_dicts
from .suppliers import get_supplier

_UPSERT = """
    INSERT OR REPLACE INTO supplier_column_map
        (supplier_profile_id, source_column, canonical_field, is_required)
    VALUES (?, ?, ?, ?)
"""


def list_mappings(conn, supplier_id: int) -> list[ColumnMapping]:
    get_supplier(conn, supplier_id)
    rows = conn.execute(
        "SELECT * FROM supplier_column_map WHERE supplier_profile_id = ? ORDER BY source_column",
        (supplier_id,),
    ).fetchall()
    return [ColumnMapping.model_validate(r) for r in rows_dicts(rows)]


def save_mapping(conn, supplier_id: int, data: ColumnMappingInput) -> ColumnMapping:
    """Create or replace the mapping for data.source_column."""
    get_supplier(conn, supplier_id)
    execute_insert_or_replace(
        conn, _UPSERT, (supplier_id, data.source_column, data.canonical_field, int(data.is_required)),
    )
    conn.commit()
    return _get_by_source(conn, supplier_id, data.source_column)


def save_inferred_mappings(conn, supplier_id: int, mappings: list[InferredMappingInput]) -> list[ColumnMapping]:
    """Save a reviewed set of inferred mappings, all as optional (is_required = 0)."""
    get_supplier(conn, supplier_id)
    for m in mappings:
        execute_insert_or_replace(conn, _UPSERT, (supplier_id, m.source_column, m.canonical_field, 0))
    conn.commit()
    return list_mappings(conn, supplier_id)


def infer_from_sample(conn, supplier_id: int, file_path: str) -> list[InferredMapping]:
    """Suggest mappings from the header row of a sample file. Nothing is saved."""
    get_supplier(conn, supplier_id)
    suffix = Path(file_path).suffix.lower()
    try:
        if suffix in (".xlsx", ".xls", ".xlsm"):
            df = pd.read_excel(file_path, nrows=5)
        elif suffix == ".csv":
            df = pd.read_csv(file_path, nrows=5)
        else:
            raise InvalidInputError(f"Unsupported sample file type '{suffix}'.")
    except InvalidInputError:
        raise
    except Exception as e:
        raise InvalidInputError(f"Could not read the sample file: {e}") from e
    suggestions = infer_column_map(df, supplier_id, conn)
    return [InferredMapping(source_column=src, canonical_field=canon) for src, canon in suggestions.items()]


def _get_by_source(conn, supplier_id: int, source_column: str) -> ColumnMapping:
    row = conn.execute(
        "SELECT * FROM supplier_column_map WHERE supplier_profile_id = ? AND source_column = ?",
        (supplier_id, source_column),
    ).fetchone()
    return ColumnMapping.model_validate(row_dict(row))
