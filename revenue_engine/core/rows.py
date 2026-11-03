"""Small helpers for turning DB rows and DataFrames into plain dicts."""

import math
from typing import Any, Optional

import pandas as pd

from engine.db import dict_from_row


def row_dict(row) -> Optional[dict[str, Any]]:
    return dict_from_row(row) if row is not None else None


def rows_dicts(rows) -> list[dict[str, Any]]:
    return [dict_from_row(r) for r in rows]


def frame_records(df: pd.DataFrame, rename: dict[str, str]) -> list[dict[str, Any]]:
    """DataFrame → list of dicts with renamed keys and NaN/NaT turned into None."""
    records = df.rename(columns=rename).to_dict(orient="records")
    return [
        {k: (None if _is_missing(v) else v) for k, v in rec.items()}
        for rec in records
    ]


def _is_missing(v: Any) -> bool:
    if v is None or v is pd.NaT:
        return True
    return isinstance(v, float) and math.isnan(v)
