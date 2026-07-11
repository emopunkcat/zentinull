# Repository Guidelines

## Project Overview

**Zentinull** — device entity resolution pipeline. Pulls ~1,500 device records from 6 sources (SharePoint, ManageEngine EC+MDM, FortiGate, Zabbix, Active Directory, ServiceDesk Plus) into per-source SQLite databases, runs **Splink** (Python, ML-based) entity resolution to cluster matching devices, then serves the merged device mesh via a **FastAPI** API backed by **DuckDB**.

Core design rule: **ingest and entity resolution are cleanly separated**. Ingestors are "dumb" — no dedup, no field mapping, no identity resolution during ingest. Splink handles all matching downstream. The full pipeline runs as four sequential stages via `make run-pipeline`.

---

## Architecture & Data Flow

```
6 Sources ──→ [Ingestors] ──→ per-source SQLite DBs (data/*.sqlite)
                                    │
                                    ▼
                              [export_for_splink.py]
                           unify + normalize → CSV
                                    │
                                    ▼
                            export/csv/devices.csv
                                    │
                                    ▼
                  ┌── scripts/build_training_set.py ──┐
                  │  (label pairs from name/mac/serial) │
                  └──────────────┬──────────────────────┘
                                 │
                                 ▼
                        [scripts/run_splink.py]
                     (Splink Linker: λ → u → EM → predict)
                                 │
                                 ▼
                        export/splink_output/clusters.csv
                                 │
                                 ▼
                        [pipeline._load_to_duckdb()]
                                 │
                                 ▼
                         data/mesh.duckdb
                               │   │
                    ┌──────────┘   └──────────┐
                    ▼                          ▼
           source_records                  devices
           metrics / events             (consolidated)
                    │
                    ▼
              [FastAPI on port 8001]
           (src/zentinull/api/ — 12 endpoints)
```

**Layers:**

| Layer | Technology | Responsibility |
|-------|-----------|----------------|
| **Ingest** | Python + SQLite | Per-source raw data dump, one module per source |
| **Export** | Python + CSV | Unify schemas, normalize fields for Splink |
| **Entity Resolution** | Splink (Python) | ML matching — 4-stage training (λ, u, EM, supervised), predict, cluster |
| **Mesh DB** | DuckDB | Consolidated device + metrics tables, indexed |
| **API** | FastAPI | REST query layer, read-only, HTML device view |

**Invocation:** `Makefile` orchestrates everything — `make run-ingest` / `make run-pipeline` / `make run-api`.

---

## Key Directories

| Path | Purpose |
|------|---------|
| `src/zentinull/` | Installed package (`zentinull`, via `pip install -e .`) |
| `src/zentinull/ingestors/` | 6 source-specific ingestors + `base.py` (SQLite helpers) + `auth.py` |
| `src/zentinull/api/` | FastAPI server + router + DuckDB query layer + Pydantic models |
| `scripts/` | Runnable entry points — `run_ingest.py`, `run_splink.py`, `build_training_set.py` |
| `tests/` | pytest suite (`tests/ingestors/`, `tests/api/`) |
| `data/` | Runtime database files (sqlite + duckdb, gitignored) |
| `export/` | CSV files for Splink pipeline (gitignored) |

---

## Development Commands

All through **Makefile** (`.DEFAULT_GOAL = help`):

```bash
make install           # pip install -e .
make install-dev       # pip install -e ".[dev]"
make setup-env         # cp .env.example .env
make env-check         # validate .env has required vars

make lint              # ruff check src/zentinull/ scripts/ tests/
make format            # ruff format + ruff check --fix
make typecheck         # mypy src/zentinull/
make check             # lint + typecheck

make test              # python -m pytest tests/ -v
make test-cov          # pytest --cov

make run-ingest        # Run all 6 ingestors (requires .env)
make run-splink        # Splink entity resolution (requires devices.csv)
make build-training    # Build training label set
make run-pipeline      # Full pipeline: ingest → export → splink → load
make run-api           # uvicorn on port 8001
make run-all           # pipeline + API in background

make clean             # Remove caches, build artifacts, runtime data
```

---

## Code Conventions & Common Patterns

### Formatting & Linting

