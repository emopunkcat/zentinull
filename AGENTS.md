# Repository Guidelines

## Project Overview

**Zentinull** is a device entity-resolution pipeline. It pulls device records from six upstream IT inventory systems (SharePoint, ManageEngine Endpoint Central / MDM, FortiGate, Zabbix, Active Directory, ServiceDesk Plus) into per-source SQLite raw stores, exports a unified Splink-compatible CSV, runs probabilistic clustering to resolve the same physical device across sources, loads the resolved clusters into a DuckDB mesh, and links non-identity context (tickets, Zabbix metrics) to the clusters. A FastAPI server and a Streamlit dashboard expose the merged mesh.

Core design rule: **ingest and entity resolution are cleanly separated**. Ingest writes raw JSON to a standard §4 schema; no deduplication or field mapping happens during ingest. Splink and the manifest walker handle all matching and normalization downstream.

Current live state (default project): 6/6 sources ingested, 1,378 exported device records, 740 resolved clusters, 10,725 attachment links, 8,491 Zabbix metrics loaded. WS28 resolves across all 6 identity sources (`fg`, `me_ec`, `me_mdm`, `sdp`, `sp`, `zbx`). `source_records` is keyed by `(source, source_id)` — the incremental load enforces this with DELETE+INSERT upserts.

## Architecture & Data Flow

```mermaid
flowchart LR
    S[6 Sources / 23 feeds] --> I[ingest]
    I --> E[export]
    E --> SP[splink]
    SP --> L[load]
    L --> A[attach]
    A --> API[FastAPI + dashboard]
```

|Stage|Module|Responsibility|
|---|---|---|
|**ingest**|`src/zentinull/ingest/runner.py` + strategies|Fetch from each source and write raw JSON to per-source SQLite tables.|
|**export**|`src/zentinull/export_for_splink.py`|Walk the manifest spec, normalize fields, and write `export/csv/devices.csv`.|
|**splink**|`src/zentinull/resolve/splink_runner.py`|Profile-driven 4-stage training (λ → u → EM → supervised), predict, and cluster.|
|**load**|`src/zentinull/cli/pipeline.py::run_load()`|Atomically load `clusters.csv` into `data/mesh.duckdb` via temp-and-swap.|
|**attach**|`src/zentinull/resolve/attach.py`|Link 6 ATTACHMENT feeds (`zbx_items`, `sdp_requests`, `sp_employees`, `sp_accountinfo`, `sp_devicenotes`, `sp_componentpurchases`) to anchor clusters via manifest `Link` specs.|
|**serve**|`src/zentinull/api/*`, `dashboard.py`|REST API on port 8001 with background scheduler (ingest + Splink refresh); Streamlit dashboard on port 8501.|

## Key Directories

|Path|Purpose|
|---|---|
|`src/zentinull/`|Installed package (`zentinull`, editable install).|
|`src/zentinull/config.py`|`ProjectPaths` dataclass, `Config` dataclass, `get_paths()`, `get_config()`, env-var-backed settings.|
|`src/zentinull/manifest/`|Manifest loader (`load_manifest`), typed dataclasses, walker, transform registry.|
|`src/zentinull/ingest/`|Strategy-driven fetcher + auth factory.|
|`src/zentinull/ingestors/`|SQLite raw-store helpers (`base.py`) + auth classes (`auth.py`).|
|`src/zentinull/resolve/`|Splink runner, attachment linker, SOT resolver, cluster validator, tier-1 classifier.|
|`src/zentinull/api/`|FastAPI server, router, DuckDB query layer, models, schema, metrics.|
|`src/zentinull/cli/`|In-process pipeline, status tracking, streaming, backup, DB tools.|
|`src/zentinull/normalizer.py`|Export-time MAC / serial / name normalization and sentinel stripping.|
|`src/zentinull/valentine.py`|Auto-discovers cross-source column matches via Valentine COMA, builds field registry.|
|`src/zentinull/worker.py`|Background scheduler — per-source ingest on manifest intervals, full Splink daily.|
|`projects/default/manifest.py`|Default project manifest — 6 systems, 23 feeds, 1 resolution profile.|
|`scripts/`|Standalone entry points (ingest, splink, training, benchmarks, seed, DQ shell).|
|`serve.py`|Unified CLI with 14 subcommands.|
|`dashboard.py`|Streamlit dashboard.|
|`data/`|Runtime SQLite + DuckDB files + status/log (gitignored).|
|`export/`|CSV exports for Splink (gitignored).|
|`tests/`|pytest suite mirroring the source layout.|

