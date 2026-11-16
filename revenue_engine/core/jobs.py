"""Revenue jobs: creation, listing, payment ID, exceptions, preview, export, sign-off."""

import hashlib
import json
import logging
import math
import os
import uuid
from datetime import date
from pathlib import Path
import re
from typing import Optional

from engine.constants import EXTRACT_MANUAL_ENTRY, JOB_TYPE_PIVOT_ALLOCATION, STATUS_COMPLETED, STATUS_FAILED, STATUS_NEEDS_REVIEW, STATUS_PROCESSING, STATUS_UPLOADED
from engine.constants import EXC_PROCESSING_ERROR
from engine.db import execute_select, get_last_insert_id
from engine.exporter import OutputLocationError, export_job, export_to_dataframes
from engine.normalizer import guess_supplier_from_filename, parse_date_from_filename, parse_deposit_amount_from_filename
from engine.processor import process_job
from models.enums import JobStatus
from models.jobs import FileNameInference, Job, JobException, JobFile, JobOutput, JobPreview

from .errors import ConflictError, InvalidInputError, NotFoundError
from .rows import frame_records, row_dict, rows_dicts
from .suppliers import get_supplier, list_suppliers
from .tier_c import list_extraction_rules

UPLOAD_EXTENSIONS = [".xlsx", ".xls", ".xlsm", ".csv"]
EXPORTABLE_STATUSES: list[JobStatus] = [STATUS_COMPLETED, STATUS_NEEDS_REVIEW]

_DEFAULT_UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "outputs"

log = logging.getLogger("core.jobs")

_JOB_SELECT = """
    SELECT rj.*, sp.name AS supplier_name, sp.job_type AS job_type,
           (SELECT COUNT(*) FROM member_review_item mri
             WHERE mri.job_id = rj.id AND mri.status = 'pending') AS pending_review_count,
           (SELECT COUNT(*) FROM revenue_job_file f WHERE f.job_id = rj.id) AS file_count
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


def output_dir() -> Path:
    """Where output workbooks are written: REVENUE_OUTPUT_DIR, default data/outputs. Never the upload folder."""
    path = Path(os.environ.get("REVENUE_OUTPUT_DIR") or _DEFAULT_OUTPUT_DIR)
    if path.resolve() == upload_dir().resolve():
        raise OutputLocationError(
            "REVENUE_OUTPUT_DIR is the upload folder. Outputs must go to a separate folder from the source files."
        )
    return path


def get_job(conn, job_id: int) -> Job:
    row = row_dict(conn.execute(_JOB_SELECT + " WHERE rj.id = ?", (job_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Job {job_id} not found.")
    return _job(row)


def _job(row: dict) -> Job:
    # Jobs created before multi-file upload have no revenue_job_file rows, only file_path.
    if not row.get("file_count") and row.get("file_path"):
        row = {**row, "file_count": 1}
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
    return [_job(r) for r in rows_dicts(rows)]


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


def job_display_name(file_names: list[str]) -> str:
    """revenue_job.original_file_name: the file's name, or the first file's name plus a count."""
    if len(file_names) == 1:
        return file_names[0]
    return f"{file_names[0]} (+{len(file_names) - 1} more)"


TIMEFRAME_FORMAT = re.compile(r"[1-4]Q\d{2}")


def normalize_timeframe(timeframe: Optional[str]) -> Optional[str]:
    """'1q26 ' -> '1Q26'; blank -> None (processing flags it). Anything not XQYY is rejected."""
    value = (timeframe or "").strip().upper()
    if not value:
        return None
    if not TIMEFRAME_FORMAT.fullmatch(value):
        raise InvalidInputError(f"Timeframe must be XQYY: quarter and 2-digit year, e.g. 1Q26 (got {timeframe!r}).")
    return value


def create_job(
    conn,
    supplier_id: int,
    files: list[tuple[str, bytes]],
    date_received: date,
    manual_totals: Optional[dict[str, float]] = None,
    timeframe: Optional[str] = None,
) -> Job:
    """Store the files and create a revenue_job in 'uploaded' status. Processing is separate (run_job).

    files: [(file name, content)], one or more files for the same supplier and month; they are
           combined before processing (Tier C suppliers take exactly one).
    manual_totals: {rule_name: amount}, required for each of a Tier C supplier's manual_entry rules.
    timeframe: "Pmt For (Time Frame)" on every Upload row, XQYY (quarter + 2-digit year, e.g. 1Q26).
    """
    supplier = get_supplier(conn, supplier_id)
    if not files:
        raise InvalidInputError("Upload at least one file.")
    if supplier.job_type == JOB_TYPE_PIVOT_ALLOCATION and len(files) > 1:
        raise InvalidInputError(f"{supplier.name} is an aggregate/pivot supplier: upload one file per job.")
    manual_totals = _check_manual_totals(conn, supplier, manual_totals)
    timeframe = normalize_timeframe(timeframe)
    stored = [(name, save_upload(name, content), hashlib.sha256(content).hexdigest()) for name, content in files]
    cur = conn.execute(
        """
        INSERT INTO revenue_job
            (supplier_profile_id, original_file_name, file_path, status, date_received, contract_id,
             manual_totals_json, timeframe)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (supplier.id, job_display_name([n for n, _, _ in stored]), str(stored[0][1]), STATUS_UPLOADED,
         date_received.isoformat(), supplier.contract_id,
         json.dumps(manual_totals) if manual_totals else None, timeframe),
    )
    job_id = get_last_insert_id(conn, cur)
    for name, path, sha in stored:
        conn.execute(
            "INSERT INTO revenue_job_file (job_id, original_file_name, file_path, sha256) VALUES (?, ?, ?, ?)",
            (job_id, name, str(path), sha),
        )
    conn.commit()
    return get_job(conn, job_id)


