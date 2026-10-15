from datetime import date
from io import BytesIO

import openpyxl
import pytest

from core import jobs, reviews
from core.errors import ConflictError, InvalidInputError, NotFoundError
from models.reviews import ReviewApproval

from .conftest import FILE_NAME, write_supplier_file


@pytest.fixture(autouse=True)
def _upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))


def _xlsx_bytes(tmp_path) -> bytes:
    return write_supplier_file(tmp_path / "src.xlsx").read_bytes()


def test_create_and_run_job(conn, seeded, tmp_path):
    job = jobs.create_job(conn, seeded["supplier_id"], FILE_NAME, _xlsx_bytes(tmp_path), date(2025, 7, 2))
    assert job.status == "uploaded"
    assert job.supplier_name == "Acme Supply"
    assert job.contract_id == "CON-1"

    done = jobs.run_job(conn, job.id)
    assert done.status == "needs_review"
    assert (done.matched_count, done.unmatched_count, done.total_revenue) == (3, 1, 270.0)
    assert done.pending_review_count == 1
    assert done.payment_id == "PAY-1"


def test_create_job_rejects_unknown_extension(conn, seeded):
    with pytest.raises(InvalidInputError):
        jobs.create_job(conn, seeded["supplier_id"], "notes.pdf", b"x", date(2025, 7, 2))


def test_create_job_unknown_supplier(conn, seeded, tmp_path):
    with pytest.raises(NotFoundError):
        jobs.create_job(conn, 999, FILE_NAME, _xlsx_bytes(tmp_path), date(2025, 7, 2))


def test_run_job_with_missing_file_ends_failed(conn, seeded, tmp_path):
    job = jobs.create_job(conn, seeded["supplier_id"], FILE_NAME, _xlsx_bytes(tmp_path), date(2025, 7, 2))
    conn.execute("UPDATE revenue_job SET file_path = ? WHERE id = ?", (str(tmp_path / "gone.xlsx"), job.id))
    conn.commit()
    with pytest.raises(FileNotFoundError):
        jobs.run_job(conn, job.id)
    failed = jobs.get_job(conn, job.id)
    assert failed.status == "failed"
    assert "File not found" in failed.error_message


def test_fail_interrupted_jobs(conn, seeded, tmp_path):
    job = jobs.create_job(conn, seeded["supplier_id"], FILE_NAME, _xlsx_bytes(tmp_path), date(2025, 7, 2))
    assert jobs.fail_interrupted_jobs(conn) == 1
    assert jobs.get_job(conn, job.id).status == "failed"


def test_list_jobs_filters(conn, processed_job):
    assert [j.id for j in jobs.list_jobs(conn, has_pending_reviews=True)] == [processed_job["job_id"]]
    assert jobs.list_jobs(conn, statuses=["completed"]) == []
    assert len(jobs.list_jobs(conn, statuses=["completed", "needs_review"], limit=5)) == 1


def test_infer_from_filename(conn, seeded):
    guess = jobs.infer_from_filename(conn, FILE_NAME)
    assert guess.supplier_profile_id == seeded["supplier_id"]
    assert guess.date_received == date(2025, 7, 2)
    assert guess.deposit_amount == 300.00
    assert jobs.infer_from_filename(conn, "random.xlsx").supplier_profile_id is None


def test_set_payment_id_updates_output_rows(conn, processed_job):
    job = jobs.set_payment_id(conn, processed_job["job_id"], "PAY-OVERRIDE")
    assert job.payment_id == "PAY-OVERRIDE"
    preview = jobs.get_preview(conn, job.id)
    assert {r.payment_id for r in preview.upload} == {"PAY-OVERRIDE"}


def test_preview_has_all_four_sheets(conn, processed_job):
    preview = jobs.get_preview(conn, processed_job["job_id"])
    assert sorted(r.pec for r in preview.upload) == ["EC001", "EC001", "EC002"]
    assert {r.entity_code for r in preview.validation} == {"EC001", "EC002"}
    assert [r.customer_name for r in preview.unmatched] == ["Zyxw Qvbn Clinic"]
    assert {r.jde_number for r in preview.jde_mapping} == {"J100", "J200"}


def test_build_export_returns_workbook(conn, processed_job):
    name, content = jobs.build_export(conn, processed_job["job_id"])
    assert name == "Acme Supply 2025.07.02 300.00 Ledger Processed.xlsx"
    wb = openpyxl.load_workbook(BytesIO(content))
    assert wb.sheetnames == ["Upload", "Validation", "Unmatched", "JDE Mapping"]


def test_build_export_refuses_failed_job(conn, seeded, tmp_path):
    job = jobs.create_job(conn, seeded["supplier_id"], FILE_NAME, _xlsx_bytes(tmp_path), date(2025, 7, 2))
    with pytest.raises(ConflictError):
        jobs.build_export(conn, job.id)


def test_exceptions_listed(conn, processed_job):
    types = {e.exception_type for e in jobs.list_exceptions(conn, processed_job["job_id"])}
    assert "revenue_reconciliation_mismatch" in types  # 270 matched vs 300 deposit


def test_confirm_job_requires_no_pending_items(conn, processed_job):
    job_id = processed_job["job_id"]
    with pytest.raises(ConflictError):
        jobs.confirm_job(conn, job_id)
    item = reviews.list_pending_items(conn, job_id)[0]
    reviews.dismiss(conn, item.id)
    # Dismissing leaves the job in needs_review with nothing left to review.
    assert jobs.get_job(conn, job_id).status == "needs_review"
    assert jobs.confirm_job(conn, job_id).status == "completed"


def test_review_approve_and_double_decision(conn, processed_job):
    job_id = processed_job["job_id"]
    item = reviews.list_pending_items(conn, job_id)[0]
    assert item.customer_name_sample == "Zyxw Qvbn Clinic"
    result = reviews.approve(conn, item.id, ReviewApproval(member_id=processed_job["members"]["desert"]))
    assert result.updated_rows == 1
    assert result.job.status == "completed"
    with pytest.raises(ConflictError):
        reviews.dismiss(conn, item.id)


def test_review_approve_unknown_member(conn, processed_job):
    item = reviews.list_pending_items(conn, processed_job["job_id"])[0]
    with pytest.raises(NotFoundError):
        reviews.approve(conn, item.id, ReviewApproval(member_id=9999))
