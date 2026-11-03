"""Contract tests for the row-level pipeline and review decisions (engine/processor.py)."""

from engine.processor import apply_review_decision


def _pending_item(conn, job_id):
    return dict(conn.execute(
        "SELECT * FROM member_review_item WHERE job_id = ? AND status = 'pending'", (job_id,)
    ).fetchone())


def test_pipeline_matches_and_queues_unmatched(conn, processed_job):
    result = processed_job["result"]
    assert result["status"] == "needs_review"
    assert result["row_count"] == 4
    assert result["matched_count"] == 3
    assert result["unmatched_count"] == 1
    assert result["total_revenue"] == 270.00
    assert result["payment_id"] == "PAY-1"
    assert result["payment_id_source"] == "ledger_auto"

    item = _pending_item(conn, processed_job["job_id"])
    assert item["customer_name_sample"] == "Zyxw Qvbn Clinic"
    assert item["jde_number"] == "J300"
    assert item["total_fee"] == 30.00


def test_pipeline_aggregates_per_pec_and_process_date(conn, processed_job):
    # Current behaviour (FINDINGS F17): grouping is by raw process_date, so two
    # June dates for EC001 produce two output rows in the same earned period.
    rows = conn.execute(
        "SELECT pec, total_revenue, earned_period, payment_id FROM revenue_output_row WHERE job_id = ? "
        "ORDER BY pec, total_revenue",
        (processed_job["job_id"],),
    ).fetchall()
    assert [(r["pec"], r["total_revenue"]) for r in rows] == [("EC001", 50.00), ("EC001", 100.00), ("EC002", 120.00)]
    assert {r["earned_period"] for r in rows} == {"2025-06-01"}
    assert {r["payment_id"] for r in rows} == {"PAY-1"}


def test_approve_completes_job_and_reaggregates(conn, processed_job):
    job_id = processed_job["job_id"]
    item = _pending_item(conn, job_id)
    updated = apply_review_decision(
        conn=conn, review_item_id=item["id"], resolved_member_id=processed_job["members"]["desert"],
        resolution_scope="supplier", resolved_by="user", create_alias=True,
        supplier_profile_id=processed_job["supplier_id"],
    )
    assert updated == 1
    job = dict(conn.execute("SELECT * FROM revenue_job WHERE id = ?", (job_id,)).fetchone())
    assert job["status"] == "completed"
    assert job["unmatched_count"] == 0
    assert job["total_revenue"] == 300.00


def test_supplier_scope_alias_is_supplier_specific(conn, processed_job):
    item = _pending_item(conn, processed_job["job_id"])
    apply_review_decision(
        conn=conn, review_item_id=item["id"], resolved_member_id=processed_job["members"]["desert"],
        resolution_scope="supplier", resolved_by="user", create_alias=True,
        supplier_profile_id=processed_job["supplier_id"],
    )
    alias = conn.execute("SELECT supplier_profile_id FROM member_alias WHERE raw_name = 'Zyxw Qvbn Clinic'").fetchone()
    assert alias["supplier_profile_id"] == processed_job["supplier_id"]


def test_global_scope_alias_has_no_supplier(conn, processed_job):
    item = _pending_item(conn, processed_job["job_id"])
    apply_review_decision(
        conn=conn, review_item_id=item["id"], resolved_member_id=processed_job["members"]["desert"],
        resolution_scope="global", resolved_by="user", create_alias=True,
        supplier_profile_id=processed_job["supplier_id"],
    )
    alias = conn.execute("SELECT supplier_profile_id FROM member_alias WHERE raw_name = 'Zyxw Qvbn Clinic'").fetchone()
    assert alias["supplier_profile_id"] is None


def test_job_only_scope_leaves_no_lasting_mapping(conn, processed_job):
    """FINDINGS F1/F2: job_only must not create an alias or JDE history."""
    item = _pending_item(conn, processed_job["job_id"])
    apply_review_decision(
        conn=conn, review_item_id=item["id"], resolved_member_id=processed_job["members"]["desert"],
        resolution_scope="job_only", resolved_by="user", create_alias=True,
        supplier_profile_id=processed_job["supplier_id"],
    )
    assert conn.execute("SELECT COUNT(*) FROM member_alias").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM jde_member_history WHERE jde_number = 'J300'"
    ).fetchone()[0] == 0
    # The decision itself still applies to this job.
    row = conn.execute(
        "SELECT match_status FROM revenue_row WHERE job_id = ? AND jde_number = 'J300'", (processed_job["job_id"],)
    ).fetchone()
    assert row["match_status"] == "matched"


def test_dismiss_marks_item_without_matching(conn, processed_job):
    item = _pending_item(conn, processed_job["job_id"])
    assert apply_review_decision(
        conn=conn, review_item_id=item["id"], resolved_member_id=None,
        resolution_scope="job_only", resolved_by="user",
    ) == 0
    status = conn.execute("SELECT status FROM member_review_item WHERE id = ?", (item["id"],)).fetchone()[0]
    assert status == "dismissed"
