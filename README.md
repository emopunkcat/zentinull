# Zentinull — Device Entity Resolution Pipeline

Pulls device records from 6+ sources (SharePoint, ManageEngine, FortiGate,
Zabbix, Active Directory, ServiceDesk Plus) into per-source SQLite databases,
runs Splink entity resolution to cluster matching devices, and serves the
merged device mesh via a FastAPI API.

## Quick start

```bash
# Install
pip install -e .

# Copy and edit
cp .env.example .env

# Run every ingestor
python scripts/run_ingest.py

# Full pipeline (ingest → export → splink → load)
python scripts/pipeline.py

# API server
python -m zentinull.api.server
```

## Project structure

```
src/zentinull/           # Library code
├── pipeline.py         # Pipeline orchestrator
├── logging_config.py   # Structured logging
├── ingestors/          # One module per source
│   ├── base.py         # SQLite helpers
│   ├── auth.py         # Auth classes (MSAL, OAuth2, LDAP, API key)
│   ...                 # sharepoint.py, manageengine.py, etc.
└── api/                # FastAPI query layer
    ├── server.py       # FastAPI app
    ├── router.py       # REST endpoints
    ├── db.py           # DuckDB query layer
    └── models.py       # Pydantic models
scripts/                # Runnable entry points
tests/                  # pytest suite
data/                   # SQLite databases, DuckDB mesh (gitignored)
export/                 # CSV exports (gitignored)
```

## Configuration

Copy `.env.example` to `.env` and fill in credentials. The API server,
ingestors, and pipeline read from environment variables — no secrets
are hardcoded.