## Development Commands

All commands are wrapped in the `Makefile`:

```bash
make install           # pip install -e .
make install-dev       # pip install -e ".[dev]"
make setup-env         # cp .env.example .env
make dev-setup         # install-dev + pre-commit install

make lint              # ruff check src/zentinull/ scripts/ tests/
make format            # ruff format + ruff check --fix
make typecheck         # MYPYPATH=src mypy src/zentinull/
make check             # lint + typecheck + format check
make check-all         # lint + typecheck + test + format check + bench-api
make ci                # alias for check-all
make pre-commit        # pre-commit run --all-files

make test              # pytest tests/ -v
make test-cov          # pytest --cov=src/zentinull --cov-report=term-missing
make test-fast         # pytest -x -q --tb=short
make test-watch        # watchfiles re-run loop

make run-ingest        # python scripts/run_ingest.py
make run-splink        # python scripts/run_splink.py
make build-training    # python scripts/build_training_set.py
make run-pipeline      # serve.py pipeline
make run-api           # serve.py start
make run-all           # pipeline + API

make bench             # python scripts/bench.py
make bench-api         # python scripts/bench_api.py
make bench-ci          # bench-api --ci --regression-threshold=25

make clean             # remove caches; leaves data/ and export/

make docker-build      # docker compose build
make docker-up         # docker compose up api
make docker-up-all     # docker compose --profile all up api dashboard
make docker-demo       # seed demo data and start API+dashboard
make docker-down       # docker compose down
make docker-clean      # docker compose down --rmi local -v
```

Preferred interactive CLI:

```bash
python serve.py start                    # Live 24/7 server: API + background data refresh
python serve.py pipeline                 # full 5-stage pipeline
python serve.py ingest                   # all sources
python serve.py ingest --source fg       # single source
python serve.py ingest --skip sp,ad    # skip sources
python serve.py splink                   # entity resolution
python serve.py splink --skip-training --threshold -5
python serve.py export                   # SQLite → CSV
python serve.py load                     # clusters → DuckDB
python serve.py status                   # pipeline status
python serve.py backup                   # backup DBs + export
python serve.py logs                     # tail pipeline log
python serve.py db list|vacuum|check     # SQLite maintenance
python serve.py audit-mapping --propose  # propose raw-key mappings
python serve.py --project demo pipeline  # run under a named project
```

## Code Conventions & Common Patterns

### Formatting & Linting

- **Python 3.12+**, line length **120**.
- **Double quotes** for strings.
- Ruff selects: `E, F, I, N, W, UP, B, SIM, ARG, RUF100`; ignores `E501` (line-length handled by config), `B028` (intentional in hot-path logs).
- Mypy **strict mode** on `src/zentinull/`; `ignore_missing_imports` for `ldap3`, `splink`, `duckdb`.
- Tests skip `ARG` via per-file ignore.
- `from __future__ import annotations` is used throughout source.
- `py.typed` marker present for downstream consumers.

### Naming Conventions

|Layer|Pattern|Examples|
|---|---|---|
|Manifest systems|lowercase key|`sp`, `me`, `fg`, `zbx`, `ad`, `sdp`|
|Feed keys|`system_table`|`sp_devices`, `me_ec`, `fg_clients`|
|Strategy classes|snake_case|`rest_json`, `paged_json`, `sdp_cursor`, `json_rpc`, `ldap`|
|Auth classes|PascalCase|`APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth`|
|Pydantic models|PascalCase|`SourceRecord`, `ClusterInfo`, `DeviceStory`, `MeshStats`|
|DuckDB query methods|snake_case|`lookup()`, `batch_lookup()`, `device_metrics()`|
|CLI command handlers|`cmd_<name>(args)`|`cmd_start()`, `cmd_pipeline()` in `serve.py`|
|CLI public functions|`run_<stage>()`|`cli/pipeline.py`|
|Private helpers|`_` prefix|`_safe()`, `_norm_mac()`, `_row_to_cluster_info()`|
|Test files|`test_*.py`|`test_db_mesh.py`, `test_pipeline.py`|
|Logger names|`zig.<domain>.<sub>`|`ingest.runner`, `api.router`, `cli.pipeline`|

### Manifest-Driven Configuration

All pipeline configuration lives in `projects/<name>/manifest.py`:

