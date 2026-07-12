# ── Zentinull — multi-stage Dockerfile ──────────────────────────────────────
# Targets: dev (hot-reload), prod (slim)
# Usage:
#   docker build --target prod -t zentinull:latest .
#   docker compose up        # dev mode with hot reload
#   docker compose run demo  # seed demo data

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 0: base — shared Python environment
# ═══════════════════════════════════════════════════════════════════════════════
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install OS deps — git for setuptools-scm, build-essential for some wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: deps — install Python dependencies (cache layer)
# ═══════════════════════════════════════════════════════════════════════════════
# Must copy src/ so pip install -e resolves the package location
FROM base AS deps

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install -e ".[dev]"

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2: dev — editable install + hot reload
# ═══════════════════════════════════════════════════════════════════════════════
# Scripts and dashboards are mounted as volumes in compose, but copy for
# a self-contained build that can run standalone.
FROM deps AS dev

COPY scripts/ scripts/
COPY serve.py dashboard.py ./

EXPOSE 8001 8501
CMD ["uvicorn", "zentinull.api.server:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 3: prod — static install, slim, no dev tooling
# ═══════════════════════════════════════════════════════════════════════════════
FROM python:3.12-slim AS prod

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Only runtime OS deps (no build-essential, no git)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install deps from a wheel build in builder stage
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install . && \
    rm -rf /root/.cache /tmp/*

COPY scripts/ scripts/
COPY serve.py dashboard.py ./

EXPOSE 8001
CMD ["uvicorn", "zentinull.api.server:app", "--host", "0.0.0.0", "--port", "8001"]
