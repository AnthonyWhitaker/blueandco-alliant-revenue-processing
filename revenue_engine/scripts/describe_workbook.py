"""
Describe a spreadsheet's structure without printing any of its data, so it can be shared safely.

    python scripts/describe_workbook.py "Airgas Q1.xlsx" ["Medline Account Key.xlsx" ...]

For every sheet: the header row (and which row it is on), the number of data rows, and per column
the kind of values (number / date / text / blank), how many are blank, and the most common "shape"
of text values: letters become A, digits 9 (e.g. a Salesforce ID 001Ab00000XyZ12 -> 999AA99999AAA99,
a timeframe 1Q26 -> 9A99). No cell values, names, amounts or totals are printed.
"""

import csv
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl

HEADER_SEARCH_ROWS = 20


def _kind(v) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return "blank"
    if isinstance(v, (datetime, date)):
        return "date"
    if isinstance(v, (int, float)):
        return "number"
    return "text"


def _shape(v) -> str:
    s = str(v).strip()
    out = "".join("A" if c.isalpha() else "9" if c.isdigit() else c for c in s)
    return out if len(out) <= 24 else out[:24] + "…"


def _header_index(rows: list[list]) -> int:
    """The first row among the top rows with the most text cells (title rows above it have fewer)."""
    best, best_count = 0, 0
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        count = sum(1 for v in row if _kind(v) == "text")
        if count > best_count and all(_kind(v) in ("text", "blank") for v in row):
            best, best_count = i, count
    return best


def describe(name: str, rows_iter) -> None:
    head, rows = [], iter(rows_iter)
    for row in rows:
        head.append(list(row))
        if len(head) >= HEADER_SEARCH_ROWS:
            break
    if not head:
        print(f"\n== {name}: empty")
        return
    h = _header_index(head)
    headers = [str(v).strip() if v is not None else "" for v in head[h]]
    kinds = [Counter() for _ in headers]
    shapes = [Counter() for _ in headers]
    n = 0

    def add(row):
        nonlocal n
        row = list(row)
        if all(_kind(v) == "blank" for v in row):
            return
        n += 1
        for i in range(len(headers)):
            v = row[i] if i < len(row) else None
            k = _kind(v)
            kinds[i][k] += 1
            if k == "text":
                shapes[i][_shape(v)] += 1

    for row in head[h + 1:]:
        add(row)
    for row in rows:
        add(row)

    print(f"\n== {name}: header on row {h + 1}, {n} data rows, {len(headers)} columns")
    for i, col in enumerate(headers):
        if not col and not (kinds[i].total() - kinds[i]["blank"]):
            continue
        k = ", ".join(f"{kk} {c}" for kk, c in kinds[i].most_common())
        shape = f"  shape {shapes[i].most_common(1)[0][0]!r}" if shapes[i] else ""
        print(f"  {openpyxl.utils.get_column_letter(i + 1):>3}  {col or '(no header)':<40} {k}{shape}")


def main(paths: list[str]) -> None:
    if not paths:
        sys.exit(__doc__)
    for p in paths:
        path = Path(p)
        print(f"\n##### {path.name}")
        if path.suffix.lower() == ".csv":
            with open(path, newline="", encoding="utf-8-sig") as f:
                describe("(csv)", csv.reader(f))
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            describe(ws.title, ws.iter_rows(values_only=True))
        wb.close()


if __name__ == "__main__":
    main(sys.argv[1:])
