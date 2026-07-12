# Zentinull — Device Entity Resolution Pipeline

[![CI](https://github.com/moonlite/zentinull/actions/workflows/ci.yml/badge.svg)](https://github.com/moonlite/zentinull/actions/workflows/ci.yml)

Pulls device records from 6+ sources (SharePoint, ManageEngine, FortiGate,
Zabbix, Active Directory, ServiceDesk Plus) into per-source SQLite databases,
runs Splink entity resolution to cluster matching devices, and serves the
merged device mesh via a FastAPI API.

## Quick start (Docker)

No credentials or Python setup needed — demo data is generated automatically.

```bash
# 1. Seed demo data and start the API
docker compose run --rm demo && docker compose up api

# 2. Open http://localhost:8001/docs  (interactive API docs)
#    or   http://localhost:8001/dashboard  (KPI endpoint)
#    or   http://localhost:8001/device-view?q=WS-001  (device view)
```

With dashboard (Streamlit):

```bash
docker compose --profile all up api dashboard
# → http://localhost:8501
```

## Quick start (native)

```bash
# Install
pip install -e .

# Copy and edit credentials
cp .env.example .env

# Seed demo data (no credentials required)
python scripts/seed_demo_data.py

# Or run the full pipeline against real sources
python scripts/run_ingest.py
python scripts/pipeline.py

# API server
python -m zentinull.api.server
# → http://localhost:8001/docs
```

## Development

### Docker dev (hot-reload)

Source code is mounted as a volume — edits in `./src/` trigger automatic
restarts via `uvicorn --reload`:

```bash
docker compose up api          # API only (port 8001)
docker compose --profile all up api dashboard  # + Streamlit (port 8501)
```

### Native dev

```bash
make dev-setup    # pip install -e ".[dev]" + pre-commit install
make check        # lint + typecheck + format check
make test-cov     # run tests with coverage
make bench        # test suite benchmarks with history tracking
make bench-api    # API endpoint performance regression gate
```

### VS Code (Dev Containers)

Open the repo in VS Code and click "Reopen in Container" (requires
Docker + Dev Containers extension). The `.devcontainer/devcontainer.json`
configures Python, Ruff, Mypy, and pre-commit automatically.

## Project structure

```
src/zentinull/           # Library code
├── pipeline.py         # Original pipeline orchestrator (subprocess)
├── logging_config.py   # Structured logging (key=value or JSON)
├── cli/                # Modern in-process pipeline + tools
│   ├── pipeline.py    # In-process run + status tracking + atomic load
│   ├── streaming.py   # Subprocess with live output + rotating log
│   ├── status.py      # Thread-safe JSON status tracking
│   ├── backup.py      # WAL checkpoint + DB copy + manifest
│   └── db_mgmt.py     # Database list, vacuum, integrity check
├── ingestors/          # One module per source
│   ├── base.py        # SQLite helpers (db, create_table, insert)
│   ├── auth.py        # Auth classes (MSAL, OAuth2, LDAP, API key)
│   ├── sharepoint.py  # SharePoint lists via API
│   ├── manageengine.py# ManageEngine EC + MDM
│   ├── fortigate.py   # FortiGate firewall inventory
│   ├── zabbix.py      # Zabbix monitored hosts
│   ├── ad.py          # Active Directory (LDAP)
│   └── servicedeskplus.py  # ServiceDesk Plus assets
├── api/                # FastAPI query layer
│   ├── server.py      # FastAPI app + CORS + lifespan
│   ├── router.py      # 12 REST endpoints
│   ├── db.py          # DuckDB query layer (MeshDB)
│   ├── models.py      # 6 frozen Pydantic models
│   └── schema.py      # Shared DuckDB DDL
└── export_for_splink.py  # Unified CSV export + field normalization
scripts/                # Runnable entry points
├── seed_demo_data.py  # Generate synthetic demo mesh (no deps)
├── bench.py           # Test suite benchmark runner
├── bench_api.py       # API endpoint performance regression gate
├── run_ingest.py      # Sequential ingest from all 6 sources
├── run_splink.py      # Full Splink pipeline (train → predict → export)
└── build_training_set.py  # Labeled pairs for supervised training
serve.py                # Unified CLI — 10 subcommands
dashboard.py            # Streamlit dashboard (pipeline KPIs, search, clusters)
tests/                  # pytest suite (448+ tests, 92% coverage)
data/                   # SQLite databases, DuckDB mesh (gitignored)
export/                 # CSV exports (gitignored)
```

## CLI

```bash
python serve.py status             # Pipeline status & timing
python serve.py backup             # Backup all databases
python serve.py db list            # List SQLite DBs with row counts
python serve.py db vacuum          # VACUUM all SQLite DBs
python serve.py logs               # Tail pipeline log
```

## Configuration

Copy `.env.example` to `.env` and fill in credentials. The API server,
ingestors, and pipeline read from environment variables — no secrets
are hardcoded.

## Quality

| Gate | Status |
|---|---|
| Tests | 448+ tests, 92% coverage |
| Lint | Ruff (E, F, I, N, W, UP, B, SIM, ARG) |
| Types | Mypy strict mode |
| Benchmarks | Historical tracking with regression gates |
| CI | GitHub Actions — lint → typecheck → test → bench |
| Pre-commit | Ruff lint, Ruff format, trailing-whitespace, YAML check |