- **Python 3.12+**, line length **120**.
- **Double quotes** for strings (`"` not `'`).
- Ruff enforced: `E, F, I, N, W, UP, B, SIM, ARG, RUF100`.
- Mypy **strict mode** on `src/zentinull/`, with `ignore_missing_imports` for `ldap3`, `splink`, `duckdb`.
- Two files skip mypy entirely: `router.py` and `db.py` (`# mypy: ignore-errors`).

### Naming

- **Ingestor modules**: lowercase single word — `sharepoint.py`, `fortigate.py`, `ad.py`.
- **Ingestor entry point**: `ingest() -> int` (returns row count).
- **Auth classes**: `PascalCase` — `APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth`, `MSALAuth`.
- **Pydantic models**: `PascalCase` — `SourceRecord`, `ClusterInfo`, `DeviceStory`, `MeshStats`.
- **DuckDB query methods**: `snake_case` — `lookup()`, `batch_lookup()`, `device_metrics()`, `_resolve_cluster()`.
- **Private helpers**: prefixed with `_` — `_safe()`, `_norm_mac()`, `_row_to_cluster_info()`.
- **Test files**: `test_*.py`.
- **Test fixtures**: descriptive — `inmemory_db`, `sample_device_record`.

### Ingestor Pattern

Every ingestor module follows this template:

```python
def ingest() -> int:
    conn = db("source_name")  # from .base — opens SQLite, drops old table
    total = 0
    for endpoint in ENDPOINTS:
        # 1. Authenticate (from auth.py)
        # 2. Fetch paginated data
        # 3. Transform to list of dicts
        # 4. create_table(conn, name, cols) → insert_raw(conn, name, records)
    conn.close()
    return total
```

Key rules: one table per source, raw JSON stored in `raw_json` column, no dedup, no ALTER TABLE at runtime. Malformed records are skipped (logged); missing fields are `""`/`NULL`.

### Error Handling

- **Ingestors**: per-endpoint `try/except`, log error as structured event, continue to next endpoint.
- **Pipeline**: `_run_step()` wraps `subprocess.run()` — raises `RuntimeError` on non-zero exit.
- **API routers**: `_db()` raises `HTTPException(503)` if DuckDB unavailable; `_resolve_cluster()` raises `HTTPException(404)` if not found.
- **No dead letter tables** — malformed records logged and skipped.

### Structured Logging

Every behavioral module uses the centralized framework:

```python
from ..logging_config import get_logger

log = get_logger("ingest.fg")  # hierarchy: zig.<component>
log.info({"event": "inserted", "source": "fg", "rows": n})
```

Two formatters: `key=value` (human, default) and JSON (`LOG_JSON=true`). `StepTimer` context manager wraps timing blocks. Logger hierarchy is `zig.*` throughout.

### SQLite3 Row Caveat
Be aware when writing new code: `sqlite3.Row.__contains__(key)` checks **integer indices**, not column names — `"col" in row` is always `False` for string keys. Safe patterns:
- `key in row.keys()` (preferred for existence check)
- `dict(row)` then use standard dict methods

`sqlite3.Row` also has no `.get()` method — calling `row.get(key, default)` raises `AttributeError`.

### DuckDB Connection Pattern

API (`MeshDB`) opens a read-only DuckDB connection per operation:

```python
def _conn(self) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(self.db_path), read_only=True)
    conn.execute("SET threads = 4")
    return conn  # caller closes in finally block
```

### FastAPI Patterns

- All 12 endpoints are `async def`, but underlying DuckDB calls are synchronous.
- CORS wide open (`allow_origins=["*"]`).
- DB connection carried on `app.state.db`. Lifespan context manager handles init.
- HTML device view rendered as inline string template in `router.py` — no template engine.

### Pipeline Invocation

`pipeline.py` orchestrates via mixed invocation:
- **Scripts** (`scripts/run_ingest.py`): direct file path via `subprocess.run`
- **Package module** (`zentinull.export_for_splink`): `-m` flag via subprocess (must use `-m` for relative imports to work)
- **DuckDB load**: in-process (direct import, no subprocess)
- **Splink**: subprocess with 300s timeout

---

## Important Files

