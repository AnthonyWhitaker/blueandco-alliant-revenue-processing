"""FastAPI dependencies: a DB connection per request, the background runner, upload reading."""

import tempfile
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import Depends, HTTPException, Request, UploadFile

from engine.db import get_connection

from .background import BackgroundRunner


def get_conn() -> Iterator[object]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_runner(request: Request) -> BackgroundRunner:
    return request.app.state.runner


Conn = Annotated[object, Depends(get_conn)]
Runner = Annotated[BackgroundRunner, Depends(get_runner)]


def read_upload(request: Request, file: UploadFile) -> bytes:
    """Read an uploaded file, enforcing MAX_UPLOAD_MB."""
    limit: int = request.app.state.settings.max_upload_bytes
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit // (1024 * 1024)} MB upload limit.")
    return content


def upload_to_temp_file(request: Request, file: UploadFile) -> str:
    """Spool an upload to a temp file for engine functions that take a path. Caller deletes it."""
    content = read_upload(request, file)
    suffix = Path(file.filename or "").suffix.lower() or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        return tmp.name
