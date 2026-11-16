"""
Export engine: generates the output workbooks for a job.

Processed workbook (4 tabs):
  1. Upload    — Salesforce Facility Revenue upload template (16 columns, SF_UPLOAD_COLUMNS)
  2. Validation — JDE / supplier name / PEC / Account ID / DB name / City / State / Method
  3. Unmatched — Raw rows that could not be matched
  4. JDE Mapping — JDE# → PEC → DB Name (for manual review and future reference)

Exceptions workbook:
  1. Reconciliation — input = output + exceptions, per amount, and the source files
  2. Exceptions — every row left out of the Upload tab, with the reason
  3. Duplicates — groups of identical rows (kept and counted, reported here)
  4. Dropped Rows — summary/total rows stripped before processing

Files are written to an output folder (never next to the source files) under a
timestamped name, and an existing file is never overwritten (see
reserve_output_path). Every file written is recorded in export_file.

Upload template rules, the same for every supplier:
  - Facility ID: the Premier entity code / verified GPO ID when there is one,
    else the Salesforce Account ID
  - Payment ID, Date Received: blank (filled in from the ledger afterwards)
  - Pmt For (Time Frame): the timeframe entered for the run (revenue_job.timeframe)
  - every other column the template has stays, blank when there's no value
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import xlsxwriter

from .constants import (
    ARTIFACT_EXCEPTION, ARTIFACT_SF_UPLOAD, KEY_ACCOUNT_NOT_FOUND, KEY_ADDRESS_MISMATCH, KEY_AMBIGUOUS,
    KEY_MISSING_ACCOUNT, KEY_NAME_AND_ADDRESS_MISMATCH, KEY_NAME_MISMATCH, KEY_NO_GPO_ID, KEY_NOT_LOADED,
    SF_UPLOAD_COLUMNS,
)
from .db import get_last_insert_id
from .gpo_key import normalize_account

PROCESSED_LABEL = "Processed"
EXCEPTIONS_LABEL = "Exceptions"

REASON_LABELS = {
    KEY_MISSING_ACCOUNT: "No account number in the file",
    KEY_ACCOUNT_NOT_FOUND: "Account number not in the GPO key",
    KEY_NAME_MISMATCH: "Account name differs from the GPO key",
    KEY_ADDRESS_MISMATCH: "Address (street or ZIP) differs from the GPO key",
    KEY_NAME_AND_ADDRESS_MISMATCH: "Account name and address (street or ZIP) differ from the GPO key",
    KEY_AMBIGUOUS: "Several GPO key entries match with different GPO IDs",
    KEY_NO_GPO_ID: "GPO key entry has no GPO ID",
    KEY_NOT_LOADED: "No GPO key loaded",
    "unmatched": "No facility match (Review queue)",
}


@dataclass(frozen=True)
class ExportedFile:
    id: int
    artifact_type: str
    file_name: str
    file_path: str


class OutputLocationError(ValueError):
    """The output folder is the folder a source file is in."""


def reserve_output_path(directory: Path, stem: str, label: str, now: Optional[datetime] = None) -> Path:
    """
    Create and return a new, empty '<stem> <label> <YYYYMMDD-HHMMSS>.xlsx' in *directory*.
    The file is created exclusively, so an existing file is never reused: a name
    already taken (two exports in the same second) gets a -2, -3, ... suffix.
    """
    directory.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    n = 1
    while True:
        path = directory / f"{stem} {label} {ts}{'' if n == 1 else f'-{n}'}.xlsx"
        try:
            with open(path, "x"):
                return path
        except FileExistsError:
            n += 1


def _source_dirs(conn: object, job: dict) -> set[Path]:
    paths = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM revenue_job_file WHERE job_id = ?", (job["id"],)
    ).fetchall()]
    if job.get("file_path"):
        paths.append(job["file_path"])
    return {Path(p).resolve().parent for p in paths if p}


def export_job(conn: object, job_id: int, output_dir: str | Path) -> list[ExportedFile]:
    """
    Write a new version of the processed workbook and the exceptions workbook
    for a job into *output_dir*, record both in export_file, and return them
    (processed first).
    """
    job = conn.execute("SELECT * FROM revenue_job WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_id} not found")
    job = dict(job)

    directory = Path(output_dir).resolve()
    if directory in _source_dirs(conn, job):
        raise OutputLocationError(
            f"The output folder {directory} holds this job's source files; outputs must go to a separate folder."
        )

    first_file = conn.execute(
        "SELECT original_file_name FROM revenue_job_file WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchone()
    stem = Path((first_file and first_file["original_file_name"]) or job.get("original_file_name")
                or f"job_{job_id}").stem
    now = datetime.now()
    processed_path = reserve_output_path(directory, stem, PROCESSED_LABEL, now)
    _write_xlsx(
        str(processed_path),
        _build_upload_df(conn, job_id),
        _build_validation_df(conn, job_id),
        _build_unmatched_df(conn, job_id),
        _build_jde_mapping_df(conn, job_id),
    )
    exceptions_path = reserve_output_path(directory, stem, EXCEPTIONS_LABEL, now)
    write_exceptions_workbook(conn, job, str(exceptions_path))

    exported = []
    for artifact, path in ((ARTIFACT_SF_UPLOAD, processed_path), (ARTIFACT_EXCEPTION, exceptions_path)):
        cur = conn.execute(
            "INSERT INTO export_file (job_id, artifact_type, file_name, file_path) VALUES (?, ?, ?, ?)",
            (job_id, artifact, path.name, str(path)),
        )
        exported.append(ExportedFile(get_last_insert_id(conn, cur), artifact, path.name, str(path)))
    conn.commit()
    return exported


def _build_upload_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT
            o.pec             AS "Pec",
            CASE WHEN o.pec IS NULL OR o.pec LIKE 'PENDING-%' THEN o.facility_id ELSE o.pec END
                              AS "Facility ID",
            o.contract_id     AS "Contract ID",
            NULL              AS "Payment ID",
            ROUND(o.volume, 2) AS "Volume",
            ROUND(o.total_revenue, 2) AS "Total Revenue",
            o.revenue_type    AS "Revenue Type",
            o.keep_return     AS "Keep/Return?",
            o.earned_period   AS "Earned Period",
            j.timeframe       AS "Pmt For (Time Frame)",
            NULL              AS "Date Received",
            o.comments        AS "Comments",
            o.date_refunded   AS "Date Refunded",
            o.rebate_retained AS "Rebate Retained",
            o.amount_retained AS "Amount Retained",
            o.retained_reason AS "Retained Reason"
        FROM revenue_output_row o
        JOIN revenue_job j ON j.id = o.job_id
        WHERE o.job_id = ?
        ORDER BY o.pec, o.earned_period
        """,
        (job_id,),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=SF_UPLOAD_COLUMNS)
    return df


