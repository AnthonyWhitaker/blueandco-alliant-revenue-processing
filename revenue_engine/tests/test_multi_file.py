"""
Combining several files for one month (engine/combine.py), duplicate
reporting, and large inputs. Mock data: tests/medline_mock.py.
"""

import csv
import random
import time
from datetime import date
from io import BytesIO

import openpyxl
import pandas as pd
import pytest

from core import jobs
from engine.combine import assign_duplicate_groups
from engine.gpo_key import load_gpo_key
from engine.supplier_presets import MEDLINE, apply_preset

from . import medline_mock as mock
from .conftest import make_members


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))


@pytest.fixture
def sid(conn, tmp_path):
    make_members(conn)
    supplier_id = apply_preset(conn, MEDLINE)
    load_gpo_key(conn, supplier_id, str(mock.write_key_csv(tmp_path / "key.csv")))
    return supplier_id


def xlsx(tmp_path, name, rows):
    return name, mock.write_medline_xlsx(tmp_path / name, rows).read_bytes()


def run(conn, sid, files):
    job = jobs.create_job(conn, sid, files, date(2026, 10, 15), timeframe="3Q26")
    return jobs.run_job(conn, job.id)


def upload_rows(conn, job_id):
    return sorted((r.pec, str(r.earned_period), round(r.volume, 2), round(r.total_revenue, 2))
                  for r in jobs.get_preview(conn, job_id).upload)


# --- Combining ---------------------------------------------------------------

def test_split_files_give_the_same_result_as_one_file(conn, sid, tmp_path):
    whole = run(conn, sid, [xlsx(tmp_path, "whole.xlsx", mock.ROWS)])
    parts = [mock.ROWS[0:3], mock.ROWS[3:5], mock.ROWS[5:6], mock.ROWS[6:9], mock.ROWS[9:]]
    split = run(conn, sid, [xlsx(tmp_path, f"part{i}.xlsx", p) for i, p in enumerate(parts, 1)])

    assert split.file_count == 5 and split.original_file_name == "part1.xlsx (+4 more)"
    assert split.row_count == whole.row_count == len(mock.ROWS)
    assert upload_rows(conn, split.id) == upload_rows(conn, whole.id)
    assert split.reconciliation.balanced
    assert split.reconciliation.input.revenue == pytest.approx(mock.INPUT_REVENUE)
    assert split.reconciliation.input.rows_per_file == {f"part{i}.xlsx": len(p) for i, p in enumerate(parts, 1)}
    # Each file's grand-total row is left out, not counted.
    assert split.reconciliation.input.dropped_rows == 5
    assert [(f.original_file_name, f.row_count) for f in jobs.list_job_files(conn, split.id)] == [
        (f"part{i}.xlsx", len(p)) for i, p in enumerate(parts, 1)]


def test_every_row_records_its_source_file_and_row(conn, sid, tmp_path):
    job = run(conn, sid, [xlsx(tmp_path, "a.xlsx", mock.ROWS[:2]), xlsx(tmp_path, "b.xlsx", mock.ROWS[2:4])])
    got = [tuple(r) for r in conn.execute(
        "SELECT source_file, source_row FROM revenue_row WHERE job_id = ? ORDER BY row_number", (job.id,))]
    assert got == [("a.xlsx", 2), ("a.xlsx", 3), ("b.xlsx", 2), ("b.xlsx", 3)]


def test_mixed_csv_and_xlsx_files(conn, sid, tmp_path):
    path = tmp_path / "b.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(mock.HEADERS)
        w.writerows(mock.ROWS[5:])
    job = run(conn, sid, [xlsx(tmp_path, "a.xlsx", mock.ROWS[:5]), ("b.csv", path.read_bytes())])
    assert job.row_count == len(mock.ROWS)
    assert job.reconciliation.balanced and job.reconciliation.input.revenue == pytest.approx(mock.INPUT_REVENUE)


def test_a_bad_file_names_itself_in_the_error(conn, sid, tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["Something", "Else"])
    wb.active.append([1, 2])
    buf = BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="^wrong.xlsx: Required columns not found"):
        run(conn, sid, [xlsx(tmp_path, "a.xlsx", mock.ROWS[:2]), ("wrong.xlsx", buf.getvalue())])
    # a.xlsx was processed before wrong.xlsx failed: none of its rows are left behind.
    assert conn.execute("SELECT COUNT(*) FROM revenue_row").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM revenue_job").fetchone()[0] == "failed"


# --- Duplicates --------------------------------------------------------------

def test_assign_duplicate_groups():
    df = pd.DataFrame({
        "a": ["1", "1", "2", "1", None, None],
        "b": ["x", "x", "y", "z", None, None],
        "__source_file": ["f1", "f2", "f1", "f1", "f1", "f2"],
        "__source_row": [2, 2, 3, 4, 5, 3],
    })
    groups = assign_duplicate_groups(df)
    assert groups.isna().tolist() == [False, False, True, True, False, False]
    assert groups[0] == groups[1] and groups[4] == groups[5] and groups[0] != groups[4]


def test_a_missing_column_counts_as_blank_for_duplicates():
    from engine.combine import DuplicateFinder
    finder = DuplicateFinder()
    finder.add(pd.DataFrame({"a": ["1", "2"], "b": ["", "y"]}), 0)
    finder.add(pd.DataFrame({"a": ["1", "2"]}), 1)               # no column b at all
    result = finder.result()
    assert result.groups.tolist() == [1, 0, 1, 0]
    assert (result.group_count, result.row_count, result.cross_file_groups) == (1, 2, 1)