```python
SYSTEMS = {
    "fg": System(
        auth=Auth(kind="api_key", options={"api_key": "FG_API_KEY"}),
        strategy="rest_json",
        label="FortiGate",
        schedule=1800,
        coverage=0.40,
        fields=("name", "os", "ip", "mac"),
    ),
}

FEEDS = {
    "fg_clients": Feed(
        system="fg",
        endpoint={"path": "/api/v2/cmdb/user/device"},
        role=Role.ANCHOR,
        profile="device",
        spec={
            "name": FieldSpec(paths=["hostname", "name"]),
            "mac": FieldSpec(paths=["mac"], transform="mac"),
            "os": FieldSpec(paths=["os"], transform="lower"),
        },
        store="clients",
    ),
    "sp_devicenotes": Feed(
        system="sp",
        endpoint={"base": "SHAREPOINT_BASE_URL", "path": "/sp_devicenotes"},
        role=Role.ATTACHMENT,
        store="sp_devicenotes",
        id_path="id",
        links=(
            Link(
                field="fields.LookupToDevicesLookupId",
                to="device",
                on="source_id",
                strategy="exact",
                scope=("sp_devices",),
            ),
        ),
    ),
}
```

Rules:
- `load_manifest()` validates 10 cross-reference constraints.
- Tuple specs are normalized to `FieldSpec` at load time.
- The device profile fields are the single source of truth for export, Splink, DuckDB schema, and API models.
- Feeds have a `role`: `Role.ANCHOR` (device identity, resolved by Splink), `Role.ATTACHMENT` (context linked to clusters after resolution), or `Role.CONTEXT` (stored but not linked automatically).
- `ATTACHMENT` feeds declare `links` — `Link(field, to, on, strategy, scope)` specs that `attach.py` resolves against the cluster keyspace.

### Ingest Strategy Pattern

```python
@register("paged_json")
def run(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    ...
```

Strategies receive a resolved `endpoint` dict and an auth object; they return a list of raw record dicts. The runner never knows source-specific details.

### §4 Raw Store Schema

Every feed table follows the same schema:

```sql
CREATE TABLE store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    remote_updated_at TEXT,
    fetched_at TEXT DEFAULT datetime('now')
);
CREATE UNIQUE INDEX store_sid ON store(source_id);
```

- `raw_json` is canonical sorted-keys JSON.
- `raw_hash` is SHA-256 of the canonical JSON; used for incremental skip-on-unchanged upsert.
- `source_id` is extracted from `raw_json` via `feed.id_path` dotted notation; falls back to `raw_hash` if empty.
- Full sync: `create_raw_store()` (DROP + tmp-swap).
- Incremental sync: `ensure_raw_store()` (preserve rows) + `upsert_raw_rows()` (insert/update/skip).
- Empty fetch (`[]`) returns 0 without touching the table — protects data on transient failures.

### Path Resolution

`src/zentinull/config.py` defines a frozen `ProjectPaths` dataclass and `resolve_paths(project=None)`. `PATHS = resolve_paths()` is created at import time.

- `default` project → `ROOT/data`, `ROOT/export`.
- Other project `p` → `ROOT/projects/p/state/data`, `ROOT/projects/p/state/export`.
- Backward-compat aliases (`DATA_DIR`, `EXPORT_DIR`, `MESH_DB`, etc.) point to `PATHS` fields.
- `serve.py` pre-parses `--project` from `sys.argv` and sets `os.environ["ZENTINULL_PROJECT"]` **before** importing any `zentinull` module, because `config.PATHS` resolves at import time.

### Normalizer

`src/zentinull/normalizer.py`:

```python
normalize_mac("00:1A:2B:3C:4D:5E")      # → "001a2b3c4d5e"
normalize_mac("00-1A-2B, 3C-4D-5E")    # → "001a2b3c4d5e" (first valid)
normalize_serial("SN-12345")            # → "12345"
normalize_serial("S/N: ABC123")          # → "ABC123"
normalize_name("HOSTNAME.DOMAIN.LOCAL")  # → "hostname"
strip_sentinels("", "N/A", "None")      # → ""
```

`NULL_SENTINELS = {"", "n/a", "none", "null", "na", "-", "unknown", "not available", "not applicable"}`.

### API Endpoints