def list_job_files(conn, job_id: int) -> list[JobFile]:
    get_job(conn, job_id)
    rows = conn.execute(
        "SELECT id, original_file_name, sha256, row_count, created_at FROM revenue_job_file WHERE job_id = ? ORDER BY id",
        (job_id,),
    ).fetchall()
    return [JobFile.model_validate(r) for r in rows_dicts(rows)]


def run_job(conn, job_id: int, manual_payment_id: Optional[str] = None) -> Job:
    """Run the processing pipeline; on any failure the job ends up 'failed' and the error re-raises."""
    try:
        result = process_job(conn=conn, job_id=job_id, manual_payment_id=manual_payment_id or None)
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
    if result.get("status") in EXPORTABLE_STATUSES:
        _write_outputs_after_processing(conn, job_id)
    return get_job(conn, job_id)


def _write_outputs_after_processing(conn, job_id: int) -> None:
    """First version of the output files. A failure here is logged on the job; processing still succeeded."""
    try:
        export_job(conn, job_id, output_dir())
    except Exception as e:
        log.exception("writing outputs failed job_id=%s", job_id)
        conn.rollback()
        conn.execute(
            "INSERT INTO exception (job_id, exception_type, message, severity) VALUES (?, ?, ?, 'error')",
            (job_id, EXC_PROCESSING_ERROR, f"Processing finished but the output files could not be written: {e}"),
        )
        conn.commit()


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


def _output(row: dict) -> JobOutput:
    return JobOutput.model_validate({**row, "available": bool(row.get("file_path")) and Path(row["file_path"]).is_file()})


def list_outputs(conn, job_id: int) -> list[JobOutput]:
    """Stored output files of a job, newest first."""
    get_job(conn, job_id)
    rows = conn.execute(
        "SELECT id, artifact_type, file_name, file_path, created_at FROM export_file WHERE job_id = ? "
        "ORDER BY created_at DESC, id DESC",
        (job_id,),
    ).fetchall()
    return [_output(r) for r in rows_dicts(rows)]


def create_outputs(conn, job_id: int) -> list[JobOutput]:
    """Write a new version of the processed and exceptions workbooks from the job's current data."""
    job = get_job(conn, job_id)
    if job.status not in EXPORTABLE_STATUSES:
        raise ConflictError(f"Job {job_id} is '{job.status}'; only completed or needs_review jobs can be exported.")
    try:
        exported = export_job(conn, job_id, output_dir())
    except OutputLocationError as e:
        raise ConflictError(str(e)) from e
    by_id = {o.id: o for o in list_outputs(conn, job_id)}
    return [by_id[e.id] for e in exported]


def read_output(conn, job_id: int, output_id: int) -> tuple[str, bytes]:
    """(file name, bytes) of a stored output file."""
    row = row_dict(conn.execute(
        "SELECT id, artifact_type, file_name, file_path, created_at FROM export_file WHERE id = ? AND job_id = ?",
        (output_id, job_id),
    ).fetchone())
    if not row:
        raise NotFoundError(f"Output {output_id} of job {job_id} not found.")
    path = Path(row["file_path"] or "")
    if not path.is_file():
        raise NotFoundError(
            f"{row['file_name']} is recorded but no longer on disk. Generate a new version from the Export page."
        )
    return row["file_name"], path.read_bytes()


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
