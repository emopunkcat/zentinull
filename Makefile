# ── Zentinull — Device Entity Resolution Pipeline ────────────────────────────
.POSIX:
.DEFAULT_GOAL := help
.PHONY: help install install-dev setup-env env-check \
        lint format typecheck test test-cov test-fast test-watch bench bench-api bench-ci ci check check-all check-format check-fast \
        dev-setup pre-commit serve \
        run-ingest run-splink build-training run-pipeline \
        run-api run-all dev \
        clean docker-build docker-up docker-up-all docker-demo docker-down docker-clean



help:  ## Show this help
	@grep -Eh '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────────────────

PYTHON := python3

install:  ## Install package (editable)
	$(PYTHON) -m pip install -e .
install-dev:  ## Install with dev dependencies (lint, type-check, test)
	$(PYTHON) -m pip install -e ".[dev]"

setup-env:  ## Copy .env.example → .env if not present
	@test -f .env || cp .env.example .env && echo "Created .env — edit credentials"

.PHONY: env-check
env-check: setup-env  ## Ensure .env exists
dev-setup: install-dev  ## Install dev deps + pre-commit hooks
	$(PYTHON) -m pip install pre-commit
	pre-commit install

# ── Quality ────────────────────────────────────────────────────────────────

lint:  ## Run ruff linter
	ruff check src/zentinull/ scripts/ tests/

format:  ## Auto-format with ruff
	ruff format src/zentinull/ scripts/ tests/
	ruff check --fix src/zentinull/ scripts/ tests/
check: lint typecheck check-format  ## Lint + type check + format check

check-all: lint typecheck test check-format bench-api  ## Full quality gate (lint → typecheck → test → format → bench)

ci: check-all  ## Match CI pipeline (same as check-all)

typecheck:  ## Run mypy type checking
	MYPYPATH=src mypy src/zentinull/

pre-commit:  ## Run all pre-commit hooks on all files
	pre-commit run --all-files

# ── Test ───────────────────────────────────────────────────────────────────

test:  ## Run test suite
	$(PYTHON) -m pytest tests/ -v

test-cov:  ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ --cov=src/zentinull --cov-report=term-missing

test-fast:  ## Fast test: stop on first failure, no coverage
	$(PYTHON) -m pytest tests/ --tb=short -x -q

test-watch:  ## Re-run tests on file changes (watches src/ tests/)
	$(PYTHON) -m watchfiles --filter python '$(PYTHON) -m pytest tests/ --tb=short -x -q' src tests

bench:  ## Run benchmark suite and track historical performance
	$(PYTHON) scripts/bench.py

bench-api:  ## Run API endpoint benchmarks
	$(PYTHON) scripts/bench_api.py

bench-ci:  ## Run API benchmarks in CI mode (regression gate)
	$(PYTHON) scripts/bench_api.py --ci --regression-threshold=25


check-format:  ## Check formatting with ruff (diff only, no changes)
	ruff format --check --diff src/zentinull/ scripts/ tests/

check-fast:  ## Quick quality check: lint + format (skip typecheck)
	ruff check src/zentinull/ scripts/ tests/
	ruff format --check src/zentinull/ scripts/ tests/

run-ingest: env-check  ## Run all 6 source ingestors
	$(PYTHON) scripts/run_ingest.py

run-splink:  ## Run entity resolution (requires export/csv/devices.csv)
	$(PYTHON) scripts/run_splink.py

build-training:  ## Build training label set for Splink
	$(PYTHON) scripts/build_training_set.py

run-pipeline: env-check  ## Full pipeline: ingest → export → splink → load
	$(PYTHON) serve.py pipeline

# ── API ────────────────────────────────────────────────────────────────────

run-api: env-check  ## Start the FastAPI server (default port 8001)
	$(PYTHON) serve.py start


serve: run-api  ## Alias for run-api

run-all: env-check  ## Full pipeline (background) + launch API server
	@echo "Starting pipeline in background..."
	$(PYTHON) serve.py pipeline &
	$(PYTHON) serve.py start


dev:  ## Dev loop: watch files → test-fast → lint on pass
	$(PYTHON) -m watchfiles --filter python \
	  '$(PYTHON) -m pytest tests/ --tb=short -x -q && echo "✓ Tests pass" && ruff check src/zentinull/ scripts/ tests/ && ruff format --check src/zentinull/ scripts/ tests/ && echo "✓ Lint + format OK"' \
	  src tests
# ── Cleanup ────────────────────────────────────────────────────────────────
clean:  ## Remove caches, build artifacts, and runtime data
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	@echo "Cleaned project artifacts (data/ and export/ kept)"
docker-build:  ## Build Docker images (dev target)
	docker compose build
docker-up:  ## Start API with hot-reload (docker compose)
	docker compose up api
docker-up-all:  ## Start API + dashboard (docker compose)
	docker compose --profile all up api dashboard
docker-demo:  ## Seed demo data, then start API
	docker compose run --rm demo && docker compose up api
docker-down:  ## Stop and remove containers
	docker compose down
docker-clean:  ## Remove containers + volumes + images
	docker compose down --rmi local -v