def _build_validation_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT DISTINCT
            rr.jde_number           AS "JDE #",
            rr.customer_name        AS "Supplier Customer Name",
            rr.entity_code          AS "PEC / Entity Code",
            rr.account_id           AS "Salesforce Account ID",
            rr.account_name_db      AS "Account List Name",
            rr.city                 AS "City",
            rr.state                AS "State",
            rr.match_method         AS "Match Method",
            rr.match_score          AS "Score"
        FROM revenue_row rr
        WHERE rr.job_id = ?
          AND rr.match_status IN ('matched', 'alias_matched')
        ORDER BY rr.entity_code, rr.customer_name
        """,
        (job_id,),
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _build_unmatched_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT
            rr.row_number           AS "Row #",
            rr.jde_number           AS "JDE #",
            rr.customer_name        AS "Customer Name",
            rr.address              AS "Address",
            rr.city                 AS "City",
            rr.state                AS "State",
            rr.zip                  AS "ZIP",
            rr.process_date         AS "Process Date",
            rr.sales_amount         AS "Sales Amount",
            rr.fee_amount           AS "Fee Amount",
            rr.product_line         AS "Product Line"
        FROM revenue_row rr
        WHERE rr.job_id = ? AND rr.match_status = 'unmatched'
        ORDER BY rr.row_number
        """,
        (job_id,),
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _build_jde_mapping_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT DISTINCT
            rr.jde_number           AS "JDE #",
            rr.customer_name        AS "Supplier Name",
            rr.entity_code          AS "PEC / Entity Code",
            rr.account_name_db      AS "Account List Name"
        FROM revenue_row rr
        WHERE rr.job_id = ?
          AND rr.match_status IN ('matched', 'alias_matched')
          AND rr.jde_number IS NOT NULL
        ORDER BY rr.jde_number
        """,
        (job_id,),
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _is_missing(val) -> bool:
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


# Date columns are written as Excel dates shown mm/dd/yyyy (the upload template's format).
DATE_COLUMNS = {"Earned Period", "Date Received", "Date Refunded", "Process Date"}
DATE_FORMAT = "mm/dd/yyyy"


def _as_date(val):
    """ISO date text ('2026-09-01', '2026-09-01 00:00:00') -> date; anything else unchanged."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?", val.strip()):
        return date.fromisoformat(val.strip()[:10])
    return val


