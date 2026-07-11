# ── Zentinull — Device Entity Resolution Pipeline ────────────────────────────
.POSIX:
.DEFAULT_GOAL := help

.PHONY: help install install-dev lint format typecheck test test-cov \
        clean run-ingest run-splink run-pipeline run-api run-all \
        build-training setup-env

help:  ## Show this help
	@grep -Eh '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────────────────

install:  ## Install package (editable)
	pip install -e .

install-dev:  ## Install with dev dependencies (lint, type-check, test)
	pip install -e ".[dev]"

setup-env:  ## Copy .env.example → .env if not present
	@test -f .env || cp .env.example .env && echo "Created .env — edit credentials"

.PHONY: env-check
env-check: setup-env  ## Ensure .env exists

# ── Quality ────────────────────────────────────────────────────────────────

lint:  ## Run ruff linter
	ruff check src/zentinull/ scripts/ tests/

format:  ## Auto-format with ruff
	ruff format src/zentinull/ scripts/ tests/
	ruff check --fix src/zentinull/ scripts/ tests/

typecheck:  ## Run mypy type checking
	mypy src/zentinull/

check: lint typecheck  ## Lint + type check

# ── Test ───────────────────────────────────────────────────────────────────

test:  ## Run test suite
	python -m pytest tests/ -v

test-cov:  ## Run tests with coverage report
	python -m pytest tests/ --cov --cov-report=term-missing

# ── Pipeline Steps ─────────────────────────────────────────────────────────

run-ingest: env-check  ## Run all 6 source ingestors
	python scripts/run_ingest.py

run-splink:  ## Run entity resolution (requires export/csv/devices.csv)
	python scripts/run_splink.py

build-training:  ## Build training label set for Splink
	python scripts/build_training_set.py

run-pipeline: env-check  ## Full pipeline: ingest → export → splink → load
	python -m zentinull.pipeline -v

# ── API ────────────────────────────────────────────────────────────────────

run-api: env-check  ## Start the FastAPI server (default port 8001)
	python -m zentinull.api.server

run-all: run-pipeline  ## Full pipeline + launch API server
	python -m zentinull.api.server

# ── Cleanup ────────────────────────────────────────────────────────────────

clean:  ## Remove caches, build artifacts, and runtime data
	rm -rf build/ dist/ *.egg-info/
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	@echo "Cleaned project artifacts (data/ and export/ kept)"
