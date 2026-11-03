# Deployment

The app (`revenue_engine/`) ships as **one container image on Azure Container Apps**, as before. What runs inside the image changed. FastAPI now serves both the REST API (`/api/*`) and the built React app on the same port (8501) and origin. The Streamlit UI is not in the image.

## Why a single container

- Production is already one container: one replica (`minReplicas = maxReplicas = 1`), 1 vCPU / 2 GiB, one ingress on port 8501. Nothing needs to scale or deploy separately.
- Same origin means no CORS, no second ingress or route, and no API URL baked into the frontend.
- Background work (processing jobs, imports) runs in-process and keeps its status in `revenue_job` and memory. That already assumes one process, so a separate API container would gain nothing.
- Pipeline changes stay minimal: the same `az acr build`, Bicep template, image name, port and Container App.

Two containers (an nginx frontend plus the API) would add routing, CORS and a second image to the pipeline, with no current reason to.

## What's in the image (`revenue_engine/Dockerfile`)

| Stage | Base (pinned) | Does |
|---|---|---|
| `frontend` | `node:22.22.2-trixie-slim` | `npm ci` (cached unless `package*.json` change), then `npm run build`, which runs the TypeScript typecheck and the Vite build |
| `runtime` | `python:3.12.15-slim-trixie` | ODBC Driver 18 for SQL Server, `pip install -r requirements.txt`, app code, `frontend/dist` from stage 1. Runs as non-root user `app` (uid 10001). |

- **Start command:** `uvicorn api.main:app --host 0.0.0.0 --port 8501 --workers 1 --proxy-headers --forwarded-allow-ips * --no-access-log`.
  - It must stay at **one worker and one replica**: the background runner and import-task status are in-process.
- **Port:** 8501, unchanged, so the ingress `targetPort` needs no change.
- **Health:**
  - `GET /api/health` (liveness, no dependencies).
  - `GET /api/health/ready` (checks the database, 503 if unreachable).
  - The Streamlit path `GET /_stcore/health` still answers `ok`.
  - The Docker `HEALTHCHECK` probes `/api/health` with Python, since curl isn't in the image (fixes F4).
- **`.dockerignore`** excludes:
  - local data, `*.db` and `.env*` (fixes F5);
  - `frontend/node_modules` and `frontend/dist`;
  - tests (except `tests/fixtures.py`, used by `scripts/check_mssql.py`), infra and docs.
- **Python dependencies:** `requirements.txt` is the runtime set. `requirements-dev.txt` adds the test tools. Streamlit is no longer a dependency; the old UI was removed from the repo.

Verified locally (`make docker-run`):
- `/` and a nested deep link (`/admin/tier-c?supplier=3`) return the SPA, and the SPA renders when loaded at that nested route.
- `/api/meta` answers. An unknown `/api/*` path returns problem+json with 404.
- `/api/health`, `/api/health/ready` and `/_stcore/health` all return 200.
- Hashed assets are served `immutable`, and the container reports `healthy`.
- It runs as uid 10001, and `ui/` isn't in the image.
- `make check-mssql` passes against SQL Server 2022 with this image.

## Running locally

| What | Command | URL |
|---|---|---|
| API with auto-reload | `make api` | http://localhost:8000/api/docs |
| UI dev server (proxies `/api` to :8000) | `make web` | http://localhost:5173 |
| Both, in Docker, with hot reload | `docker compose up` | :5173 (UI), :8000 (API) |
| Production image | `make docker-run` or `docker compose --profile prod up --build app` | http://localhost:8501 |
| All checks (the deploy gate) | `make check` | — |
| Azure SQL path, against throwaway SQL Server | `make check-mssql` | — |

Everything local shares `./data` (SQLite DB plus uploads) unless `AZURE_SQL_CONNECTION_STRING` is set.

## The pipeline (`infra/deploy.sh`, run by hand with `make deploy`)

There is still no CI service (decision: keep `deploy.sh`). The steps are unchanged except for three additions:

