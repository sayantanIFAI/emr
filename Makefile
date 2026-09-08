.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/compose/docker-compose.yml
PY := python

.PHONY: help
help: ## show targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: venv
venv: ## create local venv and install (editable + dev)
	$(PY) -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

.PHONY: up
up: ## start full stack (postgres, redis, minio, migrate, api, worker, watcher)
	$(COMPOSE) up -d --build
	@echo "API:      http://localhost:8080/healthz"
	@echo "MinIO UI: http://localhost:9001  (cdiadmin / cdiadminsecret)"

.PHONY: down
down: ## stop stack (keep volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## stop stack and delete volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## tail service logs
	$(COMPOSE) logs -f --tail=100

.PHONY: migrate
migrate: ## run alembic migrations against CDI_DATABASE_URL
	alembic upgrade head

.PHONY: api
api: ## run the ingestion API locally (needs infra up)
	uvicorn cdi_adapter.api:app --reload --port 8080

.PHONY: worker
worker: ## run a celery worker locally
	celery -A cdi_adapter.worker.celery_app worker -Q cdi -l info --concurrency 2

.PHONY: watch
watch: ## run the folder watcher locally
	$(PY) -m cdi_adapter.ingest.watcher

.PHONY: sample
sample: ## generate synthetic sample scans into the inbox
	$(PY) scripts/make_sample_docs.py --out $${CDI_INBOX_DIR:-./data/inbox}

.PHONY: test
test: ## run unit tests (integration tests auto-skip without infra)
	pytest -q

.PHONY: test-int
test-int: ## run all tests incl. integration (needs infra up + env vars)
	pytest -q -m "integration or not integration"

.PHONY: fmt
fmt: ## ruff format + lint --fix
	ruff format src tests scripts && ruff check --fix src tests scripts
