# Repository Guidelines

## Project Overview

**Zentinull** — device entity resolution pipeline. Pulls ~1,500 device records from 6 IT inventory sources (SharePoint, ManageEngine EC+MDM, FortiGate, Zabbix, Active Directory, ServiceDesk Plus) into per-source SQLite databases, runs **Splink** (Python, ML-based) probabilistic entity resolution to cluster matching devices, then serves the merged device mesh via a **FastAPI** API backed by **DuckDB**.

Core design rule: **ingest and entity resolution are cleanly separated**. Ingestors are "dumb" — no dedup, no field mapping, no identity resolution during ingest. Splink handles all matching downstream. The full pipeline runs as four sequential stages.

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
                    [cli/pipeline.run_load()]
                    temp-and-swap atomic load
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
       (src/zentinull/api/ — 14 endpoints)
                │
                ▼
          [Streamlit dashboard]
            (dashboard.py)
```

**Layers:**

|Layer|Technology|Responsibility|
|---|---|---|
|**Ingest**|Python + SQLite|Per-source raw data dump, one module per source|
|**Export**|Python + CSV|Unify schemas, normalize fields for Splink|
|**Entity Resolution**|Splink (Python)|ML matching — 4-stage training (λ, u, EM, supervised), predict, cluster|
|**Mesh DB**|DuckDB|Consolidated device + metrics tables, indexed|
|**Config**|`src/zentinull/config.py`|Centralized env-var-backed settings and path constants — single source of truth for runtime config|
|**API**|FastAPI|REST query layer, read-only, HTML device view, Prometheus `/metrics`, enhanced `/health` with dependency probes|
|**Dashboard**|Streamlit|Pipeline monitoring, device search, cluster explorer — fetches all data via httpx calls to API (port 8001), no direct DuckDB access|
**Three invocation surfaces:**

- `serve.py` — unified argparse CLI with 13 subcommands (`start`, `pipeline`, `ingest`, `splink`, `export`, `load`, `seed`, `bench`, `bench-api`, `status`, `backup`, `logs`, `db`); uses lazy imports inside each `cmd_*` function
- `scripts/*.py` — standalone entry points for individual stages
- `dashboard.py` — Streamlit app triggered via `streamlit run dashboard.py`

**Two pipeline orchestrators:**
1. `src/zentinull/pipeline.py` (original) — subprocess-based, shells out to scripts
2. `src/zentinull/cli/pipeline.py` (modern) — in-process ingest/export, streaming subprocess for Splink, temp-and-swap atomic DuckDB load

Benchmarking: `scripts/bench.py` (pytest timing + coverage, historical trend) and `scripts/bench_api.py` (per-endpoint timing of 14 endpoints against seeded TestClient, regression gate) — both persist to `.benchmarks/`.

---

## Key Directories

|Path|Purpose|
|---|---|
|`src/zentinull/`|Installed package (`zentinull`, via `pip install -e .`)|
|`src/zentinull/config.py`|Centralized configuration — all env-var-backed settings and path constants|
|`src/zentinull/contracts.py`|Shared data contract constants — `SPLINK_FIELDS` list (16 unified columns)|
|`src/zentinull/api/`|FastAPI server + router (14 endpoints) + DuckDB query layer (`MeshDB`, 690 lines) + Pydantic models + schema DDL + Prometheus `/metrics`|
|`src/zentinull/api/metrics.py`|Lightweight Prometheus-format metrics collector (request count, latency, DB errors, pipeline runs)|
|`src/zentinull/cli/`|In-process pipeline runner, streaming subprocess, status tracking, backup, DB management, brutalist log renderer|
|`src/zentinull/export_for_splink.py`|SQLite-to-CSV export with 16-field unified schema|
|`src/zentinull/logging_config.py`|Structured logging framework (six formatters, `StepTimer`, `RequestIDFilter` for correlation IDs)|
|`tests/`|pytest suite — mirrors source layout under `tests/api/`, `tests/cli/`, `tests/ingestors/`, `tests/logging/` + 4 root-level test files|
|`data/`|Runtime database files (sqlite + duckdb + status.json + pipeline.log, gitignored)|
|`export/`|CSV files for Splink pipeline (gitignored)|
|`.benchmarks/`|Historical benchmark results (gitignored)|

### Top-level files

|File|Purpose|
|---|---|
|`serve.py`|Unified CLI — 13 subcommands for all pipeline operations|
|`dashboard.py`|Streamlit app — pipeline KPIs, device search, cluster explorer|
|`Makefile`|37 targets across setup, quality, test, pipeline, API, Docker|
|`pyproject.toml`|Package metadata, all tool config (Ruff, mypy, pytest, coverage)|

---

## Development Commands

All through **Makefile** (`.DEFAULT_GOAL = help`):

```bash
make install           # pip install -e ".[dev]"
make setup-env         # cp .env.example .env
make env-check         # validate .env has required vars

make lint              # ruff check src/zentinull/ scripts/ tests/
make format            # ruff format + ruff check --fix
make typecheck         # mypy src/zentinull/
make check             # lint + typecheck
make ci                # full quality gate: check + test + bench

make test              # python -m pytest tests/ -v
make test-cov          # pytest --cov
make test-fast         # pytest -x -q --no-header

make run-ingest        # Run all 6 ingestors (requires .env)
make run-splink        # Splink entity resolution (requires devices.csv)
make build-training    # Build training label set
make run-pipeline      # Full pipeline: ingest → export → splink → load
make run-api           # uvicorn on port 8001
make run-all           # pipeline + API in background

make bench             # pytest timing + coverage
make bench-api         # API endpoint latency benchmark

make clean             # Remove caches, build artifacts, runtime data
```

**serve.py CLI** (preferred for interactive use):

```bash
python serve.py start              # Start API server
python serve.py pipeline           # Full pipeline with streaming output
python serve.py ingest             # Run all ingestors
python serve.py ingest --source fg # Single source
python serve.py ingest --skip sp,ad
python serve.py splink             # Entity resolution
python serve.py splink --skip-training --threshold -5
python serve.py export             # SQLite → CSV
python serve.py load               # Clusters → DuckDB mesh
python serve.py status             # Pipeline status table
python serve.py backup             # Backup all data
python serve.py logs               # Tail pipeline log
python serve.py db list            # List SQLite DBs with sizes
python serve.py db vacuum          # VACUUM all DBs
python serve.py db check           # Integrity check
```

**Docker:**

```bash
docker compose up api              # API server with hot-reload
docker compose up dashboard        # Streamlit dashboard (needs api)
docker compose --profile demo run --rm demo  # Seed demo data
```

**Dashboard:**

```bash
streamlit run dashboard.py         # Opens on port 8501
```

---

## Code Conventions & Common Patterns

### Formatting & Linting

- **Python 3.12+**, line length **120**.
- **Double quotes** for strings (`"` not `'`).
- Ruff enforced: `E, F, I, N, W, UP, B, SIM, ARG, RUF100`. Ignores `E501` (handled by line-length), `B028` (intentional in hot-path logs).
- Mypy **strict mode** on `src/zentinull/`, with `ignore_missing_imports` for `ldap3`, `splink`, `duckdb`.
- Tests skip `ARG` rule via per-file ignore.
- Pre-commit hooks enforce Ruff lint+format, trailing-whitespace, EOF newline, YAML validity, and no large files.
- `from __future__ import annotations` used throughout source.
- `py.typed` PEP 561 marker present for downstream consumers.

### Naming

|Pattern|Examples|
|---|---|
|**Ingestor modules**|`sharepoint.py`, `fortigate.py`, `ad.py` (lowercase single word)|
|**Ingestor entry point**|`ingest() -> int` (returns row count)|
|**Auth classes**|`APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth` (PascalCase)|
|**Pydantic models**|`SourceRecord`, `ClusterInfo`, `DeviceStory`, `MeshStats` (PascalCase)|
|**DuckDB query methods**|`lookup()`, `batch_lookup()`, `device_metrics()` (snake_case)|
|**CLI command handlers**|`cmd_<name>(args: argparse.Namespace) -> None` in `serve.py`|
|**CLI public functions**|`run_<stage>()` in `cli/pipeline.py`, `record_<event>()` in `cli/status.py`|
|**Private helpers**|`_safe()`, `_norm_mac()`, `_row_to_cluster_info()` (prefixed with `_`)|
|**Test files**|`test_*.py`|
|**Test fixtures**|`inmemory_db`, `sample_device_record`, `seeded_meshdb` (descriptive)|
|**Logger names**|`zig.<domain>.<sub>` — `ingest.sp`, `ingest.fg`, `api.router`, `cli.pipeline`|

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

### Shared Data Contracts

`src/zentinull/contracts.py` defines `SPLINK_FIELDS` — the single source of truth for the 16 unified column names used across export, Splink, DuckDB schema, and API. Every layer imports from here. The `scripts/run_splink.py` file has its own `additional_columns_to_retain` list that MUST be kept in sync with `SPLINK_FIELDS` — columns not in that list are silently dropped by Splink during clustering.

### Error Handling

- **Ingestors**: per-endpoint `try/except`, log error as structured event, continue to next endpoint.
- **Pipeline (CLI, modern)**: `run_streaming()` raises `RuntimeError` on non-zero exit or timeout; ingest runs in-process with per-source `try/except`.
- **Pipeline (original)**: `_run_step()` wraps `subprocess.run()` — raises `RuntimeError` on non-zero exit.
- **API routers**: `_db()` raises `HTTPException(503)` if DuckDB unavailable; `_resolve_cluster()` raises `HTTPException(404)` if not found.
- **No dead letter tables** — malformed records logged and skipped.
- **Status tracking**: `cli/status.py` records `start`/`done`/`fail` per stage, readable by dashboard.

### Structured Logging

Every behavioral module uses the centralized framework:

```python
from ..logging_config import get_logger

log = get_logger("ingest.fg")  # hierarchy: zig.<component>
log.info({"event": "inserted", "source": "fg", "rows": n})
```

Six formatters: `StructuredFormatter` (key=value), `JsonFormatter` (JSON lines), `PrettyFormatter` (colored terminal), `BrutalistFormatter` (block-char badges), `RegexBrutalistFormatter` (regex highlighting + format templates), `ColumnarFormatter` (compact 48-char headlines). `StepTimer` context manager wraps timing blocks. Logger hierarchy is `zig.*` throughout. CLI modules (`cli/*`) use `get_logger("cli.<module>")`.

### SQLite3 Row Caveat

`sqlite3.Row.__contains__(key)` checks **integer indices**, not column names — `"col" in row` is always `False` for string keys. Safe patterns:
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

- All 14 endpoints are `async def`, but underlying DuckDB calls are synchronous.
- CORS wide open (`allow_origins=["*"]`).
- DB connection carried on `app.state.db`. Lifespan context manager handles init.
- HTML device view rendered as inline string template in `router.py` — no template engine.
- Version hardcoded as `"3.0"` in server.py (independent of package version).

### CLI Pipeline (modern, preferred)

`cli/pipeline.py` runs stages **in-process** where possible:
- **Ingest**: direct `import` of ingestor modules (no subprocess)
- **Export**: direct `import` of `export_for_splink.export()`
- **Splink**: `run_streaming()` subprocess with line-by-line output to stderr + rotating log (10MB, 5 backups)
- **DuckDB load**: **temp-and-swap atomicity** — loads into a temp DB, then replaces `mesh.duckdb` atomically via `Path.rename()`

All stages call `cli/status.py` functions (`record_start`, `record_done`, `record_fail`) to update `data/status.json`. The Streamlit dashboard reads this file for live pipeline status.

### Pipeline Invocation (original, legacy)

`pipeline.py` orchestrates via mixed invocation:
- **Scripts** (`scripts/run_ingest.py`): direct file path via `subprocess.run`
- **Package module** (`zentinull.export_for_splink`): `-m` flag via subprocess (must use `-m` for relative imports to work)
- **DuckDB load**: in-process (direct import, no subprocess), non-atomic direct write
- **Splink**: subprocess with 300s timeout

### Status Tracking

`cli/status.py` provides thread-safe JSON status tracking:

```python
from zentinull.cli.status import record_start, record_done, record_fail, record_freshness, get_status

record_start("ingest")
# ... work ...
record_done("ingest", rows=1423, sources=6)
record_freshness("fg", newest_record="2026-07-11T10:00:00Z", row_count=245)
```

Writes to `data/status.json` with atomic temp-file swap. Used by `cli/pipeline.py`, read by `serve.py cmd_status` and `dashboard.py`.

### Path Resolution

All modules compute ROOT via `Path(__file__).resolve().parent.parent.parent.parent` to locate `data/`, `export/`, etc. relative to project root.

### Auth Classes

Three reusable auth mechanisms in `ingestors/auth.py`:
- `APIKeyAuth` — Bearer token passed in header
- `OAuth2RefreshAuth` — client-credentials grant with automatic token refresh, persisted to `token_file`
- `LDAPBindAuth` — ldap3 connection builder

### Transform Functions

Pure functions returning `(records, columns)` tuples. No I/O in transforms. Used by ingestor modules for endpoint-specific response parsing.

---

## Important Files

|File|Role|
|---|---|
|`pyproject.toml`|Package metadata, dependencies (10 runtime + 5 dev), all tool config|
|`Makefile`|All dev commands (37 targets)|
|`.env.example`|Required env vars — 5 source auth blocks + server config|
|`serve.py`|Unified CLI — 13 subcommands, lazy imports inside `cmd_*` functions|
|`dashboard.py`|Streamlit app — pipeline KPIs, device search, cluster explorer|
|`src/zentinull/pipeline.py`|Original 4-stage orchestrator (subprocess-based, legacy)|
|`src/zentinull/cli/pipeline.py`|Modern in-process pipeline with atomic DuckDB load + status tracking|
|`src/zentinull/cli/streaming.py`|`run_streaming()` — subprocess with live output + rotating log|
|`src/zentinull/cli/status.py`|`record_start/done/fail/freshness()`, `get_status()`, `print_status()`|
|`src/zentinull/cli/backup.py`|`create_backup()` — WAL checkpoint + copy DBs + manifest|
|`src/zentinull/cli/db_mgmt.py`|`list_dbs()`, `vacuum_dbs()`, `check_dbs()`|
|`src/zentinull/cli/render.py`|Brutalist log renderer — `rich`-powered terminal output for pipeline streams, gated behind `ZENTINULL_LOG_STYLE=brutalist`|
|`src/zentinull/logging_config.py`|`StructuredFormatter`, `JsonFormatter`, `PrettyFormatter`, `BrutalistFormatter`, `RegexBrutalistFormatter`, `ColumnarFormatter`, `StepTimer`, `RequestIDFilter`, `get_logger()`|
|`src/zentinull/config.py`|Centralized configuration — env vars, path constants, ingestor auth settings|
|`src/zentinull/contracts.py`|`SPLINK_FIELDS` — shared data contract (16 unified column names)|
|`src/zentinull/ingestors/base.py`|`db()`, `create_table()`, `insert()`, `insert_raw()` — SQLite helpers|
|`src/zentinull/ingestors/auth.py`|`APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth`|
|`src/zentinull/export_for_splink.py`|Unified CSV export with `SPLINK_FIELDS` and `FIELD_MAP`|
|`src/zentinull/api/server.py`|FastAPI app, CORS, lifespan, request ID middleware, dotenv loading, uvicorn on port 8001|
|`src/zentinull/api/router.py`|14 REST endpoints (incl. /metrics, /health enhanced), inline HTML device viewer|
|`src/zentinull/api/db.py`|`MeshDB` — DuckDB query layer (690 lines), 7-step cluster resolution cascade|
|`src/zentinull/api/models.py`|8 frozen Pydantic models (`SourceRecord`, `ClusterInfo`, `DeviceStory`, `MetricRecord`, `EventRecord`, `MeshStats`, `DashboardStats`, `AnomaliesReport`)|
|`src/zentinull/api/metrics.py`|Lightweight Prometheus-format metrics — request count, latency histogram, DB errors, pipeline runs|
|`src/zentinull/api/schema.py`|DuckDB DDL constants (`SOURCE_RECORDS_SQL`, `DEVICES_SQL`, `METRICS_SQL`, `EVENTS_SQL`, `INDEXES_SQL`) + `create_mesh_tables()`|
|`scripts/run_ingest.py`|Sequential ingestor runner (all 6 sources, continues on error)|
|`scripts/run_splink.py`|Full Splink pipeline — load, 4-stage training, predict, threshold sweep, export|
|`scripts/build_training_set.py`|Builds labeled pairs from CSV for supervised Splink training|
|`scripts/seed_demo_data.py`|Synthetic demo data generator (self-contained, no credentials)|
|`scripts/bench.py`|Test suite timing + coverage benchmark with historical trend|
|`scripts/bench_api.py`|API endpoint latency benchmark with regression detection|

---

## Runtime & Tooling Preferences

|Requirement|Value|
|---|---|
|**Python**|`>= 3.12` (uses `python3` binary — no bare `python` command on system)|
|**Package manager**|pip + setuptools (src layout, editable install required: `pip install -e .`)|
|**Formatter**|Ruff (`ruff format`, line-length 120, double quotes)|
|**Linter**|Ruff (`ruff check`, select `E,F,I,N,W,UP,B,SIM,ARG,RUF100`)|
|**Type checker**|Mypy strict mode (ignores `ldap3`, `splink`, `duckdb`)|
|**Test runner**|pytest with `asyncio_mode = "auto"` (though all tests are synchronous)|
|**Pre-commit**|Ruff lint+format, trailing-whitespace, EOF fixer, YAML check, large-file guard, mypy|
|**CI**|4 jobs: lint → typecheck → test+cov → benchmark regression gate|
|**Core databases**|SQLite (per-source, WAL mode), DuckDB (mesh, read-only queries)|
|**Entity Resolution**|Splink 4.x (Python package)|
|**API server**|uvicorn on `0.0.0.0:8001`|
|**Dashboard**|Streamlit on port 8501 (auto-assigned)|
|**Docker**|Multi-stage build on `python:3.12-slim`, dev stage with `--reload` and volume mounts|
|**Docker Compose**|3 services: `api`, `dashboard`, `demo` (seeding, profile-gated)|
|**Key external deps**|FastAPI 0.115+, DuckDB 1.2+, Splink 4.0+, Pydantic 2.10+, pandas 2.2+, ldap3 2.9+, requests 2.32+, httpx 0.28+|
|**Dev deps**|pytest 8+, pytest-cov 6+, pytest-asyncio 0.25+, ruff 0.9+, mypy 1.15+, pre-commit 4+|
|**Secrets**|Environment variables only — no `.env` loader library (app/containers must export them)|

### Known Quirks

- `pyproject.toml` declares `dynamic = ["version"]` but no `setuptools-scm` dependency — version defaults to `0.0.0` or fails without git tags.
- `src/zentinull/__init__.py` does not exist — the namespace resolves only via installed editable mode.
- FastAPI app hardcodes `version="3.0"` in `server.py` independently of package version.

---

## Testing & QA

### Framework

- **pytest 8.x** with `pytest-asyncio` (`asyncio_mode = "auto"`, configured in `pyproject.toml`).
- **456 tests** across 28 files in 5 subpackages plus 4 root-level files.
- **92% coverage** (measured by CI).

### Conftest Fixtures

|Conftest|Key Fixtures|
|---|---|
|`tests/conftest.py`|`inmemory_db` (SQLite `:memory:` with `Row` factory), `sample_device_record` (10-field dict)|
|`tests/api/conftest.py`|`seeded_meshdb` (4-table DuckDB with 4 devices, 7 source records, 5 metrics, 3 events), `mock_meshdb` (MagicMock), `client_with_db`, `client` (FastAPI TestClient)|
|`tests/cli/conftest.py`|`temp_data_dir`, `temp_status_file`, `isolated_status` (monkeypatches status paths), `temp_sqlite_db`|

### Mocking Strategy

Dual approach:
- **`monkeypatch`** (built-in pytest fixture) — for module-level constants (`DATA_DIR`, `ROOT`, `STATUS_FILE`, `db()` function)
- **`unittest.mock.patch`** — for functions and class constructors (`requests.get`, `ldap3.Server`, `subprocess.run`, `uvicorn.run`, `_export_fn`)

When patching ingestor `ingest()` functions that import `db` locally (e.g. `from .base import db`), monkeypatch must target the **local name in the ingestor module's namespace** (`zentinull.ingestors.source.db`), not `zentinull.ingestors.base.db`.

### Test Patterns

- **Class-based grouping** for complex modules (e.g. `TestSearch`, `TestDashboard`, `TestRunIngest`)
- **Function-based** for simple pure-function tests
- **No `pytest.mark.parametrize`** — all tests are explicit individual functions/methods
- **No async tests** despite `asyncio_mode=auto`
- **No fixture scopes** beyond function scope
- **Inline imports** inside test function bodies (not at module top level)
- **Docstrings** follow Given/When/Then style
- **`capsys`** for stdout assertions, **`caplog`** for logging assertions
- **Type annotations** present but inconsistently used in tests

### Test Coverage by Area

|Area|Tests|Coverage|
|---|---|---|
|**API — MeshDB**|`test_db_mesh.py` (595 lines)|All 15 query methods, 7-dimension search, dashboard, anomalies|
|**API — Router endpoints**|`test_router_endpoints.py` (449 lines)|All 14 endpoints with 200/404/422/503 paths|
|**API — Schema**|`test_schema.py`|DDL verification, CSV→mesh loading|
|**API — Models**|`test_models.py` + `test_models_edge.py`|Round-trip serialization, edge cases, defaults|
|**API — Server**|`test_server.py`|Lifespan, CORS, port parsing|
|**API — Pure functions**|`test_db_pure.py`|`_safe()`, `_norm_mac()` edge-to-edge|
|**CLI — Pipeline**|`test_pipeline.py` (557 lines)|run_ingest, run_export, run_splink, run_load|
|**CLI — Backup**|`test_backup.py` (334 lines)|create_backup, manifest, WAL checkpoint|
|**CLI — Streaming**|`test_streaming.py` (141 lines)|run_streaming scenarios|
|**CLI — DB Mgmt**|`test_db_mgmt.py`|list/vacuum/check with capsys|
|**CLI — Status**|`test_status_api.py` + `test_status_format.py`|Lifecycle, formatting|
|**Ingestors — Base**|`test_base.py` + `test_base_extended.py`|SQLite helpers, WAL mode, JSON|
|**Ingestors — Auth**|`test_auth.py`|All 3 auth classes with mocking|
|**Ingestors — Transform**|`test_transform.py` (515 lines)|Pure transform functions (no I/O)|
|**Ingestors — Mock**|`test_ingest_mock.py` (614 lines)|All 6 source ingestors with temp SQLite + monkeypatch|
|**Logging**|`test_setup.py` + `test_formatters.py`|Setup modes, formatters, StepTimer|
|**Export**|`test_export.py`|Normalization, field mapping, edge cases|
|**Serve CLI**|`test_serve.py` (639 lines)|All 13 commands, arg parsing, delegation|
|**Original pipeline**|`test_original_pipeline.py`|Legacy orchestrator via mock|
|**Bench scripts**|`test_bench_scripts.py` (670 lines)|Bench.py and bench_api.py scenarios, CI regression|

### Running Tests

```bash
make test          # pytest -v
make test-cov      # with coverage (source=zentinull, omit tests+__pycache__)
make test-fast     # pytest -x -q --no-header
make bench         # pytest timing + coverage with historical trend
make bench-api     # API endpoint latency benchmark with regression detection
```