|Endpoint|Purpose|
|---|---|
|`GET /device/{query}`|Device lookup by any identifier (name, serial, MAC, IP, user)|
|`GET /device/{query}/trace`|Full mesh trace: device → assigned user → their other devices, attachments, linked devices, VLANs, graph nodes/edges|
|`GET /device/{query}/metrics`|Zabbix metrics for a device|
|`GET /device/{query}/timeline`|Recent events for a device|
|`GET /device/{query}/stats`|Current state: latest metric values + event counts|
|`GET /device/{query}/attachments`|Linked attachment records (account info, notes, purchases, employees)|
|`GET /search?q=...`|Full-text device search|
|`GET /clusters`|Paginated cluster listing with filters|
|`GET /stats`|Dashboard stats|
|`GET /mesh`|Cross-source cluster statistics|
|`GET /anomalies`|Singletons, unnamed, no-serial devices|
|`GET /health`|Health check|
|`GET /metrics`|Prometheus metrics|
|`POST /pipeline/run`|Trigger full pipeline in background thread|
|`POST /batch`|Resolve multiple device queries in one request|

The mesh trace (`/device/{query}/trace`) is the sentinull-style full graph traversal:
resolves any keyword to a cluster, then expands to linked devices (shared assigned_user,
shared attachments), VLAN membership, and returns a node/edge graph for visualization.

### Error Handling

- **Ingest**: per-feed `try/except` in runner; log error, return 0, continue to next feed.
- **Pipeline**: per-stage status recording; `run_streaming()` raises `RuntimeError` on non-zero exit or timeout.
- **API**: `_db()` raises `HTTPException(503)` if mesh DB unavailable; `_resolve_cluster()` raises `HTTPException(404)` if not found.
- **Attach**: per-feed `try/except`; log and continue.
- **Status file**: `status.json` updated atomically via temp-file swap; advisory locking on POSIX only.

### Structured Logging

```python
from ..logging_config import get_logger

log = get_logger("ingest.runner")
log.info({"event": "inserted", "source": "fg", "rows": n})
```

Six formatters: `StructuredFormatter`, `JsonFormatter`, `PrettyFormatter`, `BrutalistFormatter`, `RegexBrutalistFormatter`, `ColumnarFormatter`. `StepTimer` wraps timing blocks. `request_id_var` injects request IDs.

### Async & Concurrency Patterns

- `POST /pipeline/run` runs in a background thread via `ThreadPoolExecutor(max_workers=1)` because the pipeline holds a PID lock.
- `serve.py start` launches a background scheduler (`worker.loop(register_signals=False)`) via `asyncio.create_task()` in the FastAPI lifespan; the scheduler runs per-source incremental ingest on manifest intervals and full Splink daily. No separate worker process needed.
- FastAPI endpoints are `async def`.
- DuckDB calls are synchronous and open a new read-only connection per call; called directly from async endpoints (no `run_in_executor`), so long queries block the event loop.
- Ingest is parallelized: `run_ingest()` uses `ThreadPoolExecutor(max_workers=6)` to run all 6 sources concurrently. Each system writes to its own SQLite DB, so no locking conflicts. Single-system path skips pool overhead.

### Gotchas

- `sqlite3.Row.__contains__(key)` checks integer indices, not column names — use `key in row.keys()` or `dict(row)`.
- `sqlite3.Row` has no `.get()` method.
- `--project` is an env-var pre-parse, not a normal argparse value; any non-default project must set `ZENTINULL_PROJECT` before importing `config`.
- Zoho OAuth returns HTTP 200 with `{"error": "invalid_grant"}` for revoked refresh tokens — `r.raise_for_status()` is not enough.
- Pydantic v2 strips undefined response-model fields silently; if the dashboard/client sees `None` for a key that exists in the dict, check the model definition first.
- DuckDB returns `datetime` objects for `TIMESTAMP` columns; API models expect `str` for some fields. Convert in `db.py` or use Pydantic validators.
- `source_records` must be loaded from `clusters.csv` with `all_varchar=true` to avoid type inference breaking `!= ''` checks and `LIKE` queries.
- Multi-MAC records can lose blocking keys if no valid MAC survives normalization; Splink falls back to serial-only blocking.
- Zabbix inventory fields are `serial_no_a`/`serial_no_b` and `macaddress_a`/`macaddress_b`, not `serial`/`mac`; the manifest spec must use those exact paths. In this environment the inventory is empty for all 73 hosts, so Zabbix merges must rely on hostname (and optionally IP).
- Adding `name_clean` to Splink blocking merges hostname-only sources (e.g., Zabbix) with MAC/serial-bearing records, but it can also collapse devices with generic hostnames like `iphone` when no stronger identifiers are present. Use composite blocking or tighter thresholds if that creates oversized clusters.
- `attach.py::build_keyspace()` indexes `source_id`, `name_clean`, `mac_clean`, `serial_number`, `asset_tag`, and `assigned_user`; `link_scope` feed keys are translated to `source_records.source` values via `_FEED_SOURCE_MAP` before filtering.