def _write_sheet(ws, df: pd.DataFrame, hfmt, col_widths=None, date_fmt=None) -> None:
    # A frame with columns but no rows still gets its header row (the Upload
    # template's columns are kept even when there's nothing to upload).
    if len(df.columns) == 0:
        ws.write(0, 0, "No data", hfmt)
        return
    date_cols = {i for i, c in enumerate(df.columns) if c in DATE_COLUMNS} if date_fmt is not None else set()
    for col_idx, col_name in enumerate(df.columns):
        ws.write(0, col_idx, col_name, hfmt)
    for row_idx, row in enumerate(df.itertuples(index=False, name=None), start=1):
        for col_idx, val in enumerate(row):
            if _is_missing(val):
                ws.write(row_idx, col_idx, "")
            elif col_idx in date_cols and isinstance(d := _as_date(val), date):
                ws.write_datetime(row_idx, col_idx, datetime(d.year, d.month, d.day), date_fmt)
            else:
                ws.write(row_idx, col_idx, val)
    # Auto-width columns
    if col_widths:
        for col_idx, width in enumerate(col_widths):
            ws.set_column(col_idx, col_idx, width)
    else:
        for col_idx, col_name in enumerate(df.columns):
            max_len = max(len(str(col_name)), df[col_name].astype(str).str.len().max() if not df.empty else 0)
            ws.set_column(col_idx, col_idx, min(max_len + 2, 40))
    ws.freeze_panes(1, 0)


def _write_xlsx(
    output_path: str,
    upload_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    unmatched_df: pd.DataFrame,
    jde_map_df: pd.DataFrame,
) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    workbook = xlsxwriter.Workbook(output_path)

    header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#1F4E79", "font_color": "#FFFFFF",
        "border": 1, "text_wrap": True, "valign": "vcenter",
    })
    date_fmt = workbook.add_format({"num_format": DATE_FORMAT})
    money_fmt = workbook.add_format({"num_format": "#,##0.00"})
    unmatched_header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#C00000", "font_color": "#FFFFFF",
        "border": 1,
    })

    upload_widths = [12, 18, 20, 18, 12, 14, 12, 12, 14, 16, 14, 12, 14, 14, 14, 16]
    ws_upload = workbook.add_worksheet("Upload")
    _write_sheet(ws_upload, upload_df, header_fmt, upload_widths, date_fmt)

    ws_validation = workbook.add_worksheet("Validation")
    _write_sheet(ws_validation, validation_df, header_fmt, date_fmt=date_fmt)

    ws_unmatched = workbook.add_worksheet("Unmatched")
    _write_sheet(ws_unmatched, unmatched_df, unmatched_header_fmt, date_fmt=date_fmt)

    ws_jde = workbook.add_worksheet("JDE Mapping")
    _write_sheet(ws_jde, jde_map_df, header_fmt)

    workbook.close()


