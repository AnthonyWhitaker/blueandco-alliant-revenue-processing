"""Runtime configuration, read from the environment once at startup.

Database selection is not here: engine/db.py reads AZURE_SQL_CONNECTION_STRING /
REVENUE_DB_PATH directly, and core/jobs.py reads REVENUE_UPLOAD_DIR.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cors_allow_origins: list[str]
    max_upload_bytes: int
    log_level: str


def load_settings() -> Settings:
    origins = os.environ.get("CORS_ALLOW_ORIGINS", "")
    return Settings(
        cors_allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
