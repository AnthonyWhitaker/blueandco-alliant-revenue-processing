"""RFC 7807 problem-details responses for every error the API returns."""

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import ConflictError, CoreError, InvalidInputError, NotFoundError

log = logging.getLogger("api.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"

_CORE_STATUS: dict[type[CoreError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    InvalidInputError: 422,
}
_TITLES = {
    400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
    413: "Payload Too Large", 422: "Unprocessable Content", 500: "Internal Server Error",
    503: "Service Unavailable",
}


class FieldError(BaseModel):
    loc: list[str]
    msg: str
    type: str


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    errors: Optional[list[FieldError]] = None


def problems(*statuses: int) -> dict[int | str, dict]:
    """OpenAPI `responses` entries documenting the problem-details error shape."""
    return {s: {"model": Problem, "description": _TITLES[s]} for s in statuses}


# Router-level default: any route with parameters or a body can fail validation.
PROBLEM_RESPONSES = problems(422)


def problem_response(status: int, detail: Optional[str], request: Request,
                     errors: Optional[list[FieldError]] = None) -> JSONResponse:
    body = Problem(title=_TITLES.get(status, "Error"), status=status, detail=detail,
                   instance=request.url.path, errors=errors)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status, media_type=PROBLEM_MEDIA_TYPE)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CoreError)
    async def _core(request: Request, exc: CoreError) -> JSONResponse:
        status = next((s for cls, s in _CORE_STATUS.items() if isinstance(exc, cls)), 400)
        return problem_response(status, exc.message, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(loc=[str(p) for p in e["loc"]], msg=str(e["msg"]).removeprefix("Value error, "), type=e["type"])
            for e in exc.errors()
        ]
        return problem_response(422, "The request did not pass validation.", request, errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(exc.status_code, str(exc.detail) if exc.detail else None, request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return problem_response(500, "An unexpected error occurred.", request)


def use_problem_media_type(openapi: dict) -> dict:
    """Error responses are served as application/problem+json; make the schema say so."""
    for path in openapi.get("paths", {}).values():
        for op in path.values():
            for status, resp in op.get("responses", {}).items():
                content = resp.get("content", {})
                if status[0] in "45" and "application/json" in content:
                    content[PROBLEM_MEDIA_TYPE] = content.pop("application/json")
    return openapi
