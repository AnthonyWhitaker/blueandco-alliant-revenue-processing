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
    EXC_MISSING_ENTITY_CODE, EXC_MANUAL_TOTAL,
    REVIEW_PENDING, JOB_TYPE_PIVOT_ALLOCATION,
    IDENTITY_GPO_KEY, VOLUME_BLANK, ROW_KEY_EXCEPTION, MATCH_GPO_KEY,
    EXC_KEY_VERIFICATION, EXC_GPO_ID_CORRECTED, EXC_DUPLICATE_ROWS, EXC_DUPLICATE_FILE,
    EXC_RECONCILIATION, EXC_DROPPED_ROWS, EXC_TIMEFRAME_MISSING,
)
from .combine import (
    SOURCE_FILE_COL, DuplicateFinder, SourceFile,
    amount_totals, content_columns, read_files,
)
from .db import execute_upsert, execute_insert_ignore, get_last_insert_id
from .file_reader import SOURCE_ROW_COL
from .gpo_key import GpoKey
from .member_matcher import MemberMatcher
from .normalizer import parse_date, first_of_month, get_quarter_label, parse_deposit_amount_from_filename, parse_date_from_filename
from .payment_resolver import resolve_payment_id
from .pivot_extractor import extract_totals, ExtractionError
from .allocation_engine import allocate, AllocationError
from .reconciliation import (
    InputTotals, Reconciliation, input_totals_from_json, input_totals_from_rows, reconcile,
)


def _is_blank(val) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _text(val) -> str:
    """Cell value as stripped text; blank/NaN cells become '' (not the string 'nan')."""
    return "" if _is_blank(val) else str(val).strip()


def _safe_float(val) -> Optional[float]:
    if _is_blank(val):
        return None
    try:
        f = float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
    return None if f != f else f   # NaN


def _safe_int(val) -> Optional[int]:
    f = _safe_float(val)
    return None if f is None else int(f)


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

    files = _job_files(conn, job)
    missing = [f.path for f in files if not f.path or not Path(f.path).exists()]
    if missing:
        raise FileNotFoundError(f"File not found: {', '.join(str(m) for m in missing)}")

    # Mark job as processing
    conn.execute(
        "UPDATE revenue_job SET status = ? WHERE id = ?",
        (STATUS_PROCESSING, job_id),
    )
    conn.commit()

    try:
        if supplier.get("job_type") == JOB_TYPE_PIVOT_ALLOCATION:
            if len(files) > 1:
                raise ValueError("Aggregate/pivot (Tier C) suppliers take one file per job.")
            return _run_pivot_allocation_pipeline(conn, job, supplier, files[0].path, manual_payment_id)
        return _run_pipeline(conn, job, supplier, files, manual_payment_id)
    except Exception as e:
        conn.rollback()      # drop rows inserted before the failure
        conn.execute(
            "UPDATE revenue_job SET status = ?, error_message = ? WHERE id = ?",
            (STATUS_FAILED, str(e)[:2000], job_id),
        )
        _job_log(conn, job_id, EXC_PROCESSING_ERROR, str(e), "error")
        conn.commit()
        raise


def _job_files(conn: object, job: dict) -> list[SourceFile]:
    """The job's source files in upload order. Jobs created before multi-file upload have only revenue_job.file_path."""
    rows = conn.execute(
        "SELECT original_file_name, file_path FROM revenue_job_file WHERE job_id = ? ORDER BY id", (job["id"],)
    ).fetchall()
    if rows:
        return [SourceFile(r["original_file_name"], r["file_path"]) for r in rows]
    path = job.get("file_path")
    return [SourceFile(job.get("original_file_name") or Path(path or "").name, path)]


