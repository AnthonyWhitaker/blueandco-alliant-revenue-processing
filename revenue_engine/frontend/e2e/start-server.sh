#!/usr/bin/env bash
# Started by Playwright (playwright.config.ts): a fresh synthetic database and
# the production-style server (FastAPI serving frontend/dist) on $E2E_PORT.
set -euo pipefail
cd "$(dirname "$0")/../.."   # revenue_engine/

DATA_DIR="frontend/e2e/.data"
rm -rf "$DATA_DIR" && mkdir -p "$DATA_DIR/uploads"
PYTHON="${PYTHON:-.venv/bin/python}"

export REVENUE_DB_PATH="$DATA_DIR/revenue.db"
export REVENUE_UPLOAD_DIR="$DATA_DIR/uploads"
export STATIC_DIR="frontend/dist"
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"
unset AZURE_SQL_CONNECTION_STRING

"$PYTHON" scripts/seed_e2e.py "$DATA_DIR"
exec "$PYTHON" -m uvicorn api.main:app --host 127.0.0.1 --port "${E2E_PORT:-8600}" --no-access-log