def test_same_values_in_different_columns_are_not_duplicates():
    groups = assign_duplicate_groups(pd.DataFrame({"a": ["x", "y"], "b": ["y", "x"]}))
    assert groups.isna().all()


def test_duplicates_across_files_are_reported_not_removed(conn, sid, tmp_path):
    overlap = mock.ROWS[0:2]                       # the same two invoice lines sent in both files
    job = run(conn, sid, [xlsx(tmp_path, "a.xlsx", mock.ROWS[0:4]), xlsx(tmp_path, "b.xlsx", overlap)])

    assert job.row_count == 6                      # kept
    assert job.reconciliation.balanced
    assert job.reconciliation.input.revenue == pytest.approx(3.00 + 1.50 + 6.00 + 0.30 + 3.00 + 1.50)
    dup = conn.execute(
        "SELECT duplicate_group, source_file FROM revenue_row WHERE job_id = ? AND duplicate_group IS NOT NULL "
        "ORDER BY duplicate_group, source_file", (job.id,)).fetchall()
    assert [tuple(r) for r in dup] == [(1, "a.xlsx"), (1, "b.xlsx"), (2, "a.xlsx"), (2, "b.xlsx")]
    assert job.status == "needs_review"
    warning = next(e for e in jobs.list_exceptions(conn, job.id) if e.exception_type == "duplicate_rows")
    assert warning.severity == "warning" and "2 spanning more than one file" in warning.message

    report = next(o for o in jobs.list_outputs(conn, job.id) if o.artifact_type == "exception_report")
    ws = openpyxl.load_workbook(BytesIO(jobs.read_output(conn, job.id, report.id)[1]))["Duplicates"]
    data = list(ws.iter_rows(values_only=True))
    assert len(data) == 5 and {r[1] for r in data[1:]} == {"Yes"}


def test_duplicates_within_one_file_are_reported_as_info(conn, sid, tmp_path):
    job = run(conn, sid, [xlsx(tmp_path, "a.xlsx", mock.ROWS[0:4] + [mock.ROWS[0]])])
    warning = next(e for e in jobs.list_exceptions(conn, job.id) if e.exception_type == "duplicate_rows")
    assert warning.severity == "info"
    assert job.row_count == 5 and job.status == "completed"


def test_same_file_uploaded_twice(conn, sid, tmp_path):
    f = xlsx(tmp_path, "a.xlsx", mock.ROWS[0:4])
    job = run(conn, sid, [f, ("a copy.xlsx", f[1])])
    assert job.row_count == 8
    assert any(e.exception_type == "duplicate_file" and "a copy.xlsx is byte-for-byte identical to a.xlsx"
               in e.message for e in jobs.list_exceptions(conn, job.id))


def test_tier_c_suppliers_take_one_file(conn, tmp_path):
    from core.errors import InvalidInputError
    from .conftest import make_supplier
    pivot = make_supplier(conn, name="Komet", job_type="pivot_allocation")
    f = xlsx(tmp_path, "a.xlsx", mock.ROWS[:1])
    with pytest.raises(InvalidInputError, match="one file per job"):
        jobs.create_job(conn, pivot, [f, f], date(2026, 10, 15))


# --- Large input -------------------------------------------------------------

LARGE_ROWS = 120_000


def test_large_combined_input_is_not_truncated(conn, sid, tmp_path):
    """120k invoice lines across two CSV files: every row stored, totals reconcile to the cent."""
    rng = random.Random(3)
    accounts = [(a, n, s) for a, n, s, *_ in mock.KEY_ROWS]
    files, fee_total = [], 0.0
    for part in range(2):
        path = tmp_path / f"big{part}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(mock.HEADERS)
            for i in range(LARGE_ROWS // 2):
                acct, name, street = accounts[i % len(accounts)] if i % 50 else ("9999", "UNKNOWN", "1 X ST")
                ext = round(rng.uniform(-20, 500), 2)
                fee = round(ext * 0.03, 2)
                fee_total += fee
                w.writerow([f"P{part}-{i}", f"2026-09-{1 + i % 28:02d}", acct, name, street, "AUSTIN", "TX",
                            "78701", "", ext, 0.03, fee])
            w.writerow([""] * 8 + ["Grand Total", "", "", ""])
        files.append((path.name, path.read_bytes()))

    started = time.monotonic()
    job = run(conn, sid, files)
    elapsed = time.monotonic() - started

    stored = conn.execute("SELECT COUNT(*), SUM(fee_amount) FROM revenue_row WHERE job_id = ?", (job.id,)).fetchone()
    assert job.row_count == stored[0] == LARGE_ROWS
    assert stored[1] == pytest.approx(fee_total)
    assert job.reconciliation.balanced
    assert job.reconciliation.input.revenue == pytest.approx(fee_total)
    assert job.reconciliation.exception_rows == LARGE_ROWS // 50
    assert len(jobs.get_preview(conn, job.id).upload) == 3     # EC001, EC002, EC003 for September
    assert elapsed < 120, f"processing {LARGE_ROWS} rows took {elapsed:.0f}s"