# ---------------------------------------------------------------------------
# Exceptions workbook
# ---------------------------------------------------------------------------

def _reconciliation_df(job: dict) -> pd.DataFrame:
    rec = json.loads(job.get("reconciliation_json") or "null")
    if not rec:
        return pd.DataFrame([{"Check": "Reconciliation", "Result": "Not run for this job"}])
    inp = rec["input"]
    rows = [
        {"Amount": "Revenue (fee)", "Input": inp["revenue"], "Output (Upload tab)": rec["output_revenue"],
         "Exceptions": rec["exceptions_revenue"], "Difference": rec["revenue_difference"],
         "Reconciles": "Yes" if abs(rec["revenue_difference"]) < 0.005 else "NO"},
    ]
    if rec["volume_checked"]:
        rows.append({"Amount": "Volume (sales)", "Input": inp["volume"],
                     "Output (Upload tab)": rec["output_volume"] or 0.0, "Exceptions": rec["exceptions_volume"],
                     "Difference": rec["volume_difference"],
                     "Reconciles": "Yes" if abs(rec["volume_difference"]) < 0.005 else "NO"})
    else:
        rows.append({"Amount": "Volume (sales)", "Input": inp["volume"], "Output (Upload tab)": None,
                     "Exceptions": rec["exceptions_volume"], "Difference": None,
                     "Reconciles": "Not checked: Volume is left blank for this supplier"})
    rows.append({"Amount": "Rows", "Input": inp["rows"], "Output (Upload tab)": rec["output_rows"],
                 "Exceptions": rec["exception_rows"], "Difference": None,
                 "Reconciles": "Overall: " + ("Yes" if rec["balanced"] else "NO")})
    rows.append({})
    rows.append({"Amount": "Summary/total rows left out of the input", "Input": inp.get("dropped_rows", 0),
                 "Output (Upload tab)": None, "Exceptions": None, "Difference": None,
                 "Reconciles": f"volume {inp.get('dropped_volume', 0):,.2f}, revenue {inp.get('dropped_revenue', 0):,.2f}"})
    for label, key in (("volume", "unparseable_volume"), ("revenue", "unparseable_revenue")):
        if inp.get(key):
            rows.append({"Amount": f"Rows whose {label} isn't a number (counted as 0)", "Input": inp[key]})
    rows.append({})
    for name, n in (inp.get("rows_per_file") or {}).items():
        rows.append({"Amount": f"Source file: {name}", "Input": n, "Reconciles": "rows"})
    return pd.DataFrame(rows, columns=["Amount", "Input", "Output (Upload tab)", "Exceptions", "Difference", "Reconciles"])


def _key_entries_by_account(conn: object, supplier_profile_id: int) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT account_number_norm, account_name, address, zip, gpo_id FROM gpo_key_entry "
        "WHERE supplier_profile_id = ?",
        (supplier_profile_id,),
    ).fetchall():
        out.setdefault(r["account_number_norm"], []).append(
            f"{r['account_name'] or ''} | {r['address'] or ''} {r['zip'] or ''} | GPO ID {r['gpo_id'] or '—'}"
        )
    return {k: "; ".join(v) for k, v in out.items()}


