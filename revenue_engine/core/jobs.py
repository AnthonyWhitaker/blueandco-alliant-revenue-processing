"""Revenue jobs: creation, listing, payment ID, exceptions, preview, export, sign-off."""

import json
import math
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from engine.constants import EXTRACT_MANUAL_ENTRY, JOB_TYPE_PIVOT_ALLOCATION, STATUS_COMPLETED, STATUS_FAILED, STATUS_NEEDS_REVIEW, STATUS_PROCESSING, STATUS_UPLOADED
from engine.db import execute_select, get_last_insert_id
from engine.exporter import export_job, export_to_dataframes
from engine.normalizer import guess_supplier_from_filename, parse_date_from_filename, parse_deposit_amount_from_filename
from engine.processor import process_job
from models.enums import JobStatus
from models.jobs import FileNameInference, Job, JobException, JobPreview

from .errors import ConflictError, InvalidInputError, NotFoundError
from .rows import frame_records, row_dict, rows_dicts
from .suppliers import get_supplier, list_suppliers
from .tier_c import list_extraction_rules

UPLOAD_EXTENSIONS = [".xlsx", ".xls", ".xlsm", ".csv"]
EXPORTABLE_STATUSES: list[JobStatus] = [STATUS_COMPLETED, STATUS_NEEDS_REVIEW]

_DEFAULT_UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"

_JOB_SELECT = """
    SELECT rj.*, sp.name AS supplier_name, sp.job_type AS job_type,
           (SELECT COUNT(*) FROM member_review_item mri
             WHERE mri.job_id = rj.id AND mri.status = 'pending') AS pending_review_count
    FROM revenue_job rj
    JOIN supplier_profile sp ON sp.id = rj.supplier_profile_id
"""

_SHEET_COLUMNS = {
    "Upload": {
        "Pec": "pec", "Facility ID": "facility_id", "Contract ID": "contract_id",
        "Payment ID": "payment_id", "Volume": "volume", "Total Revenue": "total_revenue",
        "Revenue Type": "revenue_type", "Keep/Return?": "keep_return", "Earned Period": "earned_period",
        "Pmt For (Time Frame)": "pmt_for_time_frame", "Date Received": "date_received",
        "Comments": "comments", "Date Refunded": "date_refunded", "Rebate Retained": "rebate_retained",
        "Amount Retained": "amount_retained", "Retained Reason": "retained_reason",
    },
    "Validation": {
        "JDE #": "jde_number", "Supplier Customer Name": "supplier_customer_name",
        "PEC / Entity Code": "entity_code", "Salesforce Account ID": "salesforce_account_id",
        "Account List Name": "account_list_name", "City": "city", "State": "state",
        "Match Method": "match_method", "Score": "score",
    },
    "Unmatched": {
        "Row #": "row_number", "JDE #": "jde_number", "Customer Name": "customer_name",
        "Address": "address", "City": "city", "State": "state", "ZIP": "zip",
        "Process Date": "process_date", "Sales Amount": "sales_amount", "Fee Amount": "fee_amount",
        "Product Line": "product_line",
    },
    "JDE Mapping": {
        "JDE #": "jde_number", "Supplier Name": "supplier_name",
        "PEC / Entity Code": "entity_code", "Account List Name": "account_list_name",
    },
}


def upload_dir() -> Path:
    return Path(os.environ.get("REVENUE_UPLOAD_DIR") or _DEFAULT_UPLOAD_DIR)


