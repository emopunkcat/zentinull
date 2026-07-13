# Zentinull Architectural Audit — 2026-07-12

5-lens vulnerability analysis. Findings ranked by severity (P0 = data loss/corruption, P1 = silent failure, P2 = drift/tech debt).

---

## Lens 1: Data Contracts & Schema Correspondence

### 1.1 P1 — SourceRecord model missing `name_clean`

**The Diagnosis:** Implicit Schema Drift — Pydantic model omits a field that exists in the CSV and DuckDB table.

**The Vulnerability:** `SPLINK_FIELDS` (contracts.py) lists `name_clean`. The CSV export writes it. DuckDB `source_records` table imports it. But `SourceRecord` (models.py:10-29) does not declare it. The `_build_story()` method in `db.py:195-211` constructs `SourceRecord` manually, omitting `name_clean`. Any downstream consumer that expects `name_clean` in a `SourceRecord` gets nothing. The field is silently dropped at the API boundary.

**The Fix:**
```
# model + db.py construction — 5-minute fix
1. Add `name_clean: str = ""` to SourceRecord model (after `name` field)
2. Add `name_clean=_safe(rd.get("name_clean"))` to the SourceRecord constructor call in db.py:195-211
3. Add a contract test: assert set(SourceRecord.model_fields.keys()) >= set(SPLINK_FIELDS)
```

### 1.2 P2 — ClusterInfo model missing `imei`

**The Diagnosis:** Implicit Schema Drift — aggregate model omits a column that exists in the devices DuckDB table.

**The Vulnerability:** DEVICES_SQL (schema.py:54) selects `imei` into the `devices` table. But `_row_to_cluster_info()` (db.py:645-668) does not read or assign `imei`, and `ClusterInfo` (models.py:32-48) has no `imei` field. Mobile device IMEI data reaches the database but is invisible to the API. No error, no warning — just silently absent.

**The Fix:**
```
1. Add `imei: str = ""` to ClusterInfo model
2. Add `imei=_safe(row.get("imei"))` to _row_to_cluster_info() in db.py
3. Add contract test: assert set(ClusterInfo.model_fields.keys()) >= set of devices table columns
```

### 1.3 P2 — DDL/Model naming mismatch: `mac_address_normalized` vs `mac_address`

**The Diagnosis:** Implicit Schema Drift — the database column and the API model use different names for the same field.

**The Vulnerability:** `DEVICES_SQL` creates column `mac_address_normalized`. `ClusterInfo` model calls it `mac_address`. `_row_to_cluster_info()` bridges this with `mac_address=_safe(row.get("mac_address_normalized"))`. This works but creates a maintenance hazard: anyone adding a query that reads `mac_address` from the devices table directly will get an empty string because the column doesn't exist under that name.

**The Fix:**
```
Option A (preferred): Rename the DDL column to `mac_address` — single source of truth
Option B: Rename the model field to `mac_address_normalized` for consistency with the DB
Choose one and apply across DDL, model, db.py queries, and router
```

### 1.4 P2 — No automated contract validation test

**The Diagnosis:** Missing Contract Enforcement — data alignment is convention-based, not machine-checked.

**The Vulnerability:** SPLINK_FIELDS ↔ additional_columns_to_retain ↔ DuckDB DDL ↔ Pydantic models must stay in sync across 4+ files. Currently enforced only by human discipline. The `zbx` FIELD_MAP was missing for months (captured in learned lessons). A column rename in any layer silently breaks downstream.

**The Fix:**
```
Add a test module tests/test_contracts.py with assertions:
- set(SPLINK_FIELDS) == set of CSV columns from export
- set(SPLINK_FIELDS) == set(additional_columns_to_retain) + unique_id
- DuckDB source_records columns ⊇ SPLINK_FIELDS
- SourceRecord model fields ⊇ SPLINK_FIELDS
- devices table columns ⊆ ClusterInfo model fields
```

---

## Lens 2: State Management & Persistence Integration

### 2.1 P0 — Status module read-modify-write race condition

