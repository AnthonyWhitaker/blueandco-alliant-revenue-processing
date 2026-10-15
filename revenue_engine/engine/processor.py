"""
Revenue Processing Engine — Core Orchestrator.

Drives the full pipeline for a single supplier file:
  1. Validate job exists and file is readable
  2. Read file and map columns to canonical fields
  3. Resolve Payment ID
  4. Match each customer row to a member (using MemberMatcher)
  5. Persist revenue_row records
  6. Aggregate matched rows into revenue_output_row records
  7. Create member_review_item records for unmatched rows
  8. Create exception records for notable issues
  9. Update job status and statistics

This module is supplier-agnostic — all supplier-specific behavior lives
in the supplier_profile and supplier_column_map database records.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .constants import (
    CANONICAL_CUSTOMER_NAME, CANONICAL_JDE_NUMBER, CANONICAL_PROCESS_DATE,
    CANONICAL_CITY, CANONICAL_STATE, CANONICAL_ZIP, CANONICAL_ADDRESS,
    CANONICAL_SALES_AMOUNT, CANONICAL_FEE_AMOUNT, CANONICAL_PRODUCT_LINE,
    CANONICAL_CONTRACT_NUMBER, CANONICAL_ENTITY_CODE_HINT, CANONICAL_ACCOUNT_ID_HINT,
    STATUS_PROCESSING, STATUS_COMPLETED, STATUS_FAILED, STATUS_NEEDS_REVIEW,
    ROW_MATCHED, ROW_UNMATCHED, ROW_ALIAS_MATCHED,
    MATCH_UNMATCHED, MATCH_JDE_HISTORY, MATCH_ALIAS, MATCH_PIVOT_ALLOCATION,
    EXC_UNMATCHED_CUSTOMER, EXC_PAYMENT_NOT_FOUND,
    EXC_REVENUE_MISMATCH, EXC_PROCESSING_ERROR,
    EXC_EXTRACTION_NOT_FOUND, EXC_ALLOCATION_INCOMPLETE,
    EXC_MISSING_ENTITY_CODE,
    REVIEW_PENDING, JOB_TYPE_PIVOT_ALLOCATION,
)
from .db import execute_upsert, execute_insert_ignore, get_last_insert_id
from .file_reader import read_supplier_file
from .member_matcher import MemberMatcher
from .normalizer import parse_date, first_of_month, get_quarter_label, parse_deposit_amount_from_filename, parse_date_from_filename
from .payment_resolver import resolve_payment_id
from .pivot_extractor import extract_totals, ExtractionError
from .allocation_engine import allocate, AllocationError


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _job_log(conn: object, job_id: int, exc_type: str, message: str, severity: str = "warning", row_id: Optional[int] = None) -> None:
    conn.execute(
        "INSERT INTO exception (job_id, row_id, exception_type, message, severity) VALUES (?, ?, ?, ?, ?)",
        (job_id, row_id, exc_type, message, severity),
    )


def process_job(
    conn: object,
    job_id: int,
    manual_payment_id: Optional[str] = None,
) -> dict:
    """
    Run the full processing pipeline for a revenue_job record.

    Returns a summary dict with statistics.
    Raises on unrecoverable errors (file not found, missing required columns).
    """
    # Load job and supplier profile
    job = conn.execute("SELECT * FROM revenue_job WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    job = dict(job)
    supplier = conn.execute(
        "SELECT * FROM supplier_profile WHERE id = ?", (job["supplier_profile_id"],)
    ).fetchone()
    if not supplier:
        raise ValueError(f"Supplier profile {job['supplier_profile_id']} not found")
    supplier = dict(supplier)

    file_path = job["file_path"]
    if not file_path or not Path(file_path).exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Mark job as processing
    conn.execute(
        "UPDATE revenue_job SET status = ? WHERE id = ?",
        (STATUS_PROCESSING, job_id),
    )
    conn.commit()

    try:
        if supplier.get("job_type") == JOB_TYPE_PIVOT_ALLOCATION:
            return _run_pivot_allocation_pipeline(conn, job, supplier, file_path, manual_payment_id)
        return _run_pipeline(conn, job, supplier, file_path, manual_payment_id)
    except Exception as e:
        conn.execute(
            "UPDATE revenue_job SET status = ?, error_message = ? WHERE id = ?",
            (STATUS_FAILED, str(e)[:2000], job_id),
        )
        _job_log(conn, job_id, EXC_PROCESSING_ERROR, str(e), "error")
        conn.commit()
        raise


def _run_pipeline(
    conn: object,
    job: dict,
    supplier: dict,
    file_path: str,
    manual_payment_id: Optional[str],
) -> dict:
    job_id = job["id"]
    supplier_id = supplier["id"]

    # ------------------------------------------------------------------
    # Step 1: Read file
    # ------------------------------------------------------------------
    df, file_warnings = read_supplier_file(file_path, supplier, conn)
    for w in file_warnings:
        _job_log(conn, job_id, "file_warning", w, "info")

    # Store confirmed column mapping in job record
    col_mapping = {c: c for c in df.columns}
    conn.execute(
        "UPDATE revenue_job SET column_mapping_json = ?, row_count = ? WHERE id = ?",
        (json.dumps(col_mapping), len(df), job_id),
    )
    conn.commit()

    # ------------------------------------------------------------------
    # Step 2: Resolve Payment ID
    # ------------------------------------------------------------------
    payment_id, deposit_amount, pid_source = resolve_payment_id(
        conn=conn,
        supplier_profile_id=supplier_id,
        filename=job.get("original_file_name") or Path(file_path).name,
        payor_filter=supplier.get("payor_filter_name"),
        manual_payment_id=manual_payment_id or job.get("payment_id"),
    )
    if not payment_id:
        _job_log(
            conn, job_id, EXC_PAYMENT_NOT_FOUND,
            f"Could not resolve Payment ID from ledger for deposit amount "
            f"{deposit_amount}. Set manually in job record.",
            "warning",
        )

    conn.execute(
        "UPDATE revenue_job SET payment_id = ?, payment_id_source = ?, deposit_amount = ? WHERE id = ?",
        (payment_id, pid_source, deposit_amount, job_id),
    )
    conn.commit()

    # ------------------------------------------------------------------
    # Step 3: Initialize MemberMatcher
    # ------------------------------------------------------------------
    matcher = MemberMatcher(
        conn=conn,
        supplier_profile_id=supplier_id,
        zip_state_threshold=supplier.get("fuzzy_zip_state_threshold", 75),
        city_state_threshold=supplier.get("fuzzy_city_state_threshold", 75),
        state_threshold=supplier.get("fuzzy_state_threshold", 80),
        nationwide_threshold=supplier.get("fuzzy_nationwide_threshold", 85),
    )
    matcher.load()

    # ------------------------------------------------------------------
    # Step 4: Deduplicate customers, match each unique customer once
    # ------------------------------------------------------------------
    # Group by JDE number (or customer name if no JDE) to avoid redundant matching
    if CANONICAL_JDE_NUMBER in df.columns:
        jde_col = CANONICAL_JDE_NUMBER
    else:
        jde_col = None

    # Build unique customer list for matching
    group_cols = []
    if jde_col:
        group_cols.append(jde_col)
    group_cols.append(CANONICAL_CUSTOMER_NAME)

    # Cache: jde_or_name → MatchResult
    match_cache: dict[str, object] = {}

    def get_cache_key(row_series) -> str:
        if jde_col and pd.notna(row_series.get(jde_col)):
            return f"jde:{row_series[jde_col]}"
        aid = row_series.get(CANONICAL_ACCOUNT_ID_HINT)
        if pd.notna(aid) and str(aid).strip():
            return f"aid:{aid}"
        ec = row_series.get(CANONICAL_ENTITY_CODE_HINT)
        if pd.notna(ec) and str(ec).strip():
            # Some supplier files (e.g. Bon Secours) reuse the same entity
            # code across multiple distinct facility names, so the hint
            # alone is not a safe cache key on its own — fold in name/state
            # too, otherwise the first row's (possibly unmatched) fuzzy
            # result gets wrongly reused for every other name sharing the code.
            name = row_series.get(CANONICAL_CUSTOMER_NAME, "")
            state = row_series.get(CANONICAL_STATE, "")
            return f"ec:{ec}|{name}|{state}"
        return f"name:{row_series.get(CANONICAL_CUSTOMER_NAME, '')}"

    # ------------------------------------------------------------------
    # Step 5: Process each row
    # ------------------------------------------------------------------
    row_ids: list[int] = []
    matched_count = 0
    unmatched_count = 0

    # Parse dates from process_date column
    dates_seen: list[str] = []

    for row_num, (_, row) in enumerate(df.iterrows(), start=1):
        # Parse canonical values
        jde_num   = str(row.get(CANONICAL_JDE_NUMBER, "") or "").strip() if jde_col else None
        cust_name = str(row.get(CANONICAL_CUSTOMER_NAME, "") or "").strip()
        entity_code_hint = str(row.get(CANONICAL_ENTITY_CODE_HINT, "") or "").strip() or None
        account_id_hint  = str(row.get(CANONICAL_ACCOUNT_ID_HINT, "") or "").strip() or None
        if not cust_name:
            # Fall back to the identity hint for display purposes (review UI, logs)
            cust_name = entity_code_hint or account_id_hint or ""
        proc_date = parse_date(row.get(CANONICAL_PROCESS_DATE))
        if not proc_date:
            proc_date = parse_date_from_filename(
                job.get("original_file_name") or ""
            )
        city      = str(row.get(CANONICAL_CITY, "") or "").strip()
        state     = str(row.get(CANONICAL_STATE, "") or "").strip()
        zip_code  = str(row.get(CANONICAL_ZIP, "") or "").strip()
        address   = str(row.get(CANONICAL_ADDRESS, "") or "").strip()
        sales_amt = _safe_float(row.get(CANONICAL_SALES_AMOUNT))
        fee_amt   = _safe_float(row.get(CANONICAL_FEE_AMOUNT))
        product   = str(row.get(CANONICAL_PRODUCT_LINE, "") or "").strip()
        contract  = str(row.get(CANONICAL_CONTRACT_NUMBER, "") or "").strip()

        if proc_date:
            dates_seen.append(proc_date)

        # Run match (from cache if same JDE/name already matched)
        cache_key = get_cache_key(row)
        if cache_key not in match_cache:
            result = matcher.match(
                customer_name=cust_name,
                jde_number=jde_num,
                city=city,
                state=state,
                zip_code=zip_code,
                entity_code_hint=entity_code_hint,
                account_id_hint=account_id_hint,
            )
            match_cache[cache_key] = result
            # Persist JDE history for successful matches
            if result.matched and jde_num:
                matcher.record_jde_match(jde_num, result.member_id, job_id)
        else:
            result = match_cache[cache_key]

        # Determine row status
        if result.matched:
            row_status = ROW_ALIAS_MATCHED if result.match_method == MATCH_ALIAS else ROW_MATCHED
            matched_count += 1
        else:
            row_status = ROW_UNMATCHED
            unmatched_count += 1

        # Persist raw row as JSON (all columns)
        raw_json = json.dumps(
            {c: (None if pd.isna(v) else str(v)) for c, v in row.items()},
            default=str,
        )

        cursor = conn.execute(
            """
            INSERT INTO revenue_row (
                job_id, row_number, raw_row_json,
                jde_number, customer_name, process_date,
                city, state, zip, address,
                sales_amount, fee_amount, product_line, contract_number,
                matched_member_id, match_method, match_score, match_geo_filter,
                entity_code, account_id, account_name_db,
                match_status, validation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, row_num, raw_json,
                jde_num or None, cust_name or None, proc_date,
                city or None, state or None, zip_code or None, address or None,
                sales_amt, fee_amt, product or None, contract or None,
                result.member_id, result.match_method, result.match_score,
                result.geo_filter,
                result.entity_code, result.account_id, result.account_name,
                row_status, "valid" if result.matched else "pending",
            ),
        )
        row_ids.append(get_last_insert_id(conn, cursor))

    conn.commit()

    # ------------------------------------------------------------------
    # Step 6: Build review items for unmatched customers
    # ------------------------------------------------------------------
    _build_review_items(conn, job_id, supplier_id, matcher)

    # ------------------------------------------------------------------
    # Step 7: Aggregate matched rows → revenue_output_row
    # ------------------------------------------------------------------
    total_revenue, total_volume = _aggregate_output(conn, job_id, job, supplier, payment_id)

    # ------------------------------------------------------------------
    # Step 8: Revenue reconciliation check
    # ------------------------------------------------------------------
    if deposit_amount and total_revenue:
        diff = abs(total_revenue - deposit_amount)
        if diff > 1.00:
            _job_log(
                conn, job_id, EXC_REVENUE_MISMATCH,
                f"Total matched revenue ${total_revenue:.2f} differs from "
                f"deposit amount ${deposit_amount:.2f} by ${diff:.2f}.",
                "warning",
            )

    # ------------------------------------------------------------------
    # Step 9: Finalize job status
    # ------------------------------------------------------------------
    final_status = STATUS_NEEDS_REVIEW if unmatched_count > 0 else STATUS_COMPLETED
    date_start = min(dates_seen) if dates_seen else None
    date_end   = max(dates_seen) if dates_seen else None

    conn.execute(
        """
        UPDATE revenue_job SET
            status = ?, matched_count = ?, unmatched_count = ?,
            total_volume = ?, total_revenue = ?,
            earned_period_start = ?, earned_period_end = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            final_status, matched_count, unmatched_count,
            total_volume, total_revenue,
            date_start, date_end,
            job_id,
        ),
    )
    conn.commit()

    return {
        "job_id":           job_id,
        "status":           final_status,
        "row_count":        len(df),
        "matched_count":    matched_count,
        "unmatched_count":  unmatched_count,
        "total_volume":     total_volume,
        "total_revenue":    total_revenue,
        "deposit_amount":   deposit_amount,
        "payment_id":       payment_id,
        "payment_id_source": pid_source,
    }


def _run_pivot_allocation_pipeline(
    conn: object,
    job: dict,
    supplier: dict,
    file_path: str,
    manual_payment_id: Optional[str],
) -> dict:
    """
    Tier C pipeline for supplier_profile.job_type = 'pivot_allocation':
    aggregate/pivot workpapers with no facility-level rows. Extracts one or
    more dollar totals (supplier_extraction_rule) and allocates each to
    pre-known facilities (supplier_allocation_rule) instead of running
    MemberMatcher. Produces the same revenue_row / revenue_output_row shapes
    as the row-level pipeline so export/ledger-linking/Admin views work
    unmodified.
    """
    job_id = job["id"]
    supplier_id = supplier["id"]

    # ------------------------------------------------------------------
    # Step 1: Resolve Payment ID (identical to row-level pipeline)
    # ------------------------------------------------------------------
    payment_id, deposit_amount, pid_source = resolve_payment_id(
        conn=conn,
        supplier_profile_id=supplier_id,
        filename=job.get("original_file_name") or Path(file_path).name,
        payor_filter=supplier.get("payor_filter_name"),
        manual_payment_id=manual_payment_id or job.get("payment_id"),
    )
    if not payment_id:
        _job_log(
            conn, job_id, EXC_PAYMENT_NOT_FOUND,
            f"Could not resolve Payment ID from ledger for deposit amount "
            f"{deposit_amount}. Set manually in job record.",
            "warning",
        )
    conn.execute(
        "UPDATE revenue_job SET payment_id = ?, payment_id_source = ?, deposit_amount = ? WHERE id = ?",
        (payment_id, pid_source, deposit_amount, job_id),
    )
    conn.commit()

    # ------------------------------------------------------------------
    # Step 2: Extract totals from the pivot/summary sheet
    # ------------------------------------------------------------------
    extraction = extract_totals(file_path, supplier_id, conn)
    for w in extraction.warnings:
        _job_log(conn, job_id, EXC_EXTRACTION_NOT_FOUND, w, "error")

    if extraction.manual_entry_rules:
        for name in extraction.manual_entry_rules:
            _job_log(
                conn, job_id, EXC_EXTRACTION_NOT_FOUND,
                f"Extraction rule {name!r} is configured as manual_entry — "
                f"a reviewer must enter the confirmed total before this job can complete.",
                "warning",
            )

    # ------------------------------------------------------------------
    # Step 3: Allocate each extracted total to its configured facilities
    # ------------------------------------------------------------------
    allocations, alloc_warnings = [], []
    if extraction.values:
        try:
            allocations, alloc_warnings = allocate(
                conn, supplier_id, extraction.values, extraction.is_sales_amount
            )
        except AllocationError as e:
            _job_log(conn, job_id, EXC_ALLOCATION_INCOMPLETE, str(e), "error")
    for w in alloc_warnings:
        _job_log(conn, job_id, EXC_ALLOCATION_INCOMPLETE, w, "warning")

    # ------------------------------------------------------------------
    # Step 4: Persist one synthetic revenue_row per allocation
    # ------------------------------------------------------------------
    process_date = job.get("date_received") or date.today().replace(day=1)
    row_num = 0
    for a in allocations:
        row_num += 1
        member = conn.execute("SELECT * FROM member WHERE id = ?", (a.member_id,)).fetchone()
        if not member:
            _job_log(
                conn, job_id, EXC_ALLOCATION_INCOMPLETE,
                f"Allocation for {a.extraction_rule_name!r} references member_id={a.member_id}, "
                f"which no longer exists in the member table — skipped.",
                "error",
            )
            continue
        member = dict(member)
        raw_json = json.dumps(
            {"extraction_rule_name": a.extraction_rule_name, "amount": a.amount, **extraction.values},
            default=str,
        )
        conn.execute(
            """
            INSERT INTO revenue_row (
                job_id, row_number, raw_row_json,
                customer_name, process_date,
                sales_amount, fee_amount, product_line, contract_number,
                matched_member_id, match_method, match_score,
                entity_code, account_id, account_name_db,
                match_status, validation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, row_num, raw_json,
                member["name"], process_date,
                a.amount if a.is_sales_amount else None,
                None if a.is_sales_amount else a.amount,
                a.product_line, a.contract_number or supplier.get("contract_id"),
                member["id"], MATCH_PIVOT_ALLOCATION, 100,
                member["entity_code"], member["account_id"], member["name"],
                ROW_MATCHED, "valid",
            ),
        )
    conn.commit()

    # ------------------------------------------------------------------
    # Step 5: Aggregate → revenue_output_row (reuses the row-level function)
    # ------------------------------------------------------------------
    total_revenue, total_volume = _aggregate_output(conn, job_id, job, supplier, payment_id)

    # ------------------------------------------------------------------
    # Step 6: Revenue reconciliation check (identical to row-level pipeline)
    # ------------------------------------------------------------------
    if deposit_amount and total_revenue:
        diff = abs(total_revenue - deposit_amount)
        if diff > 1.00:
            _job_log(
                conn, job_id, EXC_REVENUE_MISMATCH,
                f"Total allocated revenue ${total_revenue:.2f} differs from "
                f"deposit amount ${deposit_amount:.2f} by ${diff:.2f}.",
                "warning",
            )

    # ------------------------------------------------------------------
    # Step 7: Finalize job status
    # ------------------------------------------------------------------
    has_open_exceptions = bool(extraction.warnings or extraction.manual_entry_rules or alloc_warnings)
    needs_confirmation = bool(supplier.get("requires_manual_confirmation"))
    final_status = STATUS_NEEDS_REVIEW if (has_open_exceptions or needs_confirmation) else STATUS_COMPLETED

    conn.execute(
        """
        UPDATE revenue_job SET
            status = ?, matched_count = ?, unmatched_count = ?,
            total_volume = ?, total_revenue = ?,
            earned_period_start = ?, earned_period_end = ?,
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            final_status, len(allocations), 0,
            total_volume, total_revenue,
            process_date, process_date,
            job_id,
        ),
    )
    conn.commit()

    return {
        "job_id":           job_id,
        "status":           final_status,
        "row_count":        len(allocations),
        "matched_count":    len(allocations),
        "unmatched_count":  0,
        "total_volume":     total_volume,
        "total_revenue":    total_revenue,
        "deposit_amount":   deposit_amount,
        "payment_id":       payment_id,
        "payment_id_source": pid_source,
        "extracted_values": extraction.values,
    }


def _build_review_items(
    conn: object,
    job_id: int,
    supplier_id: int,
    matcher: MemberMatcher,
) -> None:
    """
    For each unique unmatched customer in the job, create a member_review_item.
    Groups by JDE number (or customer name) and computes total fee impact.
    """
    # Non-grouped columns must be aggregated: SQL Server rejects bare columns
    # (SQLite silently picks an arbitrary row's value). MIN/MAX also make the
    # representative name/location deterministic.
    rows = conn.execute(
        """
        SELECT MAX(jde_number) AS jde_number, MIN(customer_name) AS customer_name,
               MIN(city) AS city, MIN(state) AS state, MIN(zip) AS zip,
               COUNT(*) AS affected_rows,
               SUM(fee_amount) AS total_fee,
               SUM(sales_amount) AS total_sales
        FROM revenue_row
        WHERE job_id = ? AND match_status = 'unmatched'
        GROUP BY COALESCE(jde_number, customer_name)
        ORDER BY total_fee DESC
        """,
        (job_id,),
    ).fetchall()

    for r in rows:
        suggestion, score = matcher.suggest_for_review(
            customer_name=r["customer_name"],
            state=r["state"],
        )
        conn.execute(
            """
            INSERT INTO member_review_item (
                job_id, jde_number, customer_name_sample, canonical_key,
                city, state, zip,
                suggested_member_id, suggested_match_score,
                total_fee, total_sales, affected_row_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                r["jde_number"],
                r["customer_name"],
                r["customer_name"],   # UI can display the raw name
                r["city"],
                r["state"],
                r["zip"],
                suggestion["id"] if suggestion else None,
                score,
                r["total_fee"],
                r["total_sales"],
                r["affected_rows"],
                REVIEW_PENDING,
            ),
        )
    conn.commit()


