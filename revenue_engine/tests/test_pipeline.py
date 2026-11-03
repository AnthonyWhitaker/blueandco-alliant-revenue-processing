"""Contract tests for the row-level pipeline and review decisions (engine/processor.py)."""

from engine.processor import apply_review_decision, process_job

from .conftest import FILE_NAME, create_job_row, write_supplier_file


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


def test_pipeline_aggregates_per_pec_and_earned_month(conn, processed_job):
    # FINDINGS F17: EC001 has two June dates; they make one row for June.
    rows = conn.execute(
        "SELECT pec, total_revenue, earned_period, payment_id FROM revenue_output_row WHERE job_id = ? "
        "ORDER BY pec, total_revenue",
        (processed_job["job_id"],),
    ).fetchall()
    assert [(r["pec"], r["total_revenue"]) for r in rows] == [("EC001", 150.00), ("EC002", 120.00)]
    assert {r["earned_period"] for r in rows} == {"2025-06-01"}
    assert {r["payment_id"] for r in rows} == {"PAY-1"}


def test_each_earned_month_gets_its_own_row(conn, seeded, tmp_path):
    rows = [
        ("General Hospital", "J100", "Austin", "TX", "78701", 100.00, 1000.00, "2025-06-15"),
        ("General Hospital", "J100", "Austin", "TX", "78701", 50.00, 500.00, "2025-06-20"),
        ("General Hospital", "J100", "Austin", "TX", "78701", 25.00, 250.00, "2025-07-02"),
    ]
    job_id = create_job_row(conn, seeded["supplier_id"], write_supplier_file(tmp_path / FILE_NAME, rows))
    process_job(conn=conn, job_id=job_id)
    out = conn.execute(
        "SELECT earned_period, quarter_label, volume, total_revenue FROM revenue_output_row "
        "WHERE job_id = ? ORDER BY earned_period", (job_id,),
    ).fetchall()
    assert [tuple(r) for r in out] == [("2025-06-01", "2Q2025", 1500.0, 150.0), ("2025-07-01", "3Q2025", 250.0, 25.0)]


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