**The Diagnosis:** TOCTOU Race — no lock held between read and write, losing concurrent updates.

**The Vulnerability:** Every public function (`record_start`, `record_done`, `record_fail`, `record_freshness`) follows the pattern:
```python
data = _read()     # flock(LOCK_SH) → read → unlock
# ... no lock held here ...
data["stages"][stage] = {...}
_write(data)       # flock(LOCK_EX) → temp-file → os.replace → unlock
```
Two concurrent callers can both `_read()` the same snapshot, each mutate independently, then each `_write()`. The second `_write()` silently overwrites the first's changes. The exclusive lock on `_write` only prevents concurrent writes — it does nothing to protect the stale read. `fcntl.flock` is advisory and POSIX-compliant, but advisory locking cannot fix a logic-level read-modify-write gap.

**The Fix:**
```
Phase 1 (immediate): Hold LOCK_EX across the entire read→mutate→write cycle.
  - Create _with_lock() context manager that opens FD, acquires LOCK_EX, yields data dict, then writes back.
  - All record_* functions use: with _with_lock() as data: data[...] = ...; return data

Phase 2 (later): Replace JSON file with SQLite status table (WAL mode, atomic transactions).
  - Single writer, many concurrent readers, no lock complexity.
```

### 2.2 P1 — PID lock TOCTOU: concurrent pipeline detection unreliable

**The Diagnosis:** File-Based Coupling — the concurrency guard has a check-then-write race.

**The Vulnerability:** `run_pipeline()` (pipeline.py:219-228):
```python
if lock_path.exists():          # check
    old_pid = int(lock_path.read_text().strip())
    os.kill(old_pid, 0)
    raise RuntimeError(...)
lock_path.write_text(str(os.getpid()))  # write
```
Two processes can both pass the `exists()` check before either writes. The PID check via `os.kill(pid, 0)` is a TOCTOU in itself — PID can be reused between check and write. There's no `O_EXCL|O_CREAT` open, no `fcntl.flock` on the lock file.

**The Fix:**
```
Phase 1 (immediate — 10-line fix): Use O_EXCL|O_CREAT for atomic lock acquisition:
  fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
  os.write(fd, str(os.getpid()).encode())
  # On OSError (EEXIST): read PID, check liveness, raise or unlink stale lock
  # In finally: os.close(fd); os.unlink(lock_path)

Phase 2 (later): Use fcntl.flock on a dedicated lock file (same pattern as status.py).
  - Advisory lock is released on process exit (kernel guarantee) — no stale lock cleanup needed.
```

### 2.3 P1 — SQLite ingestor DROP-then-CREATE crash window

**The Diagnosis:** Non-Atomic Schema Mutation — dropping the table before recreating it risks data loss.

**The Vulnerability:** `create_table()` in `base.py:35`:
```python
conn.execute(f"DROP TABLE IF EXISTS {name}")
conn.execute(sql)  # CREATE TABLE IF NOT EXISTS
conn.commit()
```
A crash or power loss between `DROP` and `CREATE` leaves the table gone. The next `insert()` would fail because the table doesn't exist. Combined with `synchronous=NORMAL` (WAL fsync not guaranteed on crash), the old data is irrecoverable. Each ingestor source has independent SQLite files, so a crash during `fg` ingest only loses FortiGate data — but the pipeline won't detect the missing table until export.

**The Fix:**
```
Phase 1 (immediate): Write to a temp table, validate, then atomically swap:
  CREATE TABLE {name}_tmp AS SELECT ...  -- build in temp
  -- validate row count
  DROP TABLE IF EXISTS {name}            -- only after validation
  ALTER TABLE {name}_tmp RENAME TO {name}  -- SQLite ALTER RENAME is atomic

Phase 2 (later): Use CREATE TABLE IF NOT EXISTS + INSERT OR REPLACE for idempotent ingest.
  - Avoids the DROP entirely; each ingest run replaces data in-place.
```

### 2.4 P2 — Dual pipeline orchestrators

**The Diagnosis:** Fragmented State Management — two implementations of the same workflow with different behaviors.

