"""The streaming XLSX reader gives the same frame as pd.read_excel(dtype=str), with less memory."""

from datetime import date, datetime

import openpyxl
import pandas as pd
import pytest

from engine import file_reader
from engine.file_reader import read_xlsx_streaming


def _workbook(path, rows, title="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


TRICKY = [
    ["Report title"],                                   # above the header
    [],
    ["Account", "Name", None, "Amount", "Amount", "Date", "Zip", "Flag"],
    ["001", "General", "x", 10.0, 2.5, datetime(2026, 9, 1), 2134, True],
    [None, None, None, None, None, None, None, None],     # blank row in the middle
    [1002, "  spaced  ", None, -3, 0.1 + 0.2, date(2026, 9, 2), "02134", False],
    [1003, "", None, 1e15, None, None, None, None, "extra column"],
    [None, "Grand Total", None, 7.5],
    [],
    [],                                                   # trailing blank rows
]


@pytest.mark.parametrize("header_row", [2])
def test_same_frame_as_read_excel(tmp_path, header_row):
    path = _workbook(tmp_path / "t.xlsx", TRICKY)
    expected = pd.read_excel(path, header=header_row, dtype=str)
    got = read_xlsx_streaming(str(path), 0, header_row)
    pd.testing.assert_frame_equal(got, expected, check_dtype=False, check_column_type=False)


def test_by_sheet_name_and_max_columns(tmp_path):
    path = tmp_path / "t.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Summary"
    wb.active.append(["not this"])
    ws = wb.create_sheet("Data")
    ws.append(["A", "B", "C"])
    ws.append([1, 2, 3])
    wb.save(path)
    got = read_xlsx_streaming(str(path), "Data", 0, max_columns=2)
    assert list(got.columns) == ["A", "B"] and got.iloc[0].tolist() == ["1", "2"]
    assert list(read_xlsx_streaming(str(path), "Data", 0, max_columns=10).columns) == ["A", "B", "C"]


def test_chunks_join_up(tmp_path, monkeypatch):
    monkeypatch.setattr(file_reader, "_STREAM_CHUNK", 3)
    rows = [["N", "V"]] + [[i, f"v{i}"] for i in range(10)] + [[None, None], [99, "last"]]
    path = _workbook(tmp_path / "t.xlsx", rows)
    pd.testing.assert_frame_equal(read_xlsx_streaming(str(path)), pd.read_excel(path, dtype=str),
                                  check_dtype=False, check_column_type=False)


def test_header_only(tmp_path):
    path = _workbook(tmp_path / "t.xlsx", [["A", "B"]])
    got = read_xlsx_streaming(str(path))
    assert list(got.columns) == ["A", "B"] and got.empty
