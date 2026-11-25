"""
A client file in the Alliant upload-template layout (Facility ID, Contract ID, Total Revenue filled in;
most other columns blank): text amounts like '$26.00' and blank Earned Period dates.
"""

from datetime import date

import openpyxl
import pytest

from core import jobs
from engine.normalizer import normalize_key
from engine.supplier_presets import apply_preset

from .fixtures import make_members

HEADERS = ["Facility ID", "Contract ID", "Volume", "Total Revenue", "Revenue Type", "Keep/Return?", "Earned Period",
           "Date Received", "Processed Period", "Pmt For (Time Frame)", "Check #", "Check Date", "Check Received",
           "Check Amount", "Comments", "Date Refunded", "Rebate Retained", "Amount Retained", "Retained Reason"]

PRESET = {
    "profile": dict(name="Template Client", identity_mode="member_match", revenue_type="Admin Fee",
                    keep_return_default="Keep", header_row=0, skip_rows_where_col_null="Facility ID"),
    "column_map": [("Facility ID", "customer_name"), ("Contract ID", "contract_number"),
                   ("Volume", "sales_amount"), ("Total Revenue", "fee_amount"), ("Earned Period", "process_date")],
}

# facility, revenue (numbers and text), earned period
ROWS = [
    ("General Hospital", 136.75, None),
    ("General Hospital", "$26.00", None),
    ("Mercy Medical Center", "($10.00)", None),
    ("Mercy Medical Center", 0.0, None),
    (None, 71.50, None),                                  # no Facility ID: left out as a total row
    ("Desert Regional Health", "1,000.25", "2026-08-14"),  # dated row keeps its own month
]


def run(conn, tmp_path, monkeypatch, timeframe):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))
    members = make_members(conn)
    sid = apply_preset(conn, PRESET)
    # Names only (no city/state/ZIP): they match through approved aliases, as after a first month's review.
    for name, key in (("General Hospital", "general"), ("Mercy Medical Center", "mercy"),
                      ("Desert Regional Health", "desert")):
        conn.execute("INSERT INTO member_alias (member_id, supplier_profile_id, raw_name, canonical_key) "
                     "VALUES (?, ?, ?, ?)", (members[key], sid, name, normalize_key(name)))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for name, revenue, earned in ROWS:
        ws.append([name, "AL-X-1" if name else None, None, revenue, None, None, earned] + [None] * 12)
    path = tmp_path / "client.xlsx"
    wb.save(path)
    job = jobs.create_job(conn, sid, [("Client 2026.10.15.xlsx", path.read_bytes())], date(2026, 10, 15),
                          timeframe=timeframe)
    job = jobs.run_job(conn, job.id)
    out = {r["facility_id"]: (r["total_revenue"], r["earned_period"]) for r in conn.execute(
        "SELECT facility_id, total_revenue, earned_period FROM revenue_output_row WHERE job_id = ?", (job.id,))}
    messages = [r[0] for r in conn.execute("SELECT message FROM exception WHERE job_id = ?", (job.id,))]
    return out, messages


def test_text_amounts_are_read_as_numbers(conn, tmp_path, monkeypatch):
    out, messages = run(conn, tmp_path, monkeypatch, "3Q26")
    assert out["SF-GEN"][0] == pytest.approx(162.75)        # 136.75 + '$26.00'
    assert out["SF-MER"][0] == pytest.approx(-10.00)        # '($10.00)' is negative
    assert out["SF-DES"][0] == pytest.approx(1000.25)
    assert not any("isn't a number" in m for m in messages)


def test_blank_earned_period_uses_the_runs_timeframe(conn, tmp_path, monkeypatch):
    out, messages = run(conn, tmp_path, monkeypatch, "3Q26")
    assert out["SF-GEN"][1] == out["SF-MER"][1] == "2026-07-01"   # not October, the file name's month
    assert out["SF-DES"][1] == "2026-08-01"
    assert any("4 row(s) have no usable date (4 blank); Earned Period set to the first month of 3Q26" in m
               for m in messages)


def test_blank_earned_period_without_timeframe_uses_file_name_date_and_says_so(conn, tmp_path, monkeypatch):
    out, messages = run(conn, tmp_path, monkeypatch, None)
    assert out["SF-GEN"][1] == "2026-10-01"
    assert any("Earned Period set to the date in the file name" in m for m in messages)
