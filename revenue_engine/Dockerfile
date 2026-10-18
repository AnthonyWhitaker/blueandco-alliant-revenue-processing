# syntax=docker/dockerfile:1
#
# Production image: FastAPI serves the API and the built React app on one
# port (8501, unchanged from the Streamlit image, so ingress needs no change).
# See ../DEPLOYMENT.md.

# --- Stage 1: build the React app -------------------------------------------
FROM node:22.22.2-trixie-slim AS frontend
WORKDIR /frontend
# Dependencies first, so a source change doesn't reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# `build` = TypeScript typecheck (tsc -b) + Vite production build.
RUN npm run build

# --- Stage 2: Python runtime ------------------------------------------------
FROM python:3.12.15-slim-trixie AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/frontend/dist

# msodbcsql18 ODBC driver, required by pyodbc to reach Azure SQL in production.
# The Microsoft repo config package is picked to match the base image's Debian
# release.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unixodbc-dev gcc g++ && \
    curl -fsSL -o /tmp/packages-microsoft-prod.deb \
        "https://packages.microsoft.com/config/debian/$(. /etc/os-release && echo $VERSION_ID)/packages-microsoft-prod.deb" && \
    dpkg -i /tmp/packages-microsoft-prod.deb && \
    rm /tmp/packages-microsoft-prod.deb && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    apt-mark manual libgssapi-krb5-2 && \
    apt-get purge -y --auto-remove curl gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root runtime user (FINDINGS F6). data/ holds the local SQLite DB and
# uploads when AZURE_SQL_CONNECTION_STRING isn't set, so it must be writable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app && \
    mkdir -p /app/data/uploads && chown -R app:app /app/data

COPY --chown=app:app . .
COPY --from=frontend --chown=app:app /frontend/dist ./frontend/dist

USER app

EXPOSE 8501

# curl is purged above, so probe with Python (FINDINGS F4). Container Apps
# ignores Docker HEALTHCHECK; this is for `docker run` / compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/api/health', timeout=4)" || exit 1

# One worker: background jobs and import tasks run in-process (api/background.py),
# and the app is deployed as a single replica. Access logging is the app's own
# (api.access, with request ids), so uvicorn's is off.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8501", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