def get_job(conn, job_id: int) -> Job:
    row = row_dict(conn.execute(_JOB_SELECT + " WHERE rj.id = ?", (job_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Job {job_id} not found.")
    return Job.model_validate(row)


def list_jobs(
    conn,
    statuses: Optional[list[JobStatus]] = None,
    has_pending_reviews: Optional[bool] = None,
    limit: Optional[int] = None,
) -> list[Job]:
    where: list[str] = []
    params: list = []
    if statuses:
        where.append(f"rj.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if has_pending_reviews is not None:
        exists = ("EXISTS (SELECT 1 FROM member_review_item p "
                  "WHERE p.job_id = rj.id AND p.status = 'pending')")
        where.append(exists if has_pending_reviews else f"NOT {exists}")
    sql = _JOB_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rj.created_at DESC, rj.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = execute_select(conn, sql, params or None).fetchall()
    return [Job.model_validate(r) for r in rows_dicts(rows)]


def infer_from_filename(conn, file_name: str) -> FileNameInference:
    suppliers = list_suppliers(conn)
    guessed = guess_supplier_from_filename(file_name, [s.name for s in suppliers])
    supplier = next((s for s in suppliers if s.name == guessed), None)
    return FileNameInference(
        file_name=file_name,
        supplier_profile_id=supplier.id if supplier else None,
        supplier_name=supplier.name if supplier else None,
        date_received=parse_date_from_filename(file_name),
        deposit_amount=parse_deposit_amount_from_filename(file_name),
    )


def save_upload(file_name: str, content: bytes) -> Path:
    """Persist an uploaded supplier file under upload_dir() and return its path."""
    suffix = Path(file_name).suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise InvalidInputError(
            f"Unsupported file type '{suffix or file_name}'. Expected one of: {', '.join(UPLOAD_EXTENSIONS)}."
        )
    if not content:
        raise InvalidInputError("The uploaded file is empty.")
    target_dir = upload_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(content)
    return path


def manual_entry_rule_names(conn, supplier_id: int) -> list[str]:
    """Tier C extraction rules whose total is typed in at upload instead of read from the file."""
    return [r.rule_name for r in list_extraction_rules(conn, supplier_id)
            if r.extraction_method == EXTRACT_MANUAL_ENTRY]


def _check_manual_totals(conn, supplier, manual_totals: Optional[dict[str, float]]) -> dict[str, float]:
    """Every manual_entry rule of a Tier C supplier needs a total (FINDINGS F9), and nothing else may have one."""
    manual_totals = manual_totals or {}
    expected = manual_entry_rule_names(conn, supplier.id) if supplier.job_type == JOB_TYPE_PIVOT_ALLOCATION else []
    unknown = sorted(set(manual_totals) - set(expected))
    if unknown:
        raise InvalidInputError(
            f"{supplier.name} has no manual-entry extraction rule named {', '.join(map(repr, unknown))}."
        )
    missing = [name for name in expected if name not in manual_totals]
    if missing:
        raise InvalidInputError(
            f"Enter the confirmed total for {', '.join(map(repr, missing))}: "
            f"{'it is a manual-entry rule' if len(missing) == 1 else 'they are manual-entry rules'} for {supplier.name}."
        )
    for name, amount in manual_totals.items():
        if not math.isfinite(amount):
            raise InvalidInputError(f"The total for {name!r} must be a number.")
    return manual_totals


def create_job(
    conn,
    supplier_id: int,
    file_name: str,
    content: bytes,
    date_received: date,
    manual_totals: Optional[dict[str, float]] = None,
) -> Job:
    """Store the file and create a revenue_job in 'uploaded' status. Processing is separate (run_job).

    manual_totals: {rule_name: amount}, required for each of a Tier C supplier's manual_entry rules.
    """
    supplier = get_supplier(conn, supplier_id)
    manual_totals = _check_manual_totals(conn, supplier, manual_totals)
    path = save_upload(file_name, content)
    cur = conn.execute(
        """
        INSERT INTO revenue_job
            (supplier_profile_id, original_file_name, file_path, status, date_received, contract_id,
             manual_totals_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (supplier.id, file_name, str(path), STATUS_UPLOADED, date_received.isoformat(), supplier.contract_id,
         json.dumps(manual_totals) if manual_totals else None),
    )
    conn.commit()
    return get_job(conn, get_last_insert_id(conn, cur))


def run_job(conn, job_id: int, manual_payment_id: Optional[str] = None) -> Job:
    """Run the processing pipeline; on any failure the job ends up 'failed' and the error re-raises."""
    try:
        process_job(conn=conn, job_id=job_id, manual_payment_id=manual_payment_id or None)
    except Exception as e:
        # process_job marks the job failed itself, except for errors raised
        # before it starts (missing file/job/supplier), which leave it 'uploaded'.
        conn.rollback()
        conn.execute(
            "UPDATE revenue_job SET status = ?, error_message = ? WHERE id = ? AND status = ?",
            (STATUS_FAILED, str(e)[:2000], job_id, STATUS_UPLOADED),
        )
        conn.commit()
        raise
    return get_job(conn, job_id)


def fail_interrupted_jobs(conn) -> int:
    """Mark jobs left 'uploaded'/'processing' by a previous server process as failed.

    Call once at API startup: a single API process owns all processing, so any
    such job at boot can never finish.
    """
    cur = conn.execute(
        "UPDATE revenue_job SET status = ?, error_message = ? WHERE status IN (?, ?)",
        (STATUS_FAILED, "Interrupted: the server restarted before processing finished. Upload the file again.",
         STATUS_UPLOADED, STATUS_PROCESSING),
    )
    conn.commit()
    return cur.rowcount


def set_payment_id(conn, job_id: int, payment_id: str) -> Job:
    get_job(conn, job_id)
    conn.execute("UPDATE revenue_job SET payment_id = ? WHERE id = ?", (payment_id, job_id))
    conn.execute("UPDATE revenue_output_row SET payment_id = ? WHERE job_id = ?", (payment_id, job_id))
    conn.commit()
    return get_job(conn, job_id)


def list_exceptions(conn, job_id: int) -> list[JobException]:
    get_job(conn, job_id)
    rows = conn.execute(
        "SELECT id, exception_type, message, severity, status, created_at FROM exception "
        "WHERE job_id = ? ORDER BY created_at, id",
        (job_id,),
    ).fetchall()
    return [JobException.model_validate(r) for r in rows_dicts(rows)]


def get_preview(conn, job_id: int) -> JobPreview:
    get_job(conn, job_id)
    frames = export_to_dataframes(conn, job_id)
    return JobPreview(
        upload=frame_records(frames["Upload"], _SHEET_COLUMNS["Upload"]),
        validation=frame_records(frames["Validation"], _SHEET_COLUMNS["Validation"]),
        unmatched=frame_records(frames["Unmatched"], _SHEET_COLUMNS["Unmatched"]),
        jde_mapping=frame_records(frames["JDE Mapping"], _SHEET_COLUMNS["JDE Mapping"]),
    )


def export_file_name(job: Job) -> str:
    return f"{Path(job.original_file_name).stem} Processed.xlsx"


def build_export(conn, job_id: int) -> tuple[str, bytes]:
    """Build the 4-tab Salesforce workbook. Returns (download file name, bytes)."""
    job = get_job(conn, job_id)
    if job.status not in EXPORTABLE_STATUSES:
        raise ConflictError(f"Job {job_id} is '{job.status}'; only completed or needs_review jobs can be exported.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp_path = tmp.name
    try:
        export_job(conn, job_id, tmp_path)
        content = Path(tmp_path).read_bytes()
    finally:
        os.unlink(tmp_path)
    return export_file_name(job), content


def confirm_job(conn, job_id: int) -> Job:
    """Sign off a needs_review job that has no pending review items (FINDINGS F8)."""
    job = get_job(conn, job_id)
    if job.status != STATUS_NEEDS_REVIEW:
        raise ConflictError(f"Job {job_id} is '{job.status}'; only needs_review jobs can be confirmed.")
    if job.pending_review_count:
        raise ConflictError(
            f"Job {job_id} still has {job.pending_review_count} pending review item(s). Resolve them first."
        )
    conn.execute(
        "UPDATE revenue_job SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (STATUS_COMPLETED, job_id),
    )
    conn.commit()
    return get_job(conn, job_id)