def _aggregate_output(
    conn: object,
    job_id: int,
    job: dict,
    supplier: dict,
    payment_id: Optional[str],
) -> tuple[float, float]:
    """
    Aggregate matched revenue_row records into revenue_output_row.
    Groups by (entity_code, earned_period).
    Returns (total_revenue, total_volume).
    """
    # NOTE: entity_code can be NULL for a member created via the "add new
    # facility" flow (Review page / Admin > Members) before a real Premier
    # Entity Code has been assigned. Those rows must still count toward
    # total_revenue and appear in the output (with a PENDING-<member_id>
    # placeholder PEC) rather than being silently dropped — previously the
    # "AND entity_code IS NOT NULL" filter here made matched revenue vanish
    # from the job total with no warning anywhere.
    rows = conn.execute(
        """
        SELECT entity_code, account_id, matched_member_id, process_date, contract_number,
               SUM(sales_amount) AS volume,
               SUM(fee_amount)   AS total_revenue
        FROM revenue_row
        WHERE job_id = ? AND match_status IN ('matched', 'alias_matched')
        GROUP BY entity_code, account_id, matched_member_id, process_date, contract_number
        ORDER BY entity_code, process_date
        """,
        (job_id,),
    ).fetchall()

    default_contract_id = job.get("contract_id") or supplier.get("contract_id")
    revenue_type = supplier.get("revenue_type", "Admin Fee")
    keep_return  = supplier.get("keep_return_default", "Keep")
    date_received = job.get("date_received")

    total_revenue = 0.0
    total_volume  = 0.0

    for r in rows:
        earned_period = first_of_month(r["process_date"])
        quarter_label = get_quarter_label(r["process_date"])
        rev = r["total_revenue"] or 0.0
        vol = r["volume"] or 0.0
        total_revenue += rev
        total_volume  += vol
        row_contract_id = r["contract_number"] or default_contract_id

        pec = r["entity_code"]
        if not pec:
            pec = f"PENDING-{r['matched_member_id']}"
            _job_log(
                conn, job_id, EXC_MISSING_ENTITY_CODE,
                f"Matched member id={r['matched_member_id']} has no Premier "
                f"Entity Code — ${rev:.2f} is included in the job total but "
                f"output row uses placeholder PEC '{pec}'. Assign a real "
                f"entity code in Admin > Members before disbursement.",
                "warning",
            )

        conn.execute(
            """
            INSERT INTO revenue_output_row (
                job_id, pec, facility_id, contract_id, payment_id,
                volume, total_revenue, revenue_type, keep_return,
                earned_period, quarter_label, date_received
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                pec,
                r["account_id"],
                row_contract_id,
                payment_id,
                vol,
                rev,
                revenue_type,
                keep_return,
                earned_period,
                quarter_label,
                date_received,
            ),
        )

    conn.commit()
    return total_revenue, total_volume


def apply_review_decision(
    conn: object,
    review_item_id: int,
    resolved_member_id: Optional[int],
    resolution_scope: str,
    resolved_by: str,
    notes: str = "",
    create_alias: bool = False,
    supplier_profile_id: Optional[int] = None,
) -> int:
    """
    Apply a reviewer's decision for an unmatched customer:
      - Updates the member_review_item record
      - Updates all unmatched revenue_row records in the job
      - Re-aggregates output rows for the affected job
      - Optionally creates a member_alias for future auto-matching

    Returns the number of rows updated.
    """
    item = conn.execute(
        "SELECT * FROM member_review_item WHERE id = ?", (review_item_id,)
    ).fetchone()
    if not item:
        raise ValueError(f"Review item {review_item_id} not found")
    item = dict(item)
    job_id = item["job_id"]

    if resolved_member_id is None:
        # Dismissed — mark rows as intentionally unmatched (not an error)
        conn.execute(
            "UPDATE member_review_item SET status = 'dismissed', resolved_by = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (resolved_by, review_item_id),
        )
        conn.commit()
        return 0

    # Load member details
    member = conn.execute(
        "SELECT * FROM member WHERE id = ?", (resolved_member_id,)
    ).fetchone()
    if not member:
        raise ValueError(f"Member {resolved_member_id} not found")
    member = dict(member)

    # Update revenue_row records in this job
    match_filter = {
        "jde_number": item.get("jde_number"),
        "customer_name_sample": item.get("customer_name_sample"),
    }

    if match_filter["jde_number"]:
        cursor = conn.execute(
            """
            UPDATE revenue_row SET
                matched_member_id = ?, match_method = 'user_approved',
                match_score = 100,
                entity_code = ?, account_id = ?, account_name_db = ?,
                match_status = 'matched'
            WHERE job_id = ? AND jde_number = ? AND match_status = 'unmatched'
            """,
            (
                resolved_member_id,
                member["entity_code"], member["account_id"], member["name"],
                job_id, match_filter["jde_number"],
            ),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE revenue_row SET
                matched_member_id = ?, match_method = 'user_approved',
                match_score = 100,
                entity_code = ?, account_id = ?, account_name_db = ?,
                match_status = 'matched'
            WHERE job_id = ? AND customer_name = ? AND match_status = 'unmatched'
            """,
            (
                resolved_member_id,
                member["entity_code"], member["account_id"], member["name"],
                job_id, match_filter["customer_name_sample"],
            ),
        )
    updated_rows = cursor.rowcount

    # Update review item status
    conn.execute(
        """
        UPDATE member_review_item SET
            status = 'approved', resolved_member_id = ?,
            resolution_scope = ?, resolved_by = ?,
            resolved_at = CURRENT_TIMESTAMP, notes = ?
        WHERE id = ?
        """,
        (resolved_member_id, resolution_scope, resolved_by, notes, review_item_id),
    )

    # A job_only decision applies to this job's rows and nothing else: no
    # alias and no JDE history, so future jobs don't inherit it.
    remember = resolution_scope != "job_only"

    # Create alias if requested
    if create_alias and remember and item.get("customer_name_sample"):
        from .normalizer import normalize_key
        raw_name = item["customer_name_sample"]
        canon_key = normalize_key(raw_name)
        sp_id = supplier_profile_id if resolution_scope == "supplier" else None
        try:
            execute_insert_ignore(
                conn,
                """
                INSERT OR IGNORE INTO member_alias
                    (member_id, supplier_profile_id, raw_name, canonical_key,
                     jde_number, approved_by, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    resolved_member_id, sp_id, raw_name, canon_key,
                    item.get("jde_number"), resolved_by,
                ),
            )
        except Exception:
            pass

    # Also update JDE history if we have a JDE number
    if remember and item.get("jde_number") and supplier_profile_id:
        execute_upsert(
            conn,
            """
            INSERT INTO jde_member_history
                (supplier_profile_id, jde_number, member_id, first_seen_job_id, last_seen_job_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(supplier_profile_id, jde_number)
            DO UPDATE SET
                member_id = excluded.member_id,
                last_seen_job_id = excluded.last_seen_job_id,
                occurrence_count = occurrence_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (supplier_profile_id, item["jde_number"], resolved_member_id, job_id, job_id),
        )

    conn.commit()

    # Re-aggregate output for this job
    conn.execute("DELETE FROM revenue_output_row WHERE job_id = ?", (job_id,))
    conn.commit()
    job_row = conn.execute("SELECT * FROM revenue_job WHERE id = ?", (job_id,)).fetchone()
    supplier_row = conn.execute(
        "SELECT * FROM supplier_profile WHERE id = ?", (dict(job_row)["supplier_profile_id"],)
    ).fetchone()
    payment_id = dict(job_row).get("payment_id")
    total_revenue, total_volume = _aggregate_output(conn, job_id, dict(job_row), dict(supplier_row), payment_id)

    # Update job stats
    still_unmatched = conn.execute(
        "SELECT COUNT(*) FROM revenue_row WHERE job_id = ? AND match_status = 'unmatched'",
        (job_id,),
    ).fetchone()[0]
    new_status = STATUS_COMPLETED if still_unmatched == 0 else STATUS_NEEDS_REVIEW
    conn.execute(
        "UPDATE revenue_job SET status = ?, unmatched_count = ?, total_revenue = ?, total_volume = ? WHERE id = ?",
        (new_status, still_unmatched, total_revenue, total_volume, job_id),
    )
    conn.commit()

    return updated_rows