**The Vulnerability:** `cli/pipeline.py` (modern, in-process, status tracking, atomic load) and `pipeline.py` (legacy, subprocess-based, no status tracking). Legacy still has `__main__` entry point (`python -m zentinull.pipeline`). Maintenance burden: fixes applied to one don't automatically propagate. The legacy `_load_to_duckdb()` delegates to `cli.pipeline.run_load()` — creating a dependency from legacy → modern that could break if `run_load` changes its signature.

**The Fix:**
```
1. Remove legacy pipeline.py (or keep as thin wrapper that calls cli.pipeline.run_pipeline)
2. If anyone uses `python -m zentinull.pipeline`, redirect to cli.pipeline
3. Consolidate on the single modern implementation
```

### 2.5 P2 — Dashboard dual data access paths

**The Diagnosis:** Fragmented State Access — the dashboard reads mesh data via API but status via direct import.

**The Vulnerability:** `dashboard.py` uses:
- `httpx.get(f"{_API_BASE}/dashboard")` — correct, through API
- `from zentinull.cli.status import get_status` — bypasses API, reads file directly
- `from zentinull.cli.pipeline import run_ingest` — bypasses API, runs pipeline in-process

This creates two access patterns for the same data. The API has no `/status` endpoint, so the dashboard HAS to bypass it. If the API adds a `/status` endpoint later, the dashboard path diverges silently unless someone remembers to update both.

**The Fix:**
```
1. Add GET /status endpoint to the API (reads from MeshDB or status.json)
2. Switch dashboard to use the API for ALL data access — no direct imports of zentinull internals
3. Dashboard pipeline triggers should POST to an API endpoint, not import pipeline modules
```

---

## Lens 3: Internal API Boundaries & Integration

### 3.1 P1 — Dashboard imports pipeline internals directly

**The Diagnosis:** Tight Coupling to Internal Implementation — UI layer depends on CLI module internals.

**The Vulnerability:** `dashboard.py:85-132` imports `zentinull.cli.pipeline` functions directly:
```python
from zentinull.cli.pipeline import run_ingest, run_export, run_splink, run_load, run_pipeline
```
This means:
- The dashboard process runs ingest in-process (opens SQLite connections, writes files)
- A slow ingest blocks the Streamlit event loop
- Ingest errors surface as dashboard crashes, not API error responses
- The dashboard must have all ingestor auth env vars (ME_CLIENT_SECRET, etc.) — these are NOT present in Docker Compose's dashboard service

**The Fix:**
```
1. Add POST /pipeline/run endpoint to API that triggers pipeline asynchronously
2. Dashboard calls the API endpoint (httpx.post)
3. Remove all direct zentinull.cli imports from dashboard.py
4. Dashboard's Docker service no longer needs auth env vars
```

### 3.2 P2 — API health check couples to file system

**The Diagnosis:** Leaky Abstraction — the API layer probes the filesystem directly instead of through the DB adapter.

**The Vulnerability:** `router.py:45-51`:
```python
mesh_path = Path(str(getattr(db, "_path", ""))) if db else None
if mesh_path and mesh_path.exists():
    status["mesh_file"] = "present"
```
The health endpoint accesses `db._path` (a private attribute) and calls `Path.exists()`. This leaks the persistence implementation (file-based DuckDB) into the API concern. If MeshDB ever switches to an in-memory or network-attached DuckDB, this breaks.

**The Fix:**
```
1. Add a `ping() -> bool` method to MeshDB that returns True if the DB is queryable
2. Health check calls db.ping() instead of inspecting _path
3. Remove the mesh_file field from health response (or populate it from DB metadata)
```

### 3.3 P2 — Pipeline runs ingest in-process with no isolation

**The Diagnosis:** Tight Coupling — the pipeline runner imports ingestor modules directly, coupling their lifecycle to the pipeline process.