## Important Files

|File|Role|
|---|---|
|`serve.py`|Unified CLI — 14 subcommands, `--project` pre-parse, lazy imports, dotenv loader.|
|`dashboard.py`|Streamlit app — pipeline KPIs, device search, cluster explorer via httpx to API.|
|`src/zentinull/config.py`|Centralized env-var-backed config, `ProjectPaths`, `Config`, `get_paths()`, `get_config()`.|
|`src/zentinull/manifest/__init__.py`|Manifest loader + 10-rule validator.|
|`src/zentinull/manifest/types.py`|Frozen dataclasses: `Manifest`, `System`, `Feed`, `FieldSpec`, `ResolutionProfile`, etc.|
|`src/zentinull/manifest/walker.py`|Field extraction from raw JSON via dotted paths + transforms.|
|`src/zentinull/manifest/transforms.py`|Transform registry — `mac`, `serial`, `name`, `lower`, `first_of_list`, `join_list`.|
|`src/zentinull/ingest/runner.py`|Strategy dispatcher: `run_feed()`, `run_system()`.|
|`src/zentinull/ingest/auth_factory.py`|Builds auth objects from manifest `Auth` specs.|
|`src/zentinull/ingestors/base.py`|SQLite helpers: `create_raw_store()`, `ensure_raw_store()`, `insert_raw_rows()`, `upsert_raw_rows()`.|
|`src/zentinull/ingestors/auth.py`|`APIKeyAuth`, `OAuth2RefreshAuth`, `LDAPBindAuth`.|
|`src/zentinull/export_for_splink.py`|SQLite-to-CSV export via manifest walker.|
|`src/zentinull/normalizer.py`|MAC, serial, name normalization and sentinel stripping.|
|`src/zentinull/resolve/splink_runner.py`|Profile-driven Splink pipeline.|
|`src/zentinull/resolve/attach.py`|Post-cluster attachment linking; `build_keyspace()` resolves feed links against cluster identifiers.|
|`src/zentinull/resolve/classifier.py`|Tier-1 structural field classifier for audit-mapping proposals.|
|`src/zentinull/cli/pipeline.py`|5-stage pipeline orchestrator.|
|`src/zentinull/cli/status.py`|Thread-safe JSON status tracking.|
|`src/zentinull/cli/streaming.py`|Subprocess runner with live output + rotating log.|
|`src/zentinull/cli/backup.py`|WAL checkpoint + copy DBs + manifest.|
|`src/zentinull/cli/db_mgmt.py`|SQLite list, vacuum, integrity check.|
|`src/zentinull/api/server.py`|FastAPI app, CORS, lifespan, request ID middleware, background scheduler loop.|
|`src/zentinull/api/router.py`|19 REST endpoints + `/health`, `/metrics`, HTML `/device-view`; `/device/{query}/trace` for full mesh trace.|
|`src/zentinull/api/db.py`|DuckDB read-only query layer (`MeshDB`), including `device_trace()` for full mesh graph traversal, `device_vlans()` for runtime CIDR joins against `sp.sqlite`.|
|`src/zentinull/api/models.py`|21 frozen Pydantic response models.|
|`src/zentinull/api/schema.py`|DuckDB DDL + `create_mesh_tables()`.|
|`src/zentinull/api/metrics.py`|Prometheus-format metrics.|
|`projects/default/manifest.py`|Default project manifest — 6 systems, 23 feeds (8 ANCHOR, 7 ATTACHMENT, 8 CONTEXT), 1 resolution profile with `name_clean` + IP blocking for hostname-only sources.|
|`src/zentinull/worker.py`|Background scheduler — per-source incremental ingest on manifest intervals, full Splink daily; runs inside `serve.py start` or standalone via `python -m zentinull.worker`.|
|`scripts/run_ingest.py`|Thin runner — `ingest_adapter.run_ingest()`.|
|`scripts/run_splink.py`|Shim — calls `splink_runner.run()`.|
|`scripts/build_training_set.py`|Builds Splink training labels from CSV.|
|`scripts/seed_demo_data.py`|Synthetic demo mesh generator.|
|`scripts/bench.py`|Test suite timing + coverage.|
|`scripts/bench_api.py`|API endpoint latency benchmark + CI regression gate.|

## Runtime & Tooling Preferences