def _exceptions_df(conn: object, job: dict) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT source_file, source_row, row_number, match_status, exception_reason,
               jde_number, customer_name, address, city, state, zip, file_entity_code,
               process_date, sales_amount, fee_amount
        FROM revenue_row
        WHERE job_id = ? AND match_status NOT IN ('matched', 'alias_matched')
        ORDER BY row_number
        """,
        (job["id"],),
    ).fetchall()
    key = _key_entries_by_account(conn, job["supplier_profile_id"]) if rows else {}
    records = []
    for r in rows:
        reason = r["exception_reason"] or r["match_status"]
        records.append({
            "Source File": r["source_file"],
            "Source Row": r["source_row"] if r["source_row"] is not None else r["row_number"],
            "Reason": REASON_LABELS.get(reason, reason),
            "Reason Code": reason,
            "Account #": r["jde_number"],
            "Account Name": r["customer_name"],
            "Address": r["address"],
            "City": r["city"],
            "State": r["state"],
            "ZIP": r["zip"],
            "GPO ID in File": r["file_entity_code"],
            "GPO Key Entry": key.get(normalize_account(r["jde_number"])),
            "Process Date": r["process_date"],
            "Volume (sales)": r["sales_amount"],
            "Revenue (fee)": r["fee_amount"],
        })
    columns = ["Source File", "Source Row", "Reason", "Reason Code", "Account #", "Account Name", "Address", "City",
               "State", "ZIP", "GPO ID in File", "GPO Key Entry", "Process Date", "Volume (sales)", "Revenue (fee)"]
    return pd.DataFrame(records, columns=columns)


def _duplicates_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT duplicate_group, source_file, source_row, row_number, jde_number, customer_name,
               process_date, sales_amount, fee_amount, match_status
        FROM revenue_row
        WHERE job_id = ? AND duplicate_group IS NOT NULL
        ORDER BY duplicate_group, row_number
        """,
        (job_id,),
    ).fetchall()
    files_per_group: dict[int, set] = {}
    for r in rows:
        files_per_group.setdefault(r["duplicate_group"], set()).add(r["source_file"])
    columns = ["Group", "Across Files", "Source File", "Source Row", "Account #", "Account Name", "Process Date",
               "Volume (sales)", "Revenue (fee)", "Status"]
    return pd.DataFrame([{
        "Group": r["duplicate_group"],
        "Across Files": "Yes" if len(files_per_group[r["duplicate_group"]]) > 1 else "No",
        "Source File": r["source_file"],
        "Source Row": r["source_row"] if r["source_row"] is not None else r["row_number"],
        "Account #": r["jde_number"],
        "Account Name": r["customer_name"],
        "Process Date": r["process_date"],
        "Volume (sales)": r["sales_amount"],
        "Revenue (fee)": r["fee_amount"],
        "Status": r["match_status"],
    } for r in rows], columns=columns)


def _dropped_df(job: dict) -> pd.DataFrame:
    rec = json.loads(job.get("reconciliation_json") or "null") or {}
    detail = (rec.get("input") or {}).get("dropped_detail") or []
    return pd.DataFrame(
        [{"Source File": d.get("source_file"), "Source Row": d.get("source_row"),
          "Volume (sales)": d.get("volume"), "Revenue (fee)": d.get("revenue")} for d in detail],
        columns=["Source File", "Source Row", "Volume (sales)", "Revenue (fee)"],
    )


def build_exception_frames(conn: object, job: dict) -> dict[str, pd.DataFrame]:
    return {
        "Reconciliation": _reconciliation_df(job),
        "Exceptions": _exceptions_df(conn, job),
        "Duplicates": _duplicates_df(conn, job["id"]),
        "Dropped Rows": _dropped_df(job),
    }


def write_exceptions_workbook(conn: object, job: dict, output_path: str) -> None:
    frames = build_exception_frames(conn, job)
    workbook = xlsxwriter.Workbook(output_path)
    header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#C00000", "font_color": "#FFFFFF",
        "border": 1, "text_wrap": True, "valign": "vcenter",
    })
    date_fmt = workbook.add_format({"num_format": DATE_FORMAT})
    for name, df in frames.items():
        _write_sheet(workbook.add_worksheet(name), df, header_fmt, date_fmt=date_fmt)
    workbook.close()


def export_to_dataframes(
    conn: object,
    job_id: int,
) -> dict[str, pd.DataFrame]:
    """
    Return the four output DataFrames without writing a file.
    Useful for previewing in the Streamlit UI.
    """
    return {
        "Upload":      _build_upload_df(conn, job_id),
        "Validation":  _build_validation_df(conn, job_id),
        "Unmatched":   _build_unmatched_df(conn, job_id),
        "JDE Mapping": _build_jde_mapping_df(conn, job_id),
    }