def _log_duplicate_files(conn: object, job_id: int) -> None:
    rows = conn.execute(
        "SELECT original_file_name, sha256 FROM revenue_job_file WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    first_by_hash: dict[str, str] = {}
    for r in rows:
        if r["sha256"] in first_by_hash:
            _job_log(
                conn, job_id, EXC_DUPLICATE_FILE,
                f"{r['original_file_name']} is byte-for-byte identical to {first_by_hash[r['sha256']]}. "
                f"Both were processed; every row in it is reported as a duplicate.",
                "warning",
            )
        else:
            first_by_hash[r["sha256"]] = r["original_file_name"]


class _InputCounter:
    """Input totals, added up file by file (InputTotals once every file is read)."""

    _DETAIL_LIMIT = 1000

    def __init__(self) -> None:
        self.t = InputTotals(rows=0, volume=0.0, revenue=0.0, rows_per_file={}, dropped_detail=[])

    def add(self, name: str, df: pd.DataFrame, dropped: pd.DataFrame) -> None:
        t = self.t
        volume, bad_volume = amount_totals(df, CANONICAL_SALES_AMOUNT)
        revenue, bad_revenue = amount_totals(df, CANONICAL_FEE_AMOUNT)
        t.rows += len(df)
        t.volume += volume
        t.revenue += revenue
        t.unparseable_volume += bad_volume
        t.unparseable_revenue += bad_revenue
        t.rows_per_file[name] = t.rows_per_file.get(name, 0) + len(df)
        if dropped.empty:
            return
        t.dropped_rows += len(dropped)
        t.dropped_volume += amount_totals(dropped, CANONICAL_SALES_AMOUNT)[0]
        t.dropped_revenue += amount_totals(dropped, CANONICAL_FEE_AMOUNT)[0]
        room = self._DETAIL_LIMIT - len(t.dropped_detail)
        t.dropped_detail.extend(
            {"source_file": r.get(SOURCE_FILE_COL), "source_row": _safe_int(r.get(SOURCE_ROW_COL)),
             "volume": _safe_float(r.get(CANONICAL_SALES_AMOUNT)), "revenue": _safe_float(r.get(CANONICAL_FEE_AMOUNT))}
            for r in dropped.head(max(room, 0)).to_dict("records")
        )


_INSERT_ROW_SQL = """
    INSERT INTO revenue_row (
        job_id, row_number, raw_row_json,
        jde_number, customer_name, process_date,
        city, state, zip, address,
        sales_amount, fee_amount, product_line, contract_number,
        matched_member_id, match_method, match_score, match_geo_filter,
        entity_code, account_id, account_name_db,
        match_status, validation_status,
        source_file, source_row, file_entity_code, exception_reason, duplicate_group
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_INSERT_BATCH = 5000
_RECORD_CHUNK = 50_000


def _iter_records(df: pd.DataFrame):
    """Rows as dicts, converted a slice at a time: the whole frame as dicts costs several times the frame."""
    for start in range(0, len(df), _RECORD_CHUNK):
        yield from df.iloc[start:start + _RECORD_CHUNK].to_dict("records")


def _run_pipeline(
    conn: object,
    job: dict,
    supplier: dict,
    files: list[SourceFile],
    manual_payment_id: Optional[str],
) -> dict:
    job_id = job["id"]
    supplier_id = supplier["id"]
    gpo_key_mode = supplier.get("identity_mode") == IDENTITY_GPO_KEY

    _log_duplicate_files(conn, job_id)
    if not (job.get("timeframe") or "").strip():
        _job_log(conn, job_id, EXC_TIMEFRAME_MISSING,
                 "No timeframe was entered for this run, so 'Pmt For (Time Frame)' is blank.", "warning")

    # ------------------------------------------------------------------
    # Step 1: Resolve Payment ID
    # ------------------------------------------------------------------
    payment_id, deposit_amount, pid_source = resolve_payment_id(
        conn=conn,
        supplier_profile_id=supplier_id,
        filename=files[0].name,
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
    # Step 2: Identity: GPO key verification, or MemberMatcher
    # ------------------------------------------------------------------
    matcher = None
    gpo_key = None
    members_by_code: dict[str, dict] = {}
    if gpo_key_mode:
        gpo_key = GpoKey.from_db(conn, supplier_id)
        if not len(gpo_key):
            _job_log(
                conn, job_id, EXC_KEY_VERIFICATION,
                f"No GPO key is loaded for {supplier['name']}, so no row could be verified. "
                f"Load the key (Admin > Suppliers) and upload the file again.",
                "error",
            )
        # Only to fill in the Salesforce Account ID / name of a verified GPO ID; never for identity.
        for m in conn.execute(
            "SELECT id, entity_code, account_id, name FROM member WHERE is_active = 1 AND entity_code IS NOT NULL"
        ).fetchall():
            members_by_code.setdefault(str(m["entity_code"]).strip().upper(), dict(m))
    else:
        matcher = MemberMatcher(
            conn=conn,
            supplier_profile_id=supplier_id,
            zip_state_threshold=supplier.get("fuzzy_zip_state_threshold", 75),
            city_state_threshold=supplier.get("fuzzy_city_state_threshold", 75),
            state_threshold=supplier.get("fuzzy_state_threshold", 80),
            nationwide_threshold=supplier.get("fuzzy_nationwide_threshold", 85),
        )
        matcher.load()

    # Cache: jde_or_name → MatchResult / (account, name, address, zip) → Verification
    match_cache: dict[object, object] = {}
    jde_col: Optional[str] = None      # set per file

    def get_cache_key(row) -> str:
        if jde_col and pd.notna(row.get(jde_col)):
            return f"jde:{row[jde_col]}"
        aid = row.get(CANONICAL_ACCOUNT_ID_HINT)
        if pd.notna(aid) and str(aid).strip():
            return f"aid:{aid}"
        ec = row.get(CANONICAL_ENTITY_CODE_HINT)
        if pd.notna(ec) and str(ec).strip():
            # Some supplier files (e.g. Bon Secours) reuse the same entity
            # code across multiple distinct facility names, so the hint
            # alone is not a safe cache key on its own — fold in name/state
            # too, otherwise the first row's (possibly unmatched) fuzzy
            # result gets wrongly reused for every other name sharing the code.
            name = row.get(CANONICAL_CUSTOMER_NAME, "")
            state = row.get(CANONICAL_STATE, "")
            return f"ec:{ec}|{name}|{state}"
        return f"name:{row.get(CANONICAL_CUSTOMER_NAME, '')}"

    # ------------------------------------------------------------------
    # Step 3: Read the files one at a time; process each row (inserted in
    # batches). Nothing is committed until every file is in, so a file
    # that fails leaves no partial job behind.
    # ------------------------------------------------------------------
    matched_count = 0
    unmatched_count = 0
    key_exception_count = 0
    corrected_gpo_ids: dict[tuple[str, str], int] = {}
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    batch: list[tuple] = []
    counter = _InputCounter()
    duplicates = DuplicateFinder()
    col_mapping: dict[str, str] = {}
    filename_date = parse_date_from_filename(files[0].name)
    # parse_date is slow per call (pandas) and a month has few distinct dates.
    date_cache: dict[str, Optional[str]] = {}

    def row_date(value) -> Optional[str]:
        if _is_blank(value):
            return filename_date
        key = str(value)
        if key not in date_cache:
            date_cache[key] = parse_date(value)
        return date_cache[key] or filename_date

    def flush() -> None:
        if batch:
            conn.executemany(_INSERT_ROW_SQL, batch)
            batch.clear()

    row_num = 0
    for file_index, (source, result) in enumerate(read_files(files, supplier, conn)):
        df = result.df
        prefix = f"{source.name}: " if len(files) > 1 else ""
        for w in result.warnings:
            _job_log(conn, job_id, "file_warning", prefix + w, "info")
        counter.add(source.name, df, result.dropped)
        duplicates.add(df, file_index)
        raw_cols = content_columns(df)
        col_mapping.update((c, c) for c in raw_cols if c != SOURCE_ROW_COL)
        jde_col = CANONICAL_JDE_NUMBER if CANONICAL_JDE_NUMBER in df.columns else None

        for row in _iter_records(df):
            row_num += 1
            # Parse canonical values
            jde_num   = _text(row.get(CANONICAL_JDE_NUMBER)) if jde_col else None
            cust_name = _text(row.get(CANONICAL_CUSTOMER_NAME))
            entity_code_hint = _text(row.get(CANONICAL_ENTITY_CODE_HINT)) or None
            account_id_hint  = _text(row.get(CANONICAL_ACCOUNT_ID_HINT)) or None
            if not cust_name:
                # Fall back to the identity hint for display purposes (review UI, logs)
                cust_name = entity_code_hint or account_id_hint or ""
            proc_date = row_date(row.get(CANONICAL_PROCESS_DATE))
            city      = _text(row.get(CANONICAL_CITY))
            state     = _text(row.get(CANONICAL_STATE))
            zip_code  = _text(row.get(CANONICAL_ZIP))
            address   = _text(row.get(CANONICAL_ADDRESS))
            sales_amt = _safe_float(row.get(CANONICAL_SALES_AMOUNT))
            fee_amt   = _safe_float(row.get(CANONICAL_FEE_AMOUNT))
            product   = _text(row.get(CANONICAL_PRODUCT_LINE))
            contract  = _text(row.get(CANONICAL_CONTRACT_NUMBER))

            if proc_date:
                if date_start is None or proc_date < date_start:
                    date_start = proc_date
                if date_end is None or proc_date > date_end:
                    date_end = proc_date

            exception_reason = None
            if gpo_key_mode:
                vkey = (jde_num, cust_name, address, zip_code)
                verification = match_cache.get(vkey)
                if verification is None:
                    verification = gpo_key.verify(jde_num, cust_name, address, zip_code)
                    match_cache[vkey] = verification
                if verification.verified:
                    gpo_id = verification.gpo_id
                    member = members_by_code.get(gpo_id.strip().upper(), {})
                    ident = (member.get("id"), MATCH_GPO_KEY, 100, None, gpo_id, member.get("account_id"),
                             member.get("name"))
                    row_status, validation = ROW_MATCHED, "valid"
                    matched_count += 1
                    if entity_code_hint and entity_code_hint.strip().upper() != gpo_id.strip().upper():
                        pair = (entity_code_hint, gpo_id)
                        corrected_gpo_ids[pair] = corrected_gpo_ids.get(pair, 0) + 1
                else:
                    ident = (None, MATCH_UNMATCHED, None, None, None, None, None)
                    row_status, validation = ROW_KEY_EXCEPTION, "invalid"
                    exception_reason = verification.reason
                    key_exception_count += 1
            else:
                # Run match (from cache if same JDE/name already matched)
                cache_key = get_cache_key(row)
                match = match_cache.get(cache_key)
                if match is None:
                    match = matcher.match(
                        customer_name=cust_name,
                        jde_number=jde_num,
                        city=city,
                        state=state,
                        zip_code=zip_code,
                        entity_code_hint=entity_code_hint,
                        account_id_hint=account_id_hint,
                    )
                    match_cache[cache_key] = match
                    # Persist JDE history for successful matches
                    if match.matched and jde_num:
                        matcher.record_jde_match(jde_num, match.member_id, job_id)
                ident = (match.member_id, match.match_method, match.match_score, match.geo_filter,
                         match.entity_code, match.account_id, match.account_name)
                if match.matched:
                    row_status = ROW_ALIAS_MATCHED if match.match_method == MATCH_ALIAS else ROW_MATCHED
                    validation = "valid"
                    matched_count += 1
                else:
                    row_status, validation = ROW_UNMATCHED, "pending"
                    unmatched_count += 1

            # Persist raw row as JSON (all original columns)
            raw_json = json.dumps(
                {c: (None if _is_blank(row.get(c)) else str(row.get(c))) for c in raw_cols},
                default=str,
            )
            batch.append((
                job_id, row_num, raw_json,
                jde_num or None, cust_name or None, proc_date,
                city or None, state or None, zip_code or None, address or None,
                sales_amt, fee_amt, product or None, contract or None,
                *ident,
                row_status, validation,
                row.get(SOURCE_FILE_COL), _safe_int(row.get(SOURCE_ROW_COL)), entity_code_hint,
                exception_reason, None,      # duplicate_group: set below, once every file is read
            ))
            if len(batch) >= _INSERT_BATCH:
                flush()
        del df, result

    flush()
    input_totals = counter.t
    dup = duplicates.result()
    if dup.row_count:
        conn.executemany(
            "UPDATE revenue_row SET duplicate_group = ? WHERE job_id = ? AND row_number = ?",
            [(int(g), job_id, i + 1) for i, g in enumerate(dup.groups) if g],
        )
    for name, n in input_totals.rows_per_file.items():
        conn.execute("UPDATE revenue_job_file SET row_count = ? WHERE job_id = ? AND original_file_name = ?",
                     (n, job_id, name))
    # Store confirmed column mapping in job record
    conn.execute(
        "UPDATE revenue_job SET column_mapping_json = ?, row_count = ? WHERE id = ?",
        (json.dumps(col_mapping), input_totals.rows, job_id),
    )
    conn.commit()

    if input_totals.dropped_rows:
        _job_log(
            conn, job_id, EXC_DROPPED_ROWS,
            f"{input_totals.dropped_rows} summary/total row(s) left out of the input "
            f"(blank '{supplier.get('skip_rows_where_col_null')}'): volume {input_totals.dropped_volume:,.2f}, "
            f"revenue {input_totals.dropped_revenue:,.2f}. Listed on the exceptions report.",
            "info",
        )
    for label, n in (("volume", input_totals.unparseable_volume), ("revenue", input_totals.unparseable_revenue)):
        if n:
            _job_log(conn, job_id, "file_warning",
                     f"{n} row(s) have a {label} value that isn't a number; it counts as 0.", "warning")
    if dup.group_count:
        _job_log(
            conn, job_id, EXC_DUPLICATE_ROWS,
            f"{dup.row_count} rows fall into {dup.group_count} group(s) of identical rows"
            f" ({dup.cross_file_groups} spanning more than one file). They were kept and are counted;"
            f" check the Duplicates tab of the exceptions report.",
            "warning" if dup.cross_file_groups else "info",
        )

    if gpo_key_mode:
        _log_key_results(conn, job_id, key_exception_count, corrected_gpo_ids)

    # ------------------------------------------------------------------
    # Step 6: Build review items for unmatched customers
    # ------------------------------------------------------------------
    if matcher is not None:
        _build_review_items(conn, job_id, supplier_id, matcher)

    # ------------------------------------------------------------------
    # Step 7: Aggregate matched rows → revenue_output_row
    # ------------------------------------------------------------------
    total_revenue, total_volume = _aggregate_output(conn, job_id, job, supplier, payment_id)

    # ------------------------------------------------------------------
    # Step 8: Reconciliation checks
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
    reconciliation = _check_totals(conn, job_id, supplier, input_totals)

    # ------------------------------------------------------------------
    # Step 9: Finalize job status
    # ------------------------------------------------------------------
    final_status = (
        STATUS_NEEDS_REVIEW
        if unmatched_count or key_exception_count or dup.cross_file_groups or not reconciliation.balanced
        else STATUS_COMPLETED
    )

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
        "row_count":        input_totals.rows,
        "matched_count":    matched_count,
        "unmatched_count":  unmatched_count,
        "key_exception_count": key_exception_count,
        "duplicate_rows":   dup.row_count,
        "cross_file_duplicate_groups": dup.cross_file_groups,
        "total_volume":     total_volume,
        "total_revenue":    total_revenue,
        "deposit_amount":   deposit_amount,
        "payment_id":       payment_id,
        "payment_id_source": pid_source,
        "reconciliation":   reconciliation,
    }


def _log_key_results(
    conn: object, job_id: int, exception_count: int, corrected: dict[tuple[str, str], int],
) -> None:
    if exception_count:
        rows = conn.execute(
            """
            SELECT exception_reason, COUNT(*) AS n, SUM(sales_amount) AS vol, SUM(fee_amount) AS rev
            FROM revenue_row WHERE job_id = ? AND match_status = ?
            GROUP BY exception_reason ORDER BY exception_reason
            """,
            (job_id, ROW_KEY_EXCEPTION),
        ).fetchall()
        detail = "; ".join(
            f"{r['exception_reason']}: {r['n']} row(s), volume {r['vol'] or 0:,.2f}, revenue {r['rev'] or 0:,.2f}"
            for r in rows
        )
        _job_log(
            conn, job_id, EXC_KEY_VERIFICATION,
            f"{exception_count} row(s) could not be verified against the GPO key and are not in the output. "
            f"They are on the exceptions report. {detail}.",
            "warning",
        )
    if corrected:
        n = sum(corrected.values())
        pairs = ", ".join(f"{old} -> {new}" for (old, new) in sorted(corrected)[:20])
        more = "" if len(corrected) <= 20 else f" and {len(corrected) - 20} more"
        _job_log(conn, job_id, EXC_GPO_ID_CORRECTED,
                 f"{n} row(s) had a GPO ID in the file that differs from the key and now use the key's: "
                 f"{pairs}{more}.", "info")
    conn.commit()


def _check_totals(conn: object, job_id: int, supplier: dict, input_totals: InputTotals) -> Reconciliation:
    """Run the input = output + exceptions check, store it on the job, and log the result."""
    result = reconcile(conn, job_id, supplier.get("volume_mode"), input_totals)
    conn.execute("UPDATE revenue_job SET reconciliation_json = ? WHERE id = ?", (result.to_json(), job_id))
    _job_log(
        conn, job_id, EXC_RECONCILIATION,
        ("Totals reconcile: " if result.balanced else "Totals DO NOT reconcile: ") + result.summary() + ".",
        "info" if result.balanced else "error",
    )
    conn.commit()
    return result


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
    manual_totals = json.loads(job.get("manual_totals_json") or "{}")
    extraction = extract_totals(file_path, supplier_id, conn, manual_totals)
    for name, amount in manual_totals.items():
        if name in extraction.values:
            _job_log(
                conn, job_id, EXC_MANUAL_TOTAL,
                f"Extraction rule {name!r} is manual_entry; used the total entered at upload: {amount:,.2f}.",
                "info",
            )
    for w in extraction.warnings:
        _job_log(conn, job_id, EXC_EXTRACTION_NOT_FOUND, w, "error")

    if extraction.manual_entry_rules:
        for name in extraction.manual_entry_rules:
            _job_log(
                conn, job_id, EXC_EXTRACTION_NOT_FOUND,
                f"Extraction rule {name!r} is configured as manual_entry — "
                f"no total was entered at upload, so it was not allocated. Upload the file again "
                f"with the confirmed total.",
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
    # Input here is the allocated rows themselves; this guards the aggregation step.
    reconciliation = _check_totals(conn, job_id, supplier, input_totals_from_rows(conn, job_id))

    # ------------------------------------------------------------------
    # Step 7: Finalize job status
    # ------------------------------------------------------------------
    has_open_exceptions = bool(extraction.warnings or extraction.manual_entry_rules or alloc_warnings)
    needs_confirmation = bool(supplier.get("requires_manual_confirmation"))
    final_status = (STATUS_NEEDS_REVIEW if (has_open_exceptions or needs_confirmation or not reconciliation.balanced)
                    else STATUS_COMPLETED)

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
    Groups by (entity_code, earned_period): one row per PEC per earned month,
    however many invoice dates the file has (FINDINGS F17). Facility, member
    and contract stay in the key so distinct ones are never merged.
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

    # Roll the per-date sums up to earned months. Done here rather than in SQL
    # because month truncation differs between SQLite and SQL Server.
    groups: dict[tuple, dict] = {}
    for r in rows:
        process_date = str(r["process_date"]) if r["process_date"] else None
        key = (r["entity_code"], r["account_id"], r["matched_member_id"],
               first_of_month(process_date), r["contract_number"])
        g = groups.setdefault(key, {
            "entity_code": r["entity_code"], "account_id": r["account_id"],
            "matched_member_id": r["matched_member_id"], "contract_number": r["contract_number"],
            "process_date": process_date, "volume": 0.0, "total_revenue": 0.0,
        })
        g["volume"] += r["volume"] or 0.0
        g["total_revenue"] += r["total_revenue"] or 0.0

    default_contract_id = job.get("contract_id") or supplier.get("contract_id")
    volume_blank = supplier.get("volume_mode") == VOLUME_BLANK
    revenue_type = supplier.get("revenue_type", "Admin Fee")
    keep_return  = supplier.get("keep_return_default", "Keep")
    date_received = job.get("date_received")

    total_revenue = 0.0
    total_volume: Optional[float] = None if volume_blank else 0.0

    for r in groups.values():
        earned_period = first_of_month(r["process_date"])
        quarter_label = get_quarter_label(r["process_date"])
        rev = r["total_revenue"] or 0.0
        vol = r["volume"] or 0.0
        total_revenue += rev
        if volume_blank:
            vol = None
        else:
            total_volume += vol
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


def _has_flagged_rows(conn: object, job_id: int) -> bool:
    """Rows not verified against the GPO key, or identical rows found in more than one file."""
    if conn.execute(
        "SELECT COUNT(*) FROM revenue_row WHERE job_id = ? AND match_status = ?", (job_id, ROW_KEY_EXCEPTION),
    ).fetchone()[0]:
        return True
    cross_file = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT duplicate_group FROM revenue_row
            WHERE job_id = ? AND duplicate_group IS NOT NULL
            GROUP BY duplicate_group HAVING COUNT(DISTINCT source_file) > 1
        ) g
        """,
        (job_id,),
    ).fetchone()[0]
    return bool(cross_file)


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
    input_totals = (input_totals_from_json(dict(job_row).get("reconciliation_json"))
                    or input_totals_from_rows(conn, job_id))
    reconciliation = _check_totals(conn, job_id, dict(supplier_row), input_totals)

    # Update job stats. Anything else that flagged the job (rows not verified
    # against the GPO key, duplicates across files) still needs a sign-off.
    still_unmatched = conn.execute(
        "SELECT COUNT(*) FROM revenue_row WHERE job_id = ? AND match_status = 'unmatched'",
        (job_id,),
    ).fetchone()[0]
    flagged = still_unmatched or not reconciliation.balanced or _has_flagged_rows(conn, job_id)
    new_status = STATUS_NEEDS_REVIEW if flagged else STATUS_COMPLETED
    conn.execute(
        "UPDATE revenue_job SET status = ?, unmatched_count = ?, total_revenue = ?, total_volume = ? WHERE id = ?",
        (new_status, still_unmatched, total_revenue, total_volume, job_id),
    )
    conn.commit()

    return updated_rows
