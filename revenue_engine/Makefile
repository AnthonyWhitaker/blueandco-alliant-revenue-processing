.PHONY: install initdb seed ui test compose-up compose-down clean deploy

PYTHON ?= python3
PY     ?= .venv/bin/python
PIP    ?= .venv/bin/pip

install:
	$(PYTHON) -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

initdb:
	$(PY) -c "from engine.db import init_db; init_db()"

seed: initdb
	$(PY) scripts/seed_guerbet.py
	$(PY) scripts/seed_tier_ab_suppliers.py

ui:
	.venv/bin/streamlit run ui/app.py --server.headless true

test:
	$(PY) scripts/regression_test_guerbet.py
	$(PY) scripts/test_tier_ab_suppliers.py

compose-up:
	docker compose up --build

compose-down:
	docker compose down

clean:
	rm -rf __pycache__ data/revenue.db
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

deploy:
	bash infra/deploy.sh
