"""Runtime configuration, read from the environment once at startup.

Database selection is not here: engine/db.py reads AZURE_SQL_CONNECTION_STRING /
REVENUE_DB_PATH directly, and core/jobs.py reads REVENUE_UPLOAD_DIR.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    cors_allow_origins: list[str]
    max_upload_bytes: int
    log_level: str
    # Built React app to serve (frontend/dist). None = API only (local dev runs Vite).
    static_dir: Path | None


# MAX_UPLOAD_MB defaults to 200, Streamlit's default per-file limit, so nothing the legacy
# app accepted is rejected.
def load_settings() -> Settings:
    origins = os.environ.get("CORS_ALLOW_ORIGINS", "")
    return Settings(
        cors_allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        static_dir=Path(static) if (static := os.environ.get("STATIC_DIR", "frontend/dist")) else None,
    )