**The Vulnerability:** `cli/pipeline.py:40-82` imports each ingestor module and calls its `ingest()` function directly. This means:
- An ingestor crash (unhandled exception) crashes the entire pipeline
- Memory leaks in one ingestor affect all subsequent stages
- Ingestor import-time side effects (like `get_logger()`) run in the pipeline process
- No subprocess isolation between ingestors — can't parallelize

The legacy pipeline used subprocess isolation for this exact reason, but the modern pipeline sacrificed it for speed.

**The Fix:**
```
Phase 1: Wrap each ingestor call in try/except so one failure doesn't kill the pipeline (already partially done)
Phase 2: Use concurrent.futures.ProcessPoolExecutor for parallel ingestor execution
Phase 3: Add a per-ingestor timeout guard
```

---

## Lens 4: Observability & Log Centralization

### 4.1 P1 — Dead Prometheus metrics (pipeline_runs_total, db_errors_total)

**The Diagnosis:** Zombie Metrics — counters are defined and emitted in `/metrics` output but never incremented.

**The Vulnerability:** `metrics.py:106-114` defines `db_errors_total` and `pipeline_runs_total`. They appear in Prometheus output (always 0). No code in the entire codebase calls `.inc()` on them. This means:
- `/metrics` endpoint reports pipeline health as always-green
- DB errors aren't tracked — the `batch_lookup_error` log event exists but no metric
- Any monitoring dashboard relying on these counters sees 0 errors when there may be failures

**The Fix:**
```
Phase 1 (wire them):
  - db_errors_total: increment in MeshDB._conn() on connect failure, in _resolve_cluster on query failure
  - pipeline_runs_total: increment in cli/pipeline.py run_ingest/run_export/run_splink/run_load
    or in the status module record_done/record_fail

Phase 2 (add missing metrics):
  - Ingestor row counts per source
  - Pipeline stage durations (histogram)
  - DuckDB file size gauge
```

### 4.2 P1 — Dual file logging: setup() FileHandler + streaming.py RotatingFileHandler

**The Diagnosis:** Split-Brain Logging — two independent mechanisms write to the same log file.

**The Vulnerability:** When `serve.py` calls `setup(log_file=PIPELINE_LOG)`, it adds a `FileHandler` to the `zig` root logger. Separately, `streaming.py:_get_pipeline_log()` creates a dedicated `zig.cli.streaming` logger with its own `RotatingFileHandler` writing to the SAME file. Both write independently:
- The `zig` FileHandler uses the formatter from `setup()` (StructuredFormatter)
- The `zig.cli.streaming` RotatingFileHandler uses `%(message)s` format
- Rotation: the `zig` FileHandler doesn't rotate (unbounded). The streaming handler rotates at 10MB. This creates an asymmetric situation where the main log appends forever while the streaming log rotates, causing interleaved entries from two different formatters.

**The Fix:**
```
1. Remove the RotatingFileHandler from streaming.py
2. Replace with stdout emission only (12-factor compliance)
3. The subprocess output is already streamed to stderr via _emit_line() — that's sufficient
4. If file persistence is required, use a single log drain (e.g., tee to file from stdout at the process supervisor level)
```

### 4.3 P1 — RotatingFileHandler violates 12-factor app principles

**The Diagnosis:** File-Based Logging — the pipeline writes logs to a local file instead of stdout.

**The Vulnerability:** `streaming.py:26-37` creates a `RotatingFileHandler` that writes to `data/pipeline.log`. In containerized deployments:
- Logs are trapped inside the container filesystem, invisible to `docker logs`
- Log rotation requires filesystem access (not available in read-only root filesystems)
- The file path is hardcoded, making log aggregation (Fluentd, Loki, Datadog) harder to configure

**The Fix:**
```
Remove the RotatingFileHandler entirely. The subprocess output already streams to stderr.
For long-term log persistence, redirect stdout/stderr at the process supervisor level
(systemd journal, Docker log driver, or a sidecar log shipper).
```

### 4.4 P2 — logging_config bypasses config.py for 8 env var reads

**The Diagnosis:** Configuration Drift — the logging subsystem reads environment variables directly instead of using the centralized config module.