| File | Role |
|------|------|
| `pyproject.toml` | Package metadata, dependencies, all tool config |
| `Makefile` | All dev commands |
| `.env.example` | Required env vars (copy to `.env`) |
| `SCOPE.md` | Design doc — architecture, schemas, rules (note: code has a fully built API that SCOPE lists "out of scope") |
| `src/zentinull/pipeline.py` | 4-stage orchestrator with `--skip-ingest` and `--dry-run` flags |
| `src/zentinull/logging_config.py` | `StructuredFormatter`, `JsonFormatter`, `StepTimer`, `get_logger()` |
| `src/zentinull/ingestors/base.py` | `db()`, `create_table()`, `insert()`, `insert_raw()` — SQLite helpers |
| `src/zentinull/ingestors/auth.py` | `APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth`, `MSALAuth` |
| `src/zentinull/export_for_splink.py` | Unified CSV export with `SPLINK_FIELDS` and `FIELD_MAP` |
| `src/zentinull/api/server.py` | FastAPI app, CORS, lifespan, uvicorn entry point on port 8001 |
| `src/zentinull/api/router.py` | 12 REST endpoints (`/device`, `/search`, `/dashboard`, `/mesh`, `/clusters`, `/anomalies`, etc.) |
| `src/zentinull/api/db.py` | `MeshDB` — 15 query methods, 7-step cluster resolution cascade |
| `src/zentinull/api/models.py` | 7 Pydantic models (`SourceRecord`, `ClusterInfo`, `DeviceStory`, `MeshStats`, `DashboardStats`, `AnomaliesReport`, `SourceRecordWithRaw`) |
| `scripts/run_ingest.py` | Calls all 6 `ingest()` functions sequentially, continues on error |
| `scripts/run_splink.py` | Full Splink pipeline — load, 4-stage training, predict, threshold sweep, export |
| `scripts/build_training_set.py` | Builds labeled pairs from CSV for supervised Splink training |

---

## Runtime & Tooling Preferences

| Requirement | Value |
|-------------|-------|
| **Python** | `>= 3.12` |
| **Package manager** | pip + setuptools |
| **Formatter** | Ruff (`ruff format`, line-length 120) |
| **Linter** | Ruff (`ruff check`) |
| **Type checker** | Mypy strict mode |
| **Test runner** | pytest with `asyncio_mode = "auto"` |
| **Core databases** | SQLite (per-source), DuckDB (mesh) |
| **Entity Resolution** | Splink (Python package, not Zingg/Java despite project name) |
| **API server** | uvicorn on `0.0.0.0:8001` |
| **CI** | None configured |
| **Key external deps** | FastAPI, DuckDB, Splink, LDAP3, `requests` |

---

## Testing & QA

### Framework

- **pytest** with `pytest-asyncio` (`asyncio_mode = "auto"`, configured in `pyproject.toml`).
- `tests/conftest.py` provides: `inmemory_db` (SQLite `:memory:` with `Row` factory), `sample_device_record` (11-field dict).
- Tests live in `tests/ingestors/` and `tests/api/`, matched as `test_*.py`.

### Running Tests

```bash
make test          # pytest -v
make test-cov      # with coverage (source=zentinull, omit tests+__pycache__)
```

### Current Coverage

**13 tests total — covers only the bottom two layers:**

| Area | Tests | What's tested |
|------|-------|---------------|
| `ingestors/base.py` | 5 | `create_table`, `insert`, `insert_raw` — schema creation, row counts, extra columns |
| `api/models.py` | 7 | All 7 Pydantic models — roundtrip serialization, default values, frozen compliance |
| `api/router.py` | 1 | Single test: 503 response when DB unavailable |

### Coverage Gaps (not covered at all)

- **MeshDB query layer** (`api/db.py`, 621 lines) — all 15 query methods, cluster resolution, search, aggregation
- **API endpoints** — 11 of 12 have no behavioral tests (only the 503 error path is tested)
- **All 6 ingestors** — hit live APIs, tested only via `make run-ingest` end-to-end
- **Pipeline** — no tests for orchestration, DuckDB mesh loading, or error handling
- **Export** — no tests for CSV generation, field mapping, or normalization
- **Logging config** — no tests for formatters or `StepTimer`
- **Auth** — no tests for any auth class
- **Scripts** — no tests for Splink pipeline, training set builder, or any script
- **No mocking, no parametrize, no async tests** — all tests are synchronous with no external dependencies mocked

### Test Style

- Inline imports inside test function bodies (not at module top)
- `inmemory_db` fixture for SQLite tests
- `TestClient` for router tests (`app.state.db = None`)
- Pure Pydantic roundtrips for model tests
- No `pytest.mark.parametrize`, no `unittest.mock`, no fixtures beyond the two in conftest
