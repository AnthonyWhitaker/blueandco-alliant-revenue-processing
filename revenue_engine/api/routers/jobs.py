"""Revenue jobs, their review queue, and export."""

import json
from datetime import date
from typing import Annotated, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status

from core import jobs, reviews
from core.errors import InvalidInputError
from models.enums import JobStatus
from models.jobs import FileNameInference, Job, JobException, JobPatch, JobPreview
from models.reviews import ReviewApproval, ReviewDecisionResult, ReviewItem

from ..deps import Conn, Runner, read_upload
from ..errors import PROBLEM_RESPONSES, problems

router = APIRouter(tags=["jobs"], responses=PROBLEM_RESPONSES)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/jobs", response_model=list[Job], summary="List jobs, newest first")
def list_jobs(
    conn: Conn,
    status_: Annotated[Optional[list[JobStatus]], Query(alias="status")] = None,
    has_pending_reviews: Optional[bool] = None,
    limit: Annotated[Optional[int], Query(ge=1, le=500)] = None,
) -> list[Job]:
    return jobs.list_jobs(conn, statuses=status_, has_pending_reviews=has_pending_reviews, limit=limit)


@router.post(
    "/jobs", responses=problems(404, 413), response_model=Job, status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a supplier file and start processing",
    description="Returns immediately with the job in 'uploaded' status. Poll GET /jobs/{job_id} "
                "until status is completed, needs_review, or failed.",
)
def create_job(
    request: Request,
    response: Response,
    conn: Conn,
    runner: Runner,
    file: Annotated[UploadFile, File(description="Supplier remittance file (.xlsx, .xls, .xlsm, .csv)")],
    supplier_profile_id: Annotated[int, Form()],
    date_received: Annotated[date, Form()],
    manual_payment_id: Annotated[Optional[str], Form()] = None,
    manual_totals: Annotated[Optional[str], Form(
        description='Tier C only. JSON object of confirmed totals by manual_entry rule name, e.g. '
                    '{"Total Rebate": 1234.5}. Required when the supplier has manual_entry rules.',
    )] = None,
) -> Job:
    content = read_upload(request, file)
    job = jobs.create_job(conn, supplier_profile_id, file.filename or "upload", content, date_received,
                          _parse_manual_totals(manual_totals))
    runner.submit_job(job.id, (manual_payment_id or "").strip() or None)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job


def _parse_manual_totals(raw: Optional[str]) -> Optional[dict[str, float]]:
    if not (raw or "").strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in parsed.items()
    ):
        raise InvalidInputError('manual_totals must be a JSON object of numbers, e.g. {"Total Rebate": 1234.5}.')
    return {k: float(v) for k, v in parsed.items()}


@router.get("/file-name-inference", response_model=FileNameInference,
            summary="Guess supplier, date received and deposit amount from a file name")
def infer_file_name(conn: Conn, file_name: Annotated[str, Query(min_length=1)]) -> FileNameInference:
    return jobs.infer_from_filename(conn, file_name)


@router.get("/jobs/{job_id}", responses=problems(404), response_model=Job)
def get_job(conn: Conn, job_id: int) -> Job:
    return jobs.get_job(conn, job_id)


@router.patch("/jobs/{job_id}", responses=problems(404), response_model=Job, summary="Set the job's Salesforce Payment ID")
def patch_job(conn: Conn, job_id: int, body: JobPatch) -> Job:
    return jobs.set_payment_id(conn, job_id, body.payment_id)


@router.post("/jobs/{job_id}/confirmation", responses=problems(404, 409), response_model=Job,
             summary="Sign off a needs_review job that has no pending review items")
def confirm_job(conn: Conn, job_id: int) -> Job:
    return jobs.confirm_job(conn, job_id)


@router.get("/jobs/{job_id}/exceptions", responses=problems(404), response_model=list[JobException])
def list_exceptions(conn: Conn, job_id: int) -> list[JobException]:
    return jobs.list_exceptions(conn, job_id)


@router.get("/jobs/{job_id}/preview", responses=problems(404), response_model=JobPreview, summary="The four output tabs as rows")
def preview(conn: Conn, job_id: int) -> JobPreview:
    return jobs.get_preview(conn, job_id)


@router.get(
    "/jobs/{job_id}/export", response_class=Response, summary="Download the Salesforce workbook",
    responses={200: {"content": {XLSX: {"schema": {"type": "string", "format": "binary"}}},
                     "description": "4-tab XLSX"}, **problems(404, 409)},
)
def export(conn: Conn, job_id: int) -> Response:
    file_name, content = jobs.build_export(conn, job_id)
    return Response(
        content, media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


@router.get("/jobs/{job_id}/review-items", responses=problems(404), response_model=list[ReviewItem],
            summary="Pending unmatched customers, highest fee first")
def list_review_items(conn: Conn, job_id: int) -> list[ReviewItem]:
    return reviews.list_pending_items(conn, job_id)


@router.post("/review-items/{item_id}/approval", responses=problems(404, 409), response_model=ReviewDecisionResult,
             summary="Match an unmatched customer to a member")
def approve(conn: Conn, item_id: int, body: ReviewApproval) -> ReviewDecisionResult:
    return reviews.approve(conn, item_id, body)


@router.post("/review-items/{item_id}/dismissal", responses=problems(404, 409), response_model=ReviewDecisionResult,
             summary="Leave a customer unmatched (it stays on the Unmatched tab)")
def dismiss(conn: Conn, item_id: int) -> ReviewDecisionResult:
    return reviews.dismiss(conn, item_id)
