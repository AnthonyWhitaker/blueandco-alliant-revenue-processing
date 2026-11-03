"""Review queue: unmatched customers (member_review_item) and reviewer decisions."""

from engine.constants import REVIEW_PENDING
from engine.processor import apply_review_decision
from models.reviews import ReviewApproval, ReviewDecisionResult, ReviewItem

from .errors import ConflictError, NotFoundError
from .jobs import get_job
from .members import get_member
from .rows import row_dict, rows_dicts


def list_pending_items(conn, job_id: int) -> list[ReviewItem]:
    """Pending items for a job, highest fee impact first (the order reviewers work in)."""
    get_job(conn, job_id)
    rows = rows_dicts(conn.execute(
        """
        SELECT mri.*,
               m.name AS suggested_name, m.entity_code AS suggested_entity_code,
               m.billing_city AS suggested_city, m.billing_state AS suggested_state
        FROM member_review_item mri
        LEFT JOIN member m ON m.id = mri.suggested_member_id
        WHERE mri.job_id = ? AND mri.status = ?
        ORDER BY mri.total_fee DESC, mri.id
        """,
        (job_id, REVIEW_PENDING),
    ).fetchall())
    return [_to_item(r) for r in rows]


def _to_item(r: dict) -> ReviewItem:
    suggestion = None
    if r.get("suggested_member_id") and r.get("suggested_name"):
        suggestion = {
            "member_id": r["suggested_member_id"],
            "name": r["suggested_name"],
            "entity_code": r["suggested_entity_code"],
            "city": r["suggested_city"],
            "state": r["suggested_state"],
            "score": r.get("suggested_match_score") or 0,
        }
    return ReviewItem.model_validate({**r, "suggestion": suggestion})


def _pending_item(conn, item_id: int) -> dict:
    item = row_dict(conn.execute("SELECT * FROM member_review_item WHERE id = ?", (item_id,)).fetchone())
    if not item:
        raise NotFoundError(f"Review item {item_id} not found.")
    if item["status"] != REVIEW_PENDING:
        raise ConflictError(f"Review item {item_id} was already {item['status']}.")
    return item


def approve(conn, item_id: int, decision: ReviewApproval, resolved_by: str = "user") -> ReviewDecisionResult:
    item = _pending_item(conn, item_id)
    get_member(conn, decision.member_id)
    job = get_job(conn, item["job_id"])
    updated = apply_review_decision(
        conn=conn,
        review_item_id=item_id,
        resolved_member_id=decision.member_id,
        resolution_scope=decision.alias_scope,
        resolved_by=resolved_by,
        notes=decision.notes,
        create_alias=decision.create_alias,
        supplier_profile_id=job.supplier_profile_id,
    )
    return ReviewDecisionResult(updated_rows=updated, job=get_job(conn, job.id))


def dismiss(conn, item_id: int, resolved_by: str = "user") -> ReviewDecisionResult:
    item = _pending_item(conn, item_id)
    apply_review_decision(
        conn=conn,
        review_item_id=item_id,
        resolved_member_id=None,
        resolution_scope="job_only",
        resolved_by=resolved_by,
    )
    return ReviewDecisionResult(updated_rows=0, job=get_job(conn, item["job_id"]))
