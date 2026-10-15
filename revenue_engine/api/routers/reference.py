"""Health checks, option metadata, dashboard stats."""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.misc import get_dashboard_stats, get_meta
from engine.db import get_connection
from models.misc import DashboardStats, Meta

from ..deps import Conn
from ..errors import problem_response, problems

log = logging.getLogger("api.health")

router = APIRouter(tags=["reference"])


class HealthStatus(BaseModel):
    status: str


@router.get("/health", response_model=HealthStatus, summary="Liveness probe (no dependencies)")
def health() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get(
    "/health/ready", response_model=HealthStatus, summary="Readiness probe (checks the database)",
    responses=problems(503),
)
def ready(request: Request) -> HealthStatus | JSONResponse:
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception:
        log.exception("readiness check failed")
        return problem_response(503, "The database is unreachable.", request)
    return HealthStatus(status="ok")


@router.get("/meta", response_model=Meta, summary="Option lists for form selects")
def meta(response: Response) -> Meta:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return get_meta()


@router.get("/dashboard", response_model=DashboardStats, summary="Home page counts")
def dashboard(conn: Conn, response: Response) -> DashboardStats:
    response.headers["Cache-Control"] = "no-store"
    return get_dashboard_stats(conn)