|**Python**|`>= 3.12` (uses `python3` binary — no bare `python` on this system)|
|**Package manager**|pip + setuptools (src layout, editable install required: `pip install -e .`)|
|**Formatter**|Ruff (`ruff format`, line-length 120, double quotes)|
|**Linter**|Ruff (`ruff check`, select `E,F,I,N,W,UP,B,SIM,ARG,RUF100`)|
|**Type checker**|Mypy strict mode (`mypy src/zentinull/`; ignores `ldap3`, `splink`, `duckdb`)|
|**Test runner**|pytest with `asyncio_mode = "auto"`|
|**Pre-commit**|Ruff lint+format, trailing-whitespace, EOF fixer, YAML check, no large files (>500KB), no merge conflicts, detect private keys, mypy|
|**CI**|GitHub Actions — 4 jobs: lint → typecheck → test+cov → benchmark regression gate|
|**Core databases**|SQLite (per-feed raw stores, WAL mode), DuckDB (mesh, read-only queries)|
|**Entity resolution**|Splink 4.x|
|**API server**|`serve.py start` on `0.0.0.0:8001` — single 24/7 command: serves queries + runs background scheduler (ingest per-source on manifest intervals, Splink daily). No separate worker needed.|
|**Dashboard**|Streamlit on port 8501 (auto-assigned)|
|**Docker**|Multi-stage build on `python:3.12-slim`; dev stage with `--reload` and volume mounts|
|**Docker Compose**|4 services: `api` (includes scheduler), `dashboard`, `demo`, `worker` (optional standalone)|
|**Key external deps**|FastAPI 0.115+, DuckDB 1.2+, Splink 4.0+, Pydantic 2.10+, pandas 2.2+, ldap3 2.9+, requests 2.32+, httpx 0.28+|
|**Dev deps**|pytest 8+, pytest-cov 6+, pytest-asyncio 0.25+, ruff 0.9+, mypy 1.15+, pre-commit 4+|
|**Secrets**|Environment variables only; `.env` loaded by `serve.py`|

## Testing & QA

### Framework

- **pytest 8.x** with `pytest-asyncio` (`asyncio_mode = "auto"`).
- **675 tests** across 38 test files in 5 subpackages plus root-level files.
- **92% coverage** target (measured by CI).

### Conftest Fixtures

|Fixture|Purpose|
|---|---|
|`inmemory_db`|SQLite `:memory:` with `Row` factory|
|`sample_device_record`|10-field dict for unit tests|
|`seeded_meshdb`|DuckDB with 4 devices, 7 source records, 5 metrics, 3 events|
|`mock_meshdb`|`MagicMock(spec=MeshDB)` for router tests|
|`client_with_db`|`TestClient` with `mock_meshdb` on `app.state.db`|
|`client`|`TestClient` with `app.state.db = None` (503 path)|
|`temp_data_dir`|Temporary `data/` subdirectory|
|`isolated_status`|Monkeypatches `status.py` `PATHS` to temp location|
|`temp_sqlite_db`|Minimal SQLite DB for DB mgmt tests|

### Test Organization

- Layout mirrors source: `tests/api/`, `tests/cli/`, `tests/ingest/`, `tests/ingestors/`, `tests/logging/`.
- Class-based grouping for complex modules (`TestSearch`, `TestDashboard`, `TestRunIngest`).
- Function-based for pure-function tests (`test_normalizer.py`).
- No `pytest.mark.parametrize` — explicit individual tests.
- No fixture scopes beyond function scope.
- Inline imports inside test bodies (not at module top).
- Docstrings follow Given/When/Then style.
- `capsys` for stdout assertions, `caplog` for logging assertions.
- Mocking: `monkeypatch` for module-level constants (`PATHS`); `unittest.mock.patch` for functions/classes.
- `PATHS` is frozen — tests must replace it entirely via `monkeypatch.setattr`, not mutate in place.

### Running Tests

```bash
make test          # python -m pytest tests/ -v
make test-cov      # pytest --cov=src/zentinull --cov-report=term-missing
make test-fast     # pytest -x -q --tb=short
make check         # lint + typecheck + format check
make check-all     # full quality gate: lint + typecheck + test + format check + bench-api
make ci            # alias for check-all
```

### Coverage & Benchmarks

- Coverage source is `zentinull`; `omit` includes `tests/` and `__pycache__`.
- `scripts/bench.py` tracks pytest timing + coverage with historical trend.
- `scripts/bench_api.py` benchmarks 13+ API endpoints against seeded `TestClient`; CI regression gate fails if latency regresses >25%.
- Benchmark history is cached in `.benchmarks/` between CI runs.
