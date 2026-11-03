"""Serve the built React app (frontend/dist) from the same origin as the API.

Hashed files under /assets are cached for a year; every other GET that isn't
an API route returns index.html (no-cache), so deep links like
/admin/tier-c?supplier=3 work on refresh. Unknown /api/* paths still 404 as
problem+json.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

log = logging.getLogger("api.spa")


class ImmutableStaticFiles(StaticFiles):
    """Vite content-hashes everything in /assets, so a file never changes under a URL."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[no-untyped-def]  # Starlette's signature
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_spa(app: FastAPI, static_dir: Path | None) -> None:
    """Register the SPA routes. Call after every API router."""
    if static_dir is None or not (static_dir / "index.html").is_file():
        log.info("no built frontend at %s; serving the API only", static_dir)
        return
    root = static_dir.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # Top-level files from public/ (favicon.svg); never anything outside dist/.
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    log.info("serving the frontend from %s", root)
