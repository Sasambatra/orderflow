PYTHON ?= python3
VENV = .venv/bin

.PHONY: setup up down migrate seed lint format test test-integration test-concurrency test-e2e test-docker demo benchmark check build build-python run worker beat
setup:
	$(PYTHON) -m venv .venv
	$(VENV)/python -m pip install --require-hashes -r requirements-dev.lock
	$(VENV)/python -m pip install --no-deps -e .
	$(VENV)/python scripts/setup_env.py
up:
	docker compose up -d --build --wait api worker beat
down:
	docker compose down
migrate:
	$(VENV)/alembic upgrade head
seed:
	$(VENV)/orderflow seed
lint:
	$(VENV)/ruff check .
	$(VENV)/ruff format --check .
format:
	$(VENV)/ruff check --fix .
	$(VENV)/ruff format .
test:
	$(VENV)/pytest -q -m 'not e2e' --cov=orderflow --cov-fail-under=85
test-integration:
	$(VENV)/pytest -q tests/integration
test-concurrency:
	$(VENV)/pytest -q tests/concurrency
test-e2e:
	RUN_E2E=1 $(VENV)/pytest -q tests/e2e
test-docker:
	docker compose --profile test run --build --rm tests
demo:
	$(VENV)/python scripts/demo.py
benchmark:
	$(VENV)/python scripts/benchmark.py
check: lint test
	$(VENV)/python -m pip check
	$(VENV)/alembic check
build-python:
	$(VENV)/python -m build
build: build-python
	docker build --target runtime -t orderflow:local .
run:
	$(VENV)/uvicorn orderflow.main:app --host 127.0.0.1 --port 8001 --no-access-log
worker:
	$(VENV)/celery -A orderflow.workers.celery_app:app worker --loglevel=INFO --concurrency=2
beat:
	$(VENV)/celery -A orderflow.workers.celery_app:app beat --loglevel=INFO --schedule=.local/celerybeat-schedule
