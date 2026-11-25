"""Review queue: unmatched customers (member_review_item) and reviewer decisions."""

import json

from engine.constants import REVIEW_PENDING
from engine.processor import apply_review_decision
from models.reviews import ReviewApproval, ReviewDecisionResult, ReviewItem, ReviewNotMember

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
    candidates = {r["id"]: json.loads(r["candidates_json"]) if r.get("candidates_json") else [] for r in rows}
    member_ids = sorted({c["member_id"] for cs in candidates.values() for c in cs})
    members = {}
    if member_ids:
        marks = ", ".join("?" for _ in member_ids)
        members = {m["id"]: m for m in rows_dicts(conn.execute(
            f"SELECT id, name, entity_code, billing_street, billing_city, billing_state, billing_zip "
            f"FROM member WHERE id IN ({marks})", member_ids).fetchall())}
    return [_to_item(r, [_candidate(c, members[c["member_id"]]) for c in candidates[r["id"]]
                         if c["member_id"] in members])
            for r in rows]


def _candidate(c: dict, m: dict) -> dict:
    return {"member_id": m["id"], "name": m["name"], "entity_code": m["entity_code"], "street": m["billing_street"],
            "city": m["billing_city"], "state": m["billing_state"], "zip": m["billing_zip"],
            "score": c.get("score") or 0, "matched": c.get("matched") or [], "via": c.get("via") or ""}


def _to_item(r: dict, candidates: list[dict]) -> ReviewItem:
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
    return ReviewItem.model_validate({**r, "suggestion": suggestion, "candidates": candidates})


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


def mark_not_member(conn, item_id: int, decision: ReviewNotMember, resolved_by: str = "user") -> ReviewDecisionResult:
    """The account isn't a member: its rows are set aside, and later files don't send it to review again."""
    item = _pending_item(conn, item_id)
    job = get_job(conn, item["job_id"])
    updated = apply_review_decision(
        conn=conn,
        review_item_id=item_id,
        resolved_member_id=None,
        resolution_scope="supplier",
        resolved_by=resolved_by,
        notes=decision.notes,
        supplier_profile_id=job.supplier_profile_id,
        not_member=True,
    )
    return ReviewDecisionResult(updated_rows=updated, job=get_job(conn, job.id))