**The Vulnerability:** `logging_config.py` reads 8 env vars directly (`ZENTINULL_LOG_STYLE`, `ZENTINULL_LOG_RULES`, `ZENTINULL_LOG_FORMATS`, `ZENTINULL_LOG_SHOW`, `ZENTINULL_LOG_PRETTY`, `ZENTINULL_LOG_COMPACT_WIDTH`, `ZENTINULL_LOG_COLUMN_MAP`, `ZENTINULL_LOG_COMPACT_FORMATS`). Meanwhile `config.py` defines 7 corresponding constants (`LOG_LEVEL`, `LOG_JSON`, `LOG_PRETTY`, `LOG_STYLE`, `LOG_RULES`, `LOG_SHOW`, `LOG_FORMATS`) that are never imported. If a default value changes in config.py, the logging subsystem won't see it — it has its own hardcoded defaults.

**The Fix:**
```
Phase 1: Import LOG_STYLE, LOG_RULES, LOG_FORMATS, LOG_SHOW, LOG_PRETTY from config.py in logging_config.py
Phase 2: Remove the direct os.environ.get() calls in formatter __init__ methods
Phase 3: Add config.py LOG_COMPACT_WIDTH, LOG_COLUMN_MAP, LOG_COMPACT_FORMATS constants
```

### 4.5 P3 — get_logger() redundantly calls setup() on every invocation

**The Diagnosis:** Redundant Reconfiguration — every `get_logger()` call clears and re-adds all handlers.

**The Vulnerability:** `get_logger()` (logging_config.py) calls `setup(level="INFO")` on every call if `_initialized` wasn't set. The old `_initialized` guard was removed, and now `setup()` clears handlers each time — which means concurrent `get_logger()` calls from different threads create a race on `root.handlers.clear()` + `root.addHandler()`. In practice, this is benign because Python logging is thread-safe, but the repeated setup calls are wasteful.

**The Fix:**
```
Replace the guard with a simple bool flag:
  _configured = False
  def get_logger(name):
      global _configured
      if not _configured:
          setup(level="INFO")
          _configured = True
      ...
```

---

## Lens 5: Configuration Matching

### 5.1 P2 — 7 LOG_* config.py constants are dead code

**The Diagnosis:** Zombie Configuration — centralized config defines values no consumer reads.

**The Vulnerability:** `config.py:41-47` defines `LOG_LEVEL`, `LOG_JSON`, `LOG_PRETTY`, `LOG_STYLE`, `LOG_RULES`, `LOG_SHOW`, `LOG_FORMATS`. Zero imports of any `LOG_*` constant from config.py anywhere in the codebase. The logging system reads these env vars directly with its own defaults. If an operator changes a default in config.py, nothing happens — the logging subsystem has its own independent defaults scattered across formatter constructors.

**The Fix:**
```
Wire logging_config to use config.py constants (see Lens 4.4), then these become live.
Alternatively: delete the dead constants from config.py to avoid confusion.
```

### 5.2 P2 — API_HOST constant is dead code

**The Diagnosis:** Zombie Configuration — a config constant with no consumer.

**The Vulnerability:** `config.py:37` defines `API_HOST = "0.0.0.0"` from `ZENTINULL_HOST` env var. No module imports `API_HOST`. `server.py:14` imports `API_PORT` but not `API_HOST`. The uvicorn `host` parameter is hardcoded to `"0.0.0.0"` in `server.py:98`. The `ZENTINULL_HOST` env var is documented in `.env.example` but silently ignored.

**The Fix:**
```
Either:
1. Import API_HOST in server.py and use it for the uvicorn host parameter
2. Remove API_HOST from config.py and .env.example (if 0.0.0.0 is always correct)
```

### 5.3 P2 — SPLINK_THRESHOLD defined in config.py but never imported

**The Diagnosis:** Zombie Configuration — config constant not wired to its consumer.

