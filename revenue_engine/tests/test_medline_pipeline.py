"""
Medline in gpo_key mode (mock.MEDLINE_GPO_KEY) through the real pipeline: GPO ID
verification, rollup to one row per GPO ID per month, the Upload template,
the totals check, and the output files. Mock data: tests/medline_mock.py.
"""

from datetime import date, datetime
from io import BytesIO

import openpyxl
import pytest

from core import jobs
from engine.constants import SF_UPLOAD_COLUMNS
from engine.exporter import OutputLocationError, export_job, reserve_output_path
from engine.gpo_key import load_gpo_key
from engine.processor import process_job
from engine.reconciliation import input_totals_from_json, reconcile
from engine.supplier_presets import MEDLINE, ROLLED_UP_PLACEHOLDER, apply_preset

from . import medline_mock as mock
from .conftest import create_job_row, make_members

pytestmark = pytest.mark.usefixtures("dirs")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))


@pytest.fixture
def medline(conn, tmp_path):
    members = make_members(conn)
    sid = apply_preset(conn, mock.MEDLINE_GPO_KEY)
    conn.execute("UPDATE supplier_profile SET contract_id = 'CON-MED' WHERE id = ?", (sid,))
    conn.commit()
    load_gpo_key(conn, sid, str(mock.write_key_csv(tmp_path / "key.csv")))
    return {"supplier_id": sid, "members": members}


def run_medline(conn, sid, tmp_path, rows=mock.ROWS, timeframe="3Q26"):
    content = mock.write_medline_xlsx(tmp_path / "medline.xlsx", rows).read_bytes()
    job = jobs.create_job(conn, sid, [("Medline 2026.10.15 22.80.xlsx", content)], date(2026, 10, 15),
                          timeframe=timeframe)
    return jobs.run_job(conn, job.id)


def rows_of(conn, job_id, where="1 = 1"):
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM revenue_row WHERE job_id = ? AND {where} ORDER BY row_number", (job_id,)).fetchall()]


def invoice(row) -> str:
    import json
    return json.loads(row["raw_row_json"])["InvoiceNumber"]


# --- Verification ------------------------------------------------------------

