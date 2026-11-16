"""
Read a job's source files one at a time, and find duplicate rows across them.

One month can arrive as several files (a Medline month can be several
million rows split into 1M-row spreadsheets). Each file is read with the
supplier's column map (file_reader.read_supplier_file_detailed), tagged with
its file name and spreadsheet row, processed, and released before the next
is read, so memory depends on the largest file, not on the month.

Duplicate rows are found and reported, never removed: rows whose original
content is identical in every column (ignoring which file/row they came from)
share a duplicate_group number. A group is "cross-file" when its rows come
from more than one file, the usual sign that the same data was sent twice.
Rows are compared by a 128-bit fingerprint of their content, kept for every
row (16 bytes each) until all files are read.
"""

import hashlib
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np
import pandas as pd

from .file_reader import SOURCE_ROW_COL, ReadResult, read_supplier_file_detailed

SOURCE_FILE_COL = "__source_file"
DUPLICATE_GROUP_COL = "__duplicate_group"
_META_COLS = (SOURCE_FILE_COL, SOURCE_ROW_COL, DUPLICATE_GROUP_COL)


@dataclass(frozen=True)
class SourceFile:
    name: str      # file name as uploaded (shown in reports)
    path: str      # where it is stored


def content_columns(df: pd.DataFrame) -> list[str]:
    """The columns that make up a row's content (everything except where it came from)."""
    return [c for c in df.columns if c not in _META_COLS]


def read_files(files: list[SourceFile], supplier_profile: dict, conn: object) -> Iterator[tuple[SourceFile, ReadResult]]:
    """Each file's ReadResult in upload order, read only when the previous one is done. Errors name the file."""
    if not files:
        raise ValueError("The job has no source files.")
    for f in files:
        try:
            result = read_supplier_file_detailed(f.path, supplier_profile, conn)
        except ValueError as e:
            raise ValueError(f"{f.name}: {e}") from e
        result.df[SOURCE_FILE_COL] = f.name
        if not result.dropped.empty:
            result.dropped[SOURCE_FILE_COL] = f.name
        yield f, result
        del result      # release this file before reading the next


def _column_keys(column: str) -> tuple[str, str]:
    """Two 16-character hash keys per column name, so the same value in different columns hashes differently."""
    digest = hashlib.sha256(str(column).encode()).hexdigest()
    return digest[:16], digest[16:32]


@dataclass(frozen=True)
class DuplicateGroups:
    groups: np.ndarray            # per row in reading order: group number (1, 2, ...) or 0 for a unique row
    group_count: int = 0          # groups of identical rows
    row_count: int = 0            # rows that belong to a group
    cross_file_groups: int = 0    # groups spanning more than one file


class DuplicateFinder:
    """Fingerprint each file's rows as they are read; group identical rows once all files are in."""

    def __init__(self) -> None:
        self._h1: list[np.ndarray] = []
        self._h2: list[np.ndarray] = []
        self._file: list[np.ndarray] = []

    def add(self, df: pd.DataFrame, file_index: int) -> None:
        # Sum of per-column hashes of the non-blank cells: a missing column and a blank cell are the
        # same, as when the files were concatenated. Compared as text, so '10' and '10.0' differ.
        h1 = np.zeros(len(df), dtype=np.uint64)
        h2 = np.zeros(len(df), dtype=np.uint64)
        for col in content_columns(df):
            text = df[col].astype("string").fillna("")
            blank = (text == "").to_numpy(dtype=bool)
            k1, k2 = _column_keys(col)
            c1 = pd.util.hash_pandas_object(text, index=False, hash_key=k1).to_numpy(copy=True)
            c2 = pd.util.hash_pandas_object(text, index=False, hash_key=k2).to_numpy(copy=True)
            c1[blank] = 0
            c2[blank] = 0
            h1 += c1
            h2 += c2
        self._h1.append(h1)
        self._h2.append(h2)
        self._file.append(np.full(len(df), file_index, dtype=np.int32))

    def result(self) -> DuplicateGroups:
        if not self._h1:
            return DuplicateGroups(np.zeros(0, dtype=np.int64))
        fp = pd.DataFrame({"h1": np.concatenate(self._h1), "h2": np.concatenate(self._h2)})
        files = np.concatenate(self._file)
        groups = np.zeros(len(fp), dtype=np.int64)
        is_dup = fp.duplicated(keep=False).to_numpy()
        if not is_dup.any():
            return DuplicateGroups(groups)
        # Numbered in order of first appearance.
        groups[is_dup] = fp[is_dup].groupby(["h1", "h2"], sort=False).ngroup().to_numpy() + 1
        files_per_group = pd.Series(files[is_dup]).groupby(groups[is_dup]).nunique()
        return DuplicateGroups(
            groups=groups,
            group_count=int(files_per_group.size),
            row_count=int(is_dup.sum()),
            cross_file_groups=int((files_per_group > 1).sum()),
        )


def assign_duplicate_groups(df: pd.DataFrame) -> pd.Series:
    """Group number (1, 2, ...) for rows identical to at least one other row; <NA> for unique rows."""
    finder = DuplicateFinder()
    finder.add(df, 0)
    groups = pd.Series(finder.result().groups, index=df.index).astype("Int64")
    return groups.mask(groups == 0)


def amount_totals(df: pd.DataFrame, column: str) -> tuple[float, int]:
    """(sum of the parseable amounts, count of non-blank values that aren't numbers) for one canonical column."""
    if df.empty or column not in df.columns:
        return 0.0, 0
    text = df[column].astype("string").str.replace(",", "", regex=False).str.strip()
    numbers = pd.to_numeric(text, errors="coerce")
    unparseable = int((numbers.isna() & text.notna() & (text != "")).sum())
    return float(numbers.sum()), unparseable


def to_optional_str(value) -> Optional[str]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s or None