**The Vulnerability:** `config.py:82` defines `SPLINK_THRESHOLD` from env var. `scripts/run_splink.py` reads `os.environ.get("SPLINK_THRESHOLD", "-5")` directly. `cli/pipeline.py:128` sets it as an env var for the subprocess. The config constant is a third, unused definition. If someone changes the default threshold in config.py, it has no effect — run_splink.py has its own hardcoded default.

**The Fix:**
```
1. Import SPLINK_THRESHOLD in scripts/run_splink.py (or pass as CLI arg)
2. Import SPLINK_THRESHOLD in cli/pipeline.py instead of reading os.environ
3. Or: remove SPLINK_THRESHOLD from config.py since the subprocess boundary requires env var passthrough anyway
```

### 5.4 P1 — Docker Compose auth mismatch: dashboard service has no env vars

**The Diagnosis:** Configuration Drift — the dashboard service cannot perform pipeline operations because it lacks credentials.

**The Vulnerability:** `docker-compose.yml`:
- `api` service: `env_file: .env` — has all auth vars
- `dashboard` service: only `PYTHONDONTWRITEBYTECODE=1` — no auth vars
- `demo` service: no env vars at all

The dashboard imports `zentinull.cli.pipeline.run_ingest` which imports ingestor modules which import from `zentinull.config` (e.g., `FG_API_KEY`). If someone clicks "Run Ingest" in the dashboard's Docker instance, the ingestor gets empty strings for all auth configs and silently fails. This is the "Docker Compose passes zero auth env vars" vulnerability from the learned lessons.

**The Fix:**
```
Option A (if dashboard keeps direct imports): add env_file: .env to dashboard service
Option B (preferred): move pipeline triggers to API — dashboard never needs auth vars
  (see Lens 3.1 fix)
```

### 5.5 P3 — .env.example stale comment about API_HOST/API_PORT

**The Diagnosis:** Documentation Drift — comment in example config doesn't match implementation.

**The Vulnerability:** The `.env.example` file has a comment saying API_HOST/API_PORT keys are not yet checked. Since config.py now defines them from `ZENTINULL_HOST`/`ZENTINULL_PORT`, the comment is misleading. Operators who set `API_HOST` in `.env` expecting it to work will be confused.

**The Fix:**
```
Update .env.example comments to reflect the actual env var names (ZENTINULL_HOST, ZENTINULL_PORT).
```

---

## Summary: Pareto Ranking

| # | Severity | Lens | Finding | Effort |
|---|---|---|---|---|
| 1 | **P0** | State | Status read-modify-write race | 30 min |
| 2 | **P1** | Data | SourceRecord missing name_clean | 5 min |
| 3 | **P1** | State | PID lock TOCTOU | 10 min |
| 4 | **P1** | State | SQLite DROP-then-CREATE crash window | 30 min |
| 5 | **P1** | O11y | Dead Prometheus metrics | 20 min |
| 6 | **P1** | O11y | Dual file logging + 12-factor violation | 15 min |
| 7 | **P1** | Config | Docker dashboard no auth vars | 15 min |
| 8 | **P1** | API | Dashboard imports pipeline internals | 60 min |
| 9 | **P2** | Data | ClusterInfo missing imei | 5 min |
| 10 | **P2** | Data | mac_address_normalized naming drift | 15 min |
| 11 | **P2** | Data | No contract validation tests | 30 min |
| 12 | **P2** | State | Dual pipeline orchestrators | 20 min |
| 13 | **P2** | State | Dashboard dual data access | 45 min |
| 14 | **P2** | API | Health check couples to filesystem | 10 min |
| 15 | **P2** | API | Pipeline no ingestor isolation | 60 min |
| 16 | **P2** | O11y | logging_config bypasses config.py | 20 min |
| 17 | **P2** | Config | 7 LOG_* dead constants | 20 min |
| 18 | **P2** | Config | API_HOST dead code | 5 min |
| 19 | **P2** | Config | SPLINK_THRESHOLD dead code | 5 min |
| 20 | **P3** | O11y | get_logger() redundant setup() calls | 10 min |
| 21 | **P3** | Config | .env.example stale comment | 5 min |
</zw>

</parameter>
