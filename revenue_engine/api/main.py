"""
FastAPI application for the Alliant Revenue Processing Engine.

Run locally from revenue_engine/:
    uvicorn api.main:app --reload
Interactive docs at /api/docs; schema at /api/openapi.json.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import PlainTextResponse

from core.jobs import fail_interrupted_jobs
from engine.db import init_db

from .background import BackgroundRunner
from .errors import install_error_handlers, use_problem_media_type
from .routers import jobs, members, reference, suppliers
from .settings import load_settings
from .spa import mount_spa

log = logging.getLogger("api")

# Conservative defaults for an internal tool; the SPA needs no framing or cross-origin access.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
access_log = logging.getLogger("api.access")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conn = init_db()
    try:
        interrupted = fail_interrupted_jobs(conn)
        if interrupted:
            log.warning("marked %d interrupted job(s) as failed", interrupted)
    finally:
        conn.close()
    app.state.runner = BackgroundRunner()
    yield
    app.state.runner.shutdown()


def create_app() -> FastAPI:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = FastAPI(
        title="Alliant Revenue Processing API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    install_error_handlers(app)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition", "Location", "X-Request-ID"],
        )

    @app.middleware("http")
    async def request_logging(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        access_log.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.0f",
            request_id, request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    for module in (reference, jobs, suppliers, members):
        app.include_router(module.router, prefix="/api")

    # The Streamlit image's health path, kept so anything already probing it keeps working.
    @app.get("/_stcore/health", include_in_schema=False)
    def legacy_health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    mount_spa(app, settings.static_dir)

    def openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = use_problem_media_type(get_openapi(
                title=app.title, version=app.version, routes=app.routes,
            ))
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's documented override hook
    return app


app = create_app()