0. **New:** run `make check` (pytest, OpenAPI snapshot, frontend `npm run ci`: types, typecheck, lint, format, tests, build). Skip with `SKIP_CHECKS=1`.
1. Resource group (unchanged).
2. ACR (unchanged). **New:** the first time it runs, it copies the current `revenue-engine:latest`, the last Streamlit image, to `revenue-engine:streamlit-legacy` so it remains a rollback target.
3. `az acr build`, now tagged **`:latest` and `:<git sha>`**. `:latest` is still what the Container App runs, as before (fixes F6: there's now a versioned tag to roll back to).
4. Bicep (unchanged template). `allowedIpRanges` is now passed from `ALLOWED_IP_RANGES`; `deploy.sh` previously couldn't set it.
5. Schema init and seed (unchanged commands; they work in the new image).
6. Summary, now including the image tag and the rollback command.

**New guard (F14):** the script refuses to deploy with public ingress unless `ALLOWED_IP_RANGES` is set or `ALLOW_PUBLIC_INGRESS=true`. The image now serves a scriptable REST API, which is a bigger exposure than the Streamlit UI, and there is no authentication yet.

The full diff is in the commit `ci: build the React app into the image and harden deploy.sh`. **It has not been run against Azure.**

## Environment variables

**Build-time vs runtime:** Vite inlines `VITE_*` values into the JavaScript at build time, so they are public and fixed per image. The app needs none. The browser calls the API on the same origin, so **one image works in every environment**.

| Variable | Where it's set | When | Used by | Notes |
|---|---|---|---|---|
| `AZURE_SQL_CONNECTION_STRING` | Container App secret `sql-conn` → env (Bicep) | runtime | `engine/db.py` | **Unchanged.** Contains the SQL admin password (F14). Unset means SQLite. |
| `REVENUE_DB_PATH` | compose / local shell | runtime | `engine/db.py` | **Unchanged.** SQLite file when not on Azure SQL. Default `data/revenue.db`. |
| `REVENUE_UPLOAD_DIR` | optional | runtime | `core/jobs.py` | New. Where uploaded supplier files are kept. Default `data/uploads` (ephemeral in the container, as `/tmp` was; see F7). |
| `MAX_UPLOAD_MB` | optional | runtime | `api/settings.py` | New. Default 200, Streamlit's per-file limit. |
| `CORS_ALLOW_ORIGINS` | optional | runtime | `api/settings.py` | New. Comma-separated origins. Empty (default) = same-origin only. |
| `LOG_LEVEL` | optional | runtime | `api/settings.py` | New. Default `INFO`. Logs go to stdout and then to Log Analytics, as before. |
| `STATIC_DIR` | Dockerfile (`/app/frontend/dist`) | runtime | `api/spa.py` | New. Built SPA to serve. Empty = API only. |
| `VITE_API_BASE_URL` | not set | **build-time, public** | frontend | Only for a cross-origin API. Never put a secret here. |
| `API_PROXY_TARGET` | compose / dev shell | dev only | `vite.config.ts` | Where the Vite dev server proxies `/api`. |
| `RESOURCE_GROUP`, `LOCATION`, `NAME_PREFIX`, `ACR_NAME`, `EXISTING_SQL_SERVER`, `EXISTING_ENVIRONMENT`, `SQL_ADMIN_PASSWORD` | operator shell | deploy | `deploy.sh` | **Unchanged.** |
| `ALLOWED_IP_RANGES`, `ALLOW_PUBLIC_INGRESS`, `SKIP_CHECKS` | operator shell | deploy | `deploy.sh` | New (see above). |

The Streamlit image read no other environment variables (its settings were command-line flags). Nothing that was read before has been dropped. **No secret reaches the browser.**

## Azure specifics checked

| Item | Before | Now |
|---|---|---|
| Port | 8501 | 8501 |
| Start command | `streamlit run ui/app.py …` (Dockerfile `CMD`) | `uvicorn api.main:app … --port 8501` (Dockerfile `CMD`). Bicep sets no command. |
| Health probe | Bicep defines none, so the default TCP probe on 8501 is used | Same: the TCP probe still works. *Recommended, not applied:* HTTP liveness on `/api/health` and readiness on `/api/health/ready`. |
| Logs | stdout → Log Analytics | stdout → Log Analytics. One access line per request: `api.access request_id=… method=… path=… status=… duration_ms=…`. |
| Application Insights | not present | not added |
| Scale | 1 replica | 1 replica (required, see above) |

## Rollback

The Container App keeps running whatever image you point it at:

```bash
# Back to the last Streamlit image (tagged automatically on the first new deploy):
az containerapp update -n <prefix>-ui -g <rg> --image <acr>.azurecr.io/revenue-engine:streamlit-legacy

# Back to an earlier build of the new app:
az containerapp update -n <prefix>-ui -g <rg> --image <acr>.azurecr.io/revenue-engine:<git sha>
```

- Both images use the same database schema; this migration made no schema change, so rolling back needs no data step.
- Jobs processed with the new app are readable by the Streamlit image and the other way round.
- If `:streamlit-legacy` is missing (the first deploy ran with an older `deploy.sh`), rebuild it from git: `git checkout <commit before this branch> -- revenue_engine && az acr build … --image revenue-engine:streamlit-legacy`.

## Before the first deploy of the new app

1. Decide on access (F14): Easy Auth (Entra ID) on the Container App, or `ALLOWED_IP_RANGES`.
2. Check production `revenue_job` for `failed` rows. Azure SQL processing was broken before this branch (F16, F18, F19).
3. Run `make check` and `make check-mssql` locally.
4. `make deploy`, then open `/api/health/ready` and the Dashboard, and process one known file end to end.
