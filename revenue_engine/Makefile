.PHONY: install initdb seed ui api web openapi test check docker-build docker-run test-real-data check-mssql compose-up compose-down clean deploy

PYTHON ?= python3
PY     ?= .venv/bin/python
PIP    ?= .venv/bin/pip

install:
	$(PYTHON) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

initdb:
	$(PY) -c "from engine.db import init_db; init_db()"

seed: initdb
	$(PY) scripts/seed_guerbet.py
	$(PY) scripts/seed_tier_ab_suppliers.py

# The legacy Streamlit UI (needs requirements-legacy.txt, included by `make install`).
ui:
	.venv/bin/streamlit run ui/app.py --server.headless true

api:
	.venv/bin/uvicorn api.main:app --reload --port 8000

# Vite dev server on :5173, proxying /api to `make api` on :8000.
web:
	cd frontend && npm run dev

openapi:
	$(PY) scripts/export_openapi.py

test:
	.venv/bin/pytest -q

# The gate infra/deploy.sh runs: backend tests, the OpenAPI snapshot matches the
# code, and the frontend's typecheck, lint, format, tests and build.
check: test
	$(PY) scripts/export_openapi.py >/dev/null
	git diff --exit-code -- openapi.json
	cd frontend && npm ci --no-audit --no-fund && npm run ci

# The production image, locally: http://localhost:8501 (SQLite under ./data).
docker-build:
	docker build -t revenue-engine:local .

docker-run: docker-build
	mkdir -p data
	docker run --rm -p 8501:8501 -v "$(CURDIR)/data:/app/data" revenue-engine:local

# Regression scripts against real supplier files (gitignored; must be present locally).
test-real-data:
	$(PY) scripts/regression_test_guerbet.py
	$(PY) scripts/test_tier_ab_suppliers.py

# Run scripts/check_mssql.py against a throwaway SQL Server 2022 container.
MSSQL_PW := Local-Only-Test-1!
check-mssql:
	docker build -q -t revenue-engine:check .
	docker network create rev-check >/dev/null 2>&1 || true
	docker rm -f rev-check-sql >/dev/null 2>&1 || true
	docker run -d --name rev-check-sql --network rev-check --platform linux/amd64 \
		-e ACCEPT_EULA=Y -e 'MSSQL_SA_PASSWORD=$(MSSQL_PW)' mcr.microsoft.com/mssql/server:2022-latest >/dev/null
	until docker exec rev-check-sql /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P '$(MSSQL_PW)' \
		-Q "IF DB_ID('rev_check') IS NULL CREATE DATABASE rev_check" >/dev/null 2>&1; do sleep 3; done
	docker run --rm --network rev-check -v "$(CURDIR):/app" -w /app \
		-e 'AZURE_SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=tcp:rev-check-sql,1433;Database=rev_check;Uid=sa;Pwd=$(MSSQL_PW);Encrypt=yes;TrustServerCertificate=yes' \
		revenue-engine:check python scripts/check_mssql.py; status=$$?; \
		docker rm -f rev-check-sql >/dev/null; docker network rm rev-check >/dev/null; exit $$status

compose-up:
	docker compose up --build

compose-down:
	docker compose down

clean:
	rm -rf __pycache__ data/revenue.db
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

deploy:
	bash infra/deploy.sh