def test_unverified_rows_are_kept_as_exceptions_with_a_reason(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    assert job.row_count == len(mock.ROWS)          # nothing dropped; the grand-total row isn't a data row
    exc = rows_of(conn, job.id, "match_status = 'key_exception'")
    assert {invoice(r): r["exception_reason"] for r in exc} == mock.EXCEPTION_REASONS
    # Not guessed: no GPO ID, no member, no review item — even for INV8, whose file GPO ID is a real member's.
    assert all(r["entity_code"] is None and r["matched_member_id"] is None for r in exc)
    assert conn.execute("SELECT COUNT(*) FROM member_review_item WHERE job_id = ?", (job.id,)).fetchone()[0] == 0
    assert job.status == "needs_review"
    assert job.pending_review_count == 0     # signed off on the Export page once the exceptions are checked
    assert jobs.confirm_job(conn, job.id).status == "completed"


def test_verified_rows_use_the_keys_gpo_id(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    ok = {invoice(r): r for r in rows_of(conn, job.id, "match_status = 'matched'")}
    assert ok["INV5"]["file_entity_code"] == "EC999" and ok["INV5"]["entity_code"] == "EC002"   # corrected
    assert ok["INV3"]["file_entity_code"] is None and ok["INV3"]["entity_code"] == "EC001"      # filled in
    assert {r["match_method"] for r in ok.values()} == {"gpo_key"}
    assert ok["INV1"]["account_id"] == "SF-GEN"   # looked up from the verified GPO ID, for the Validation tab
    messages = {e.exception_type: e.message for e in jobs.list_exceptions(conn, job.id)}
    assert "EC999 -> EC002" in messages["gpo_id_corrected"]
    assert "4 row(s) could not be verified" in messages["gpo_key_verification"]


def test_no_key_loaded_means_every_row_is_an_exception(conn, tmp_path):
    make_members(conn)
    sid = apply_preset(conn, mock.MEDLINE_GPO_KEY)
    job = run_medline(conn, sid, tmp_path)
    assert job.matched_count == 0
    assert {r["exception_reason"] for r in rows_of(conn, job.id)} == {"account_not_in_key", "missing_account_number"}
    errors = [e for e in jobs.list_exceptions(conn, job.id) if e.severity == "error"]
    assert any("No GPO key is loaded" in e.message for e in errors)
    assert job.reconciliation.balanced and job.reconciliation.output_rows == 0


# --- Rollup ------------------------------------------------------------------

def test_rollup_one_row_per_gpo_id_per_month(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    upload = jobs.get_preview(conn, job.id).upload
    got = {(r.pec, r.earned_period.isoformat()): (r.volume, r.total_revenue) for r in upload}
    assert got == pytest.approx(mock.EXPECTED_UPLOAD)
    # Two Medline accounts (1001, 1002) for one facility make one row per month.
    assert len(upload) == len(mock.EXPECTED_UPLOAD)
    assert job.total_volume == pytest.approx(620.00)
    assert job.total_revenue == pytest.approx(18.60)


def test_volume_blank_supplier_populates_total_revenue_only(conn, tmp_path):
    """The rolled-up supplier stub: a config-only supplier, Volume left blank."""
    members = make_members(conn)
    sid = apply_preset(conn, ROLLED_UP_PLACEHOLDER)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([c for c, _ in ROLLED_UP_PLACEHOLDER["column_map"]])
    ws.append(["General Hospital", "EC001", "Austin", "TX", "2026-09-30", 120.50])
    ws.append(["Mercy Medical Center", "EC002", "Dallas", "TX", "2026-09-30", 80.25])
    wb.save(tmp_path / "rolled.xlsx")
    job = jobs.create_job(conn, sid, [("rolled.xlsx", (tmp_path / "rolled.xlsx").read_bytes())], date(2026, 10, 15),
                          timeframe="3Q26")
    job = jobs.run_job(conn, job.id)
    assert job.status == "completed" and members
    upload = jobs.get_preview(conn, job.id).upload
    assert {(r.pec, r.volume, r.total_revenue) for r in upload} == {("EC001", None, 120.50), ("EC002", None, 80.25)}
    assert job.total_volume is None
    assert job.reconciliation.balanced and not job.reconciliation.volume_checked


# --- Upload template ---------------------------------------------------------

def test_upload_template_columns_and_fixed_values(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path, timeframe="3q26 ")
    processed = next(o for o in jobs.list_outputs(conn, job.id) if o.artifact_type == "salesforce_upload")
    _, content = jobs.read_output(conn, job.id, processed.id)
    ws = openpyxl.load_workbook(BytesIO(content))["Upload"]
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0]) == SF_UPLOAD_COLUMNS
    for values in rows[1:]:
        r = dict(zip(SF_UPLOAD_COLUMNS, values))
        assert r["Facility ID"] == r["Pec"]                 # Premier entity code / verified GPO ID
        assert r["Contract ID"] == "CON-MED"                # per-supplier config
        assert r["Payment ID"] in ("", None)                # filled in from the ledger afterwards
        assert r["Date Received"] in ("", None)
        assert (r["Revenue Type"], r["Keep/Return?"]) == ("Admin Fee", "Keep")
        assert r["Pmt For (Time Frame)"] == "3Q26"          # entered for the run, XQYY
        assert isinstance(r["Earned Period"], datetime)     # a real date, not text
        for blank in ("Comments", "Date Refunded", "Rebate Retained", "Amount Retained", "Retained Reason"):
            assert r[blank] in ("", None)


def test_facility_id_falls_back_to_salesforce_id_without_entity_code(conn, processed_job):
    conn.execute("UPDATE revenue_output_row SET pec = 'PENDING-1' WHERE job_id = ? AND pec = 'EC001'",
                 (processed_job["job_id"],))
    conn.commit()
    upload = {r.pec: r.facility_id for r in jobs.get_preview(conn, processed_job["job_id"]).upload}
    assert upload == {"PENDING-1": "SF-GEN", "EC002": "EC002"}


def test_dates_are_written_mm_dd_yyyy(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    processed = next(o for o in jobs.list_outputs(conn, job.id) if o.artifact_type == "salesforce_upload")
    ws = openpyxl.load_workbook(BytesIO(jobs.read_output(conn, job.id, processed.id)[1]))["Upload"]
    col = SF_UPLOAD_COLUMNS.index("Earned Period") + 1
    cell = ws.cell(row=2, column=col)
    assert cell.value == datetime(2026, 9, 1) and cell.number_format == "mm/dd/yyyy"


@pytest.mark.parametrize("bad", ["3Q2026", "Q3 2026", "5Q26", "2026Q3"])
def test_timeframe_must_be_xqyy(conn, medline, tmp_path, bad):
    from core.errors import InvalidInputError
    with pytest.raises(InvalidInputError, match="XQYY"):
        run_medline(conn, medline["supplier_id"], tmp_path, timeframe=bad)


def test_medline_preset_contract_id(conn):
    sid = apply_preset(conn, MEDLINE)
    assert conn.execute("SELECT contract_id FROM supplier_profile WHERE id = ?", (sid,)).fetchone()[0] == "AL-DS-001"


def test_missing_timeframe_is_flagged(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path, timeframe="  ")
    assert job.timeframe is None
    assert "timeframe_missing" in {e.exception_type for e in jobs.list_exceptions(conn, job.id)}


def test_empty_upload_tab_keeps_the_template_header(conn, tmp_path):
    make_members(conn)
    sid = apply_preset(conn, mock.MEDLINE_GPO_KEY)    # no key loaded: nothing verifies, nothing to upload
    job = run_medline(conn, sid, tmp_path)
    processed = next(o for o in jobs.list_outputs(conn, job.id) if o.artifact_type == "salesforce_upload")
    ws = openpyxl.load_workbook(BytesIO(jobs.read_output(conn, job.id, processed.id)[1]))["Upload"]
    assert list(next(ws.iter_rows(values_only=True))) == SF_UPLOAD_COLUMNS


# --- Reconciliation ----------------------------------------------------------

def test_totals_reconcile_input_equals_output_plus_exceptions(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    rec = job.reconciliation
    assert rec.balanced
    assert (rec.input.volume, rec.input.revenue) == pytest.approx((mock.INPUT_VOLUME, mock.INPUT_REVENUE))
    assert (rec.exceptions_volume, rec.exceptions_revenue) == pytest.approx(
        (mock.EXCEPTIONS_VOLUME, mock.EXCEPTIONS_REVENUE))
    assert rec.output_revenue + rec.exceptions_revenue == pytest.approx(rec.input.revenue)
    assert rec.output_volume + rec.exceptions_volume == pytest.approx(rec.input.volume)
    # The grand-total row is reported, not counted (it would double the input).
    assert (rec.input.dropped_rows, rec.input.dropped_revenue) == (1, pytest.approx(mock.INPUT_REVENUE))


def test_reconciliation_catches_lost_output(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    conn.execute("DELETE FROM revenue_output_row WHERE id = (SELECT MIN(id) FROM revenue_output_row WHERE job_id = ?)",
                 (job.id,))
    conn.commit()
    row = dict(conn.execute("SELECT reconciliation_json FROM revenue_job WHERE id = ?", (job.id,)).fetchone())
    rec = reconcile(conn, job.id, "sum", input_totals_from_json(row["reconciliation_json"]))
    assert not rec.balanced and rec.revenue_difference != 0


def test_unbalanced_job_is_never_marked_completed(conn, seeded, tmp_path, monkeypatch):
    import engine.processor as processor
    from .conftest import write_supplier_file
    real = processor._aggregate_output

    def lossy(conn, job_id, *args):  # simulate an aggregation bug that loses a row
        total = real(conn, job_id, *args)
        conn.execute("DELETE FROM revenue_output_row WHERE id = (SELECT MAX(id) FROM revenue_output_row "
                     "WHERE job_id = ?)", (job_id,))
        conn.commit()
        return total
    monkeypatch.setattr(processor, "_aggregate_output", lossy)
    rows = [("General Hospital", "J100", "Austin", "TX", "78701", 100.00, 1000.00, "2025-06-15"),
            ("Mercy Medical Center", "J200", "Dallas", "TX", "75201", 120.00, 1200.00, "2025-06-10")]
    job_id = create_job_row(conn, seeded["supplier_id"], write_supplier_file(tmp_path / "f.xlsx", rows))
    result = process_job(conn, job_id)
    assert result["status"] == "needs_review" and not result["reconciliation"].balanced
    assert any(e.severity == "error" and e.exception_type == "reconciliation"
               for e in jobs.list_exceptions(conn, job_id))


def test_review_decision_rechecks_totals(conn, processed_job):
    from core import reviews
    from models.reviews import ReviewApproval
    item = reviews.list_pending_items(conn, processed_job["job_id"])[0]
    reviews.approve(conn, item.id, ReviewApproval(member_id=processed_job["members"]["desert"]))
    rec = jobs.get_job(conn, processed_job["job_id"]).reconciliation
    assert rec.balanced and rec.exception_rows == 0 and rec.output_revenue == pytest.approx(300.0)


# --- Output files ------------------------------------------------------------

def test_outputs_go_to_the_output_folder_and_are_never_overwritten(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    first = jobs.list_outputs(conn, job.id)
    second = jobs.create_outputs(conn, job.id)
    names = [o.file_name for o in jobs.list_outputs(conn, job.id)]
    assert len(names) == 4 and len(set(names)) == 4 and {o.id for o in first} < {o.id for o in jobs.list_outputs(conn, job.id)}
    assert all((tmp_path / "outputs" / n).is_file() for n in names)
    assert not list((tmp_path / "uploads").glob("*Processed*"))
    assert second[0].file_name.startswith("Medline 2026.10.15 22.80 Processed ")


def test_reserve_output_path_versions_within_the_same_second(tmp_path):
    from datetime import datetime
    now = datetime(2026, 11, 13, 9, 30, 0)
    a = reserve_output_path(tmp_path, "Medline", "Processed", now)
    a.write_bytes(b"first")
    b = reserve_output_path(tmp_path, "Medline", "Processed", now)
    assert a.name == "Medline Processed 20261113-093000.xlsx"
    assert b.name == "Medline Processed 20261113-093000-2.xlsx"
    assert a.read_bytes() == b"first"


def test_outputs_refuse_the_source_folder(conn, medline, tmp_path, monkeypatch):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    with pytest.raises(OutputLocationError):
        export_job(conn, job.id, tmp_path / "uploads")
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "uploads"))
    with pytest.raises(OutputLocationError):
        jobs.output_dir()


def test_exceptions_workbook_lists_every_excluded_row(conn, medline, tmp_path):
    job = run_medline(conn, medline["supplier_id"], tmp_path)
    report = next(o for o in jobs.list_outputs(conn, job.id) if o.artifact_type == "exception_report")
    wb = openpyxl.load_workbook(BytesIO(jobs.read_output(conn, job.id, report.id)[1]))
    exc = list(wb["Exceptions"].iter_rows(values_only=True))
    header, data = exc[0], [dict(zip(exc[0], r)) for r in exc[1:]]
    assert "Reason" in header and "GPO Key Entry" in header
    assert sorted(r["Reason Code"] for r in data) == sorted(mock.EXCEPTION_REASONS.values())
    assert sum(r["Revenue (fee)"] for r in data) == pytest.approx(mock.EXCEPTIONS_REVENUE)
    assert {r["Source Row"] for r in data} == {8, 9, 10, 11}   # spreadsheet rows of INV7..INV10
    rec = {r[0]: r for r in wb["Reconciliation"].iter_rows(values_only=True)}
    assert rec["Revenue (fee)"][5] == "Yes"
    assert len(list(wb["Dropped Rows"].iter_rows())) == 2       # header + the grand-total row
