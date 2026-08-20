"""
Export engine: generates the 4-tab Salesforce-ready XLSX output file.

Output tabs:
  1. Upload    — Salesforce Facility Revenue upload format (16 columns)
  2. Validation — JDE / supplier name / PEC / Account ID / DB name / City / State / Method
  3. Unmatched — Raw rows that could not be matched
  4. JDE Mapping — JDE# → PEC → DB Name (for manual review and future reference)
"""

from pathlib import Path
from typing import Optional

import pandas as pd
import xlsxwriter

from .constants import SF_UPLOAD_COLUMNS


def export_job(
    conn: object,
    job_id: int,
    output_path: Optional[str] = None,
) -> str:
    """
    Generate the 4-tab XLSX output for a completed or needs_review job.

    Returns the path to the output file.
    """
    job = conn.execute("SELECT * FROM revenue_job WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_id} not found")
    job = dict(job)

    supplier = conn.execute(
        "SELECT * FROM supplier_profile WHERE id = ?", (job["supplier_profile_id"],)
    ).fetchone()
    supplier = dict(supplier)

    # Build output path if not provided
    if not output_path:
        original_name = Path(job.get("original_file_name", f"job_{job_id}")).stem
        output_dir = Path(job.get("file_path", ".")).parent
        output_path = str(output_dir / f"{original_name} Processed.xlsx")

    # Load data
    upload_df     = _build_upload_df(conn, job_id)
    validation_df = _build_validation_df(conn, job_id)
    unmatched_df  = _build_unmatched_df(conn, job_id)
    jde_map_df    = _build_jde_mapping_df(conn, job_id)

    # Write XLSX
    _write_xlsx(output_path, upload_df, validation_df, unmatched_df, jde_map_df)

    # Record export in export_file table
    conn.execute(
        """
        INSERT INTO export_file (job_id, artifact_type, file_name, file_path)
        VALUES (?, 'salesforce_upload', ?, ?)
        """,
        (job_id, Path(output_path).name, output_path),
    )
    conn.commit()

    return output_path


def _build_upload_df(conn: object, job_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT
            pec             AS "Pec",
            facility_id     AS "Facility ID",
            contract_id     AS "Contract ID",
            payment_id      AS "Payment ID",
            ROUND(volume, 2) AS "Volume",
            ROUND(total_revenue, 2) AS "Total Revenue",
            revenue_type    AS "Revenue Type",
            keep_return     AS "Keep/Return?",
            earned_period   AS "Earned Period",
            quarter_label   AS "Pmt For (Time Frame)",
            date_received   AS "Date Received",
            comments        AS "Comments",
            date_refunded   AS "Date Refunded",
            rebate_retained AS "Rebate Retained",
            amount_retained AS "Amount Retained",
            retained_reason AS "Retained Reason"
        FROM revenue_output_row
        WHERE job_id = ?
        ORDER BY pec, earned_period
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
    date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd"})
    money_fmt = workbook.add_format({"num_format": "#,##0.00"})
    unmatched_header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#C00000", "font_color": "#FFFFFF",
        "border": 1,
    })

    def write_sheet(ws, df, hfmt, col_widths=None):
        if df.empty:
            ws.write(0, 0, "No data", hfmt)
            return
        for col_idx, col_name in enumerate(df.columns):
            ws.write(0, col_idx, col_name, hfmt)
        for row_idx, row in df.iterrows():
            for col_idx, val in enumerate(row):
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    ws.write(row_idx + 1, col_idx, "")
                else:
                    ws.write(row_idx + 1, col_idx, val)
        # Auto-width columns
        if col_widths:
            for col_idx, width in enumerate(col_widths):
                ws.set_column(col_idx, col_idx, width)
        else:
            for col_idx, col_name in enumerate(df.columns):
                max_len = max(len(str(col_name)), df[col_name].astype(str).str.len().max() if not df.empty else 0)
                ws.set_column(col_idx, col_idx, min(max_len + 2, 40))
        ws.freeze_panes(1, 0)

    upload_widths = [12, 18, 20, 18, 12, 14, 12, 12, 14, 16, 14, 12, 14, 14, 14, 16]
    ws_upload = workbook.add_worksheet("Upload")
    write_sheet(ws_upload, upload_df, header_fmt, upload_widths)

    ws_validation = workbook.add_worksheet("Validation")
    write_sheet(ws_validation, validation_df, header_fmt)

    ws_unmatched = workbook.add_worksheet("Unmatched")
    write_sheet(ws_unmatched, unmatched_df, unmatched_header_fmt)

    ws_jde = workbook.add_worksheet("JDE Mapping")
    write_sheet(ws_jde, jde_map_df, header_fmt)

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
