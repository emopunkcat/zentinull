# Comprehensive Repository Cleanup Audit & Remediation Plan

## Phase 1: Critical (Security, Data Loss, Crashes)
### [Critical] SQL injection risk in f-string SQL table/column names
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 243)
- **Effort:** M
- **Description:** Table name feed.store is interpolated into SQL via f-string: f"SELECT * FROM {feed.store}". While the manifest is validated at load time, this pattern appears in 6+ locations (export_for_splink.py:64, attach.py:245, valentine.py:95, base.py:38-116, db_mgmt.py:46) and the db_mgmt variant passes user-provided CLI args directly. Any path that writes to the manifest at runtime or has a malformed feed.store creates arbitrary SQL execution.
- **Fix:** Use parameterized queries with table names validated against a whitelist, or use SQLite's quote() to escape identifiers. The db_mgmt.py CLI path should validate table names against sqlite_master before building queries.

### [Critical] Per-request DuckDB connection created but never used — each endpoint opens its own
- **File:** `src/zentinull/api/server.py` (Lines: 124-140)
- **Effort:** M
- **Description:** The request_db_conn middleware (server.py:125-140) opens a DuckDB connection per request and stores it as request.state.db_conn. However, every router endpoint calls _db(request) which returns the MeshDB singleton, and every MeshDB method (lookup, search, dashboard, etc.) opens its OWN connection via _conn(). The per-request middleware connection is completely orphaned — opened then closed, never referenced by endpoint logic. This doubles connection churn and wastes I/O on every API call.
- **Fix:** Either (a) remove the request_db_conn middleware and have MeshDB methods accept an optional connection from request.state, or (b) refactor MeshDB to use a connection passed from middleware instead of always calling self._conn(). The cleanest fix: make MeshDB._conn() optionally accept a pre-existing duckdb connection, have middleware open one, and pass it through request.state.db_conn — then all endpoints use that single connection.

## Phase 2: Quick Wins (XS/S effort, High/Medium Impact)
### [High] SSL certificate verification disabled globally in rest_json strategy
- **File:** `src/zentinull/ingest/strategies/rest_json.py` (Lines: 51)
- **Effort:** S
- **Description:** requests.get(url, ..., verify=False) disables SSL certificate verification for all REST JSON fetches including ManageEngine, ServiceDesk, and SharePoint calls. This opens the door to MITM attacks against the ingestor pipeline — any attacker on the network can intercept OAuth tokens, API keys, and device data in transit.
- **Fix:** Remove verify=False, or make it configurable per-system via manifest so it's opt-in only for known-self-signed endpoints like FortiGate. Default must be verify=True.

### [High] Broad bare except Exception swallowing in db.py ping() and dashboards
- **File:** `src/zentinull/api/db.py` (Lines: 47)
- **Effort:** S
- **Description:** ping() catches 'except Exception' and returns False — silently swallowing ImportError, duckdb.Error, OSError, etc. dashboard() and anomalies() also catch Exception to log + re-raise, but the re-raise in the middleware's bare except (server.py:67) catches everything. This masks transient vs permanent failures and makes debugging impossible without reading server logs.
- **Fix:** Catch specific exceptions: duckdb.Error and OSError for DB operations. Remove the bare-except middleware fallback in server.py that masks 500 errors with metrics.inc().

### [High] assert statements used for production control flow in db.py
- **File:** `src/zentinull/api/db.py` (Lines: 445, 449, 452, 455, 474, 520, 523, 549, 578)
- **Effort:** S
- **Description:** At least 9 locations use 'assert row is not None' after fetchone() calls. Python assert statements are stripped when running with -O (optimized mode), silently producing NoneType errors at runtime. The correct pattern is to check and raise a specific exception.
- **Fix:** Replace 'assert row is not None' with explicit None checks that raise RuntimeError or HTTPException with descriptive messages, e.g. 'if row is None: raise RuntimeError("devices table empty — run pipeline first")'.

### [High] Thread-safety race in _get_manifest() module-level cache
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 65-69)
- **Effort:** XS
- **Description:** _get_manifest() uses hasattr-based cache: 'if not hasattr(_get_manifest, "_cache"): _get_manifest._cache = load_manifest()'. The hasattr + assignment is not atomic — two threads racing through the first call can both execute load_manifest() and one will overwrite the other's cache. While load_manifest() is idempotent, the race pattern indicates broader thread-safety gaps.
- **Fix:** Use a threading.Lock or functools.lru_cache decorator for thread-safe memoization. lru_cache on _get_manifest itself (with maxsize=1) is simpler and already used in get_config().

### [High] load_manifest() called on every render with no error handling
- **File:** `dashboard.py` (Lines: 228-230)
- **Effort:** S
- **Description:** The data freshness section calls load_manifest() during every Streamlit render (every 30s auto-refresh AND every button click / text input). This re-imports and re-validates the manifest module on every interaction. If the manifest is corrupted, missing, or validation fails, the import raises an unhandled exception that crashes the entire dashboard with a 500-level traceback. The stale freshness data is then invisible.
- **Fix:** Wrap in try/except and show a fallback message on error. Also wrap with @st.cache_resource to avoid re-importing the module on every script re-run — the manifest is immutable once loaded.

### [High] Live .env has dead env vars (API_PORT, LOG_LEVEL, etc.) that app doesn't read — silent config drift
- **File:** `.env` (Lines: 26-30)
- **Effort:** XS
- **Description:** The local .env file sets API_HOST, API_PORT, LOG_LEVEL, LOG_JSON, and LM_STUDIO_BASE_URL — none of which are ever read by the application. config.py reads ZENTINULL_HOST, ZENTINULL_PORT (with PORT fallback), ZENTINULL_LOG_LEVEL, ZENTINULL_LOG_JSON. Since all five have sensible defaults (0.0.0.0, 8001, INFO, false), the server 'works' but configuration changes to API_PORT=9000 silently have zero effect. The .env.example warns about this (lines 25-28), but the actual .env hasn't been updated.
- **Fix:** Replace API_HOST → ZENTINULL_HOST, API_PORT → ZENTINULL_PORT, LOG_LEVEL → ZENTINULL_LOG_LEVEL, LOG_JSON → ZENTINULL_LOG_JSON in .env. Remove LM_STUDIO_BASE_URL (completely unused) or add ZENTINULL_ prefix if needed elsewhere. Add a git pre-commit hook or CI check that warns when .env contains any var not in the whitelist of expected env vars.

### [High] README and PKG-INFO reference nonexistent scripts/pipeline.py
- **File:** `README.md` (Lines: 44)
- **Effort:** XS
- **Description:** README.md line 44 and src/zentinull.egg-info/PKG-INFO both reference 'python scripts/pipeline.py' but that file does not exist. The pipeline was moved to serve.py CLI and src/zentinull/cli/pipeline.py. New users following quick-start instructions will get FileNotFoundError.
- **Fix:** Update README.md line 44 to 'python serve.py pipeline' and rebuild the egg-info (pip install -e .). All three doc sources (README.md, egg-info/PKG-INFO, installed METADATA) must be fixed.

### [High] CLI pipeline calls scripts/run_splink.py via subprocess instead of importing splink_runner directly
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 286-312)
- **Effort:** S
- **Description:** run_splink() launches 'python3 scripts/run_splink.py' as a subprocess rather than calling src/zentinull/resolve/splink_runner.run() directly. The script itself is a 14-line shim that does nothing except call splink_runner.run(). This adds subprocess overhead, breaks error tracebacks, loses type safety, and introduces an unnecessary layer of indirection that can silently fail if PYTHON path differs.
- **Fix:** Replace subprocess call with direct import: from zentinull.resolve.splink_runner import run as run_splink_direct. Delete scripts/run_splink.py or keep as a tiny dispatch. Also update the CLI pipeline function signature and docstring.

### [High] scripts/e2e_enrichment.py duplicates entire MANUAL_REGISTRY from src/zentinull/valentine.py
- **File:** `scripts/e2e_enrichment.py` (Lines: 41-81)
- **Effort:** XS
- **Description:** The 30+-entry MANUAL_REGISTRY dict is copy-pasted into both scripts/e2e_enrichment.py (lines 41-81) and src/zentinull/valentine.py (lines 26-57). The script does not import from valentine.py — it redefines its own copy. Any update to one will silently diverge from the other, causing mismatch between the registry used by the pipeline (valentine.py) vs the standalone script.
- **Fix:** Import from zentinull.valentine instead of redefining: from zentinull.valentine import MANUAL_REGISTRY. Remove the duplicate dict from e2e_enrichment.py.

### [High] _FEED_SOURCE_MAP defined identically in two modules — guaranteed drift risk
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 67-76)
- **Effort:** XS
- **Description:** The exact same _FEED_SOURCE_MAP dict (8 identical entries mapping feed keys to source column values) is defined in export_for_splink.py (lines 24-33) and again in cli/pipeline.py (lines 67-76). Adding a new feed requires updating BOTH copies or the pipeline silently breaks. The resolve/attach.py module imports from export_for_splink.py's copy, cli/render.py doesn't use either (another risk surface). This is a maintainability time bomb.
- **Fix:** Centralize _FEED_SOURCE_MAP in a single location — best candidate is export_for_splink.py (since attach.py already imports it). Then cli/pipeline.py should import that single copy instead of redefining its own.

### [High] Derived-field normalization logic duplicated across export and pipeline modules
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 180-214)
- **Effort:** S
- **Description:** The derived-field normalization pipeline (name_clean = normalize_name, mac_clean = normalize_mac, serial_number = normalize_serial, manufacturer.lower(), strip_sentinels, fill-missing-fields) is implemented identically in export_for_splink.py (export function, lines ~62-90) AND in cli/pipeline.py's export_source() function (lines ~192-214). This means a change to normalization logic must be applied in two places or one path silently produces different output.
- **Fix:** Extract the per-record normalization (derived field computation, sentinel stripping, field fill) into a shared function in export_for_splink.py that both codepaths call.

### [High] MeshDB._conn() creates read-write DuckDB connections for read-only queries
- **File:** `src/zentinull/api/db.py` (Lines: 46-49)
- **Effort:** S
- **Description:** MeshDB._conn() opens connections with read_only=False for every internal query method (lookup, search, batch_lookup, dashboard, mesh_stats, etc.). The mesh DuckDB file is only written by the pipeline — the API is read-only. Opening read-write connections risks accidental writes from the API layer and prevents concurrent readers (DuckDB allows multiple readers only with read_only=True).
- **Fix:** Change _conn() signature to accept a read_only parameter (default True for API use) and pass read_only=True from all read-only query methods. Only the load pipeline and incremental-sync paths should open read-write.

### [High] Lazy import of manifest inside hot-path _build_story on every device lookup
- **File:** `src/zentinull/api/db.py` (Lines: 293-295)
- **Effort:** XS
- **Description:** Every single GET /device/{query} call triggers from ..manifest import load_manifest and from ..resolve.sot import sot_resolve inside the _build_story method. The import itself is cached by Python's import system after first use, but this creates a hidden dependency from the hot API path to the manifest system — and the import trace is opaque to static analyzers.
- **Fix:** Hoist these imports to the top of db.py (they are already guarded by from __future__ import annotations). They are used unconditionally in _build_story, so there is no benefit to lazy loading them.

### [Medium] Duplicated _FEED_SOURCE_MAP definition across two modules
- **File:** `src/zentinull/export_for_splink.py` (Lines: 32-41)
- **Effort:** XS
- **Description:** The exact same _FEED_SOURCE_MAP dict (8 entries mapping feed_key→source value) is defined in both export_for_splink.py and cli/pipeline.py. Any source addition or rename requires updating both copies. The attach.py module also references the copy from export_for_splink, creating a fragile dependency.
- **Fix:** Move to a single shared location (e.g. config.py or manifest/__init__.py) and import from there. Add a test that the exported CSV source column values match known values from the manifest.

### [Medium] Triple-duplicated _resolve_dotted() helper
- **File:** `src/zentinull/manifest/walker.py` (Lines: 24-38)
- **Effort:** XS
- **Description:** The _resolve_dotted() function for traversing dotted paths (e.g. 'user.email') through dicts/list is independently implemented in walker.py:24, attach.py:103, and base.py:170. Each copy has slight variations (attach handles only dicts, walker handles lists via digit indices, base handles only dicts). This is a correctness risk when behavior drifts between copies.
- **Fix:** Extract to a shared utility module (e.g. manifest/transforms.py or a new utils.py) with full list/dict path support, then import in all three locations.

### [Medium] Duplicated pipeline lock orchestration pattern
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 808-850, 877-940)
- **Effort:** XS
- **Description:** The lock-file pattern (open→flock→write PID→finally cleanup) is duplicated verbatim in both run_incremental_sync() and run_pipeline(). The ~40-line lock acquisition + cleanup block is identical except for error messages. This is a maintenance hazard — any change to lock semantics must update both copies in sync.
- **Fix:** Extract into a context manager function (e.g. @contextmanager def pipeline_lock() yields) that handles open/flock/write/cleanup, then use 'with pipeline_lock():' in both functions.

### [Medium] export_source() duplicates export_for_splink export logic
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 217-278)
- **Effort:** S
- **Description:** export_source() in cli/pipeline.py independently implements per-source CSV export with the same SQLite→walker→normalize→write CSV flow already handled by export_for_splink.py's export(). The only difference is scope (single source vs all anchors). This creates a second maintenance surface for the same CSV transformation pipeline.
- **Fix:** Refactor export_for_splink.export() to accept an optional source filter parameter, then call it from export_source() instead of reimplementing.

### [Medium] _load_zbx_metrics() overly broad exception handling around JSON parsing
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 340-346)
- **Effort:** XS
- **Description:** The hosts raw_json parsing loop catches (json.JSONDecodeError, ValueError, TypeError) but the items loop has nested parsing in a for loop with no per-item exception handling. A single malformed item causes the entire metrics load to silently abort. Also uses bare except Exception in the outer _load_zbx_metrics flow.
- **Fix:** Add per-item try/except around the json.loads + field extraction so one bad item doesn't kill the batch. Or use the json module's ignore_decoding_errors approach if available.

### [Medium] skip_training parameter accepted but silently ignored
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 295-296)
- **Effort:** XS
- **Description:** run_splink() accepts a skip_training parameter but logs 'skip_training not yet implemented — running with defaults' and then proceeds to run the full training pipeline anyway. This is misleading for API consumers who pass skip_ingest=true and skip_training=true expecting a real shortcut.
- **Fix:** Either implement the skip behavior (emit warning and return early) or remove the parameter and raise ValueError if passed, forcing callers to understand it's unsupported.

### [Medium] _load_mesh_data misnamed — loads dashboard KPI, not mesh stats
- **File:** `dashboard.py` (Lines: 43-58)
- **Effort:** XS
- **Description:** The function _load_mesh_data (with docstring 'Load mesh stats via API') actually calls the /dashboard endpoint which returns DashboardStats (clusters/records/sources/coverage). The /mesh endpoint returns completely different data: MeshStats (by_source_count, by_source_combo, records_per_source). The names don't align — the function loads dashboard KPI data used for the top KPI cards and coverage table, not mesh statistical data.
- **Fix:** Rename to _load_dashboard_data and update the docstring to say 'Load dashboard KPI data from /dashboard endpoint'. The existing usage is correct (the returned data IS dashboard data), only the naming is misleading.

### [Medium] load_manifest() not cached via Streamlit — re-imports on every interaction
- **File:** `dashboard.py` (Lines: 228-230)
- **Effort:** XS
- **Description:** The load_manifest() call in the render path (freshness section) is called on every Streamlit script re-run — every button click, every keystroke in the search box, every 30s auto-refresh. Python's sys.modules cache avoids full re-import, but the module-level code re-instantiates all dataclasses and the _validate() call re-imports strategy registries. This is wasteful for a frozen config that never changes during a dashboard session.
- **Fix:** Use @st.cache_resource on load_manifest or a wrapper, so the Manifest object is computed once and reused across all re-renders.

### [Medium] Stale cache after pipeline run — individual stage buttons only clear partial caches
- **File:** `dashboard.py` (Lines: 108-111, 119-120, 127, 133-134)
- **Effort:** XS
- **Description:** After triggering individual stages, the cache-clearing logic is inconsistent: Ingest button clears _load_status only; Splink button clears _load_status only; Load button clears both _load_status AND _load_mesh_data. But the Export button clears nothing at all. After Export completes, the 'Data freshness' section won't update until the 30s ttl expires or another action clears it. After Splink completes, the mesh stats won't update until the user also triggers Load or waits 30s.
- **Fix:** Call _load_status.clear() AND _load_mesh_data.clear() after every successful stage run. The status is always updated by record_done() regardless of stage, and mesh data depends on Load but is cheap to refetch.

### [Medium] Coverage values are opaque formatted strings — not sortable in dataframe
- **File:** `dashboard.py` (Lines: 251-253)
- **Effort:** S
- **Description:** The API returns coverage as strings like '123/456 (27%)' from the /dashboard endpoint. These are displayed via st.dataframe as plain text, making them unsortable and un-filterable. Users cannot sort by coverage percentage or compare fields numerically. The same data could be shown as a progress bar or as separate numeric columns.
- **Fix:** Display coverage as st.progress bars alongside the label, or parse the percentage component for display. Alternatively, add a separate API call or expand the dashboard endpoint to return structured {filled, total, pct} coverage dictionaries.

### [Medium] .env.example missing 13 env vars read by config.py and worker.py
- **File:** `.env.example` (Lines: 62-69)
- **Effort:** S
- **Description:** config.py reads 38 env vars; .env.example only documents ~22. Missing from template: ZENTINULL_PROJECT (read by resolve_paths and manifest loader), PORT (fallback for ZENTINULL_PORT), SPLINK_PREDICT_THRESHOLD, SPLINK_U_MAX_PAIRS, SPLINK_LAMBDA_RECALL, SPLINK_SWEEP_THRESHOLDS, FH_RETENTION_DAYS, ZOMBIE_STALE_DAYS, ZENTINULL_SCHED_ZBX/FG/ME/SDP/AD/SP/SPLINK (7 worker schedule env vars from worker.py lines 12-19), ZENTINULL_LOG_PRETTY, ZENTINULL_LOG_STYLE, ZENTINULL_LOG_RULES, ZENTINULL_LOG_SHOW, ZENTINULL_LOG_FORMATS, ZENTINULL_LOG_COMPACT_WIDTH, ZENTINULL_LOG_COLUMN_MAP, ZENTINULL_LOG_COMPACT_FORMATS, and SSH_PASS (used by scripts/tunnel.sh). New deployments require manually guessing or spelunking code for these.
- **Fix:** Add all missing env vars to .env.example with sensible placeholder defaults, grouped by section (Project, Splink, Worker Schedule, Logging, SSH Tunnel). Mark each with a comment noting which module reads it.

### [Medium] Config dataclass default for fg_base_url is literal placeholder that differs from get_config() computed default
- **File:** `src/zentinull/config.py` (Lines: 143, 223)
- **Effort:** XS
- **Description:** The Config dataclass declares fg_base_url: str = 'https://_fg_host_:8443' (a literal placeholder string), but get_config() computes it as f'https://{os.environ.get("FG_HOST", "fg.example.com")}:{os.environ.get("FG_PORT", "8443")}'. The dataclass default is NEVER used because get_config() always provides the value, but if someone constructs Config(paths=...) without calling get_config(), they get a broken URL 'https://_fg_host_:8443'. Also, the fallback host differs ('fg.example.com' vs '_fg_host_').
- **Fix:** Align the dataclass default with the get_config() computed default: change to fg_base_url: str = 'https://fg.example.com:8443' or compute via a helper that both the dataclass default and get_config() use. Add a comment noting this field is computed at config-time from FG_HOST + FG_PORT.

### [Medium] Global singleton Metrics object with thread-unsafe iteration in generate()
- **File:** `src/zentinull/api/metrics.py` (Lines: 1-130)
- **Effort:** S
- **Description:** metrics = Metrics() is a module-level singleton. The generate() method iterates _values dicts while holding per-counter locks, but the helper classes _LabeledCounter and _LabeledHistogram only lock during inc()/observe(), not during generate(). A concurrent inc() could mutate _values during iteration by generate(), causing a RuntimeError: dictionary changed size during iteration. The Prometheus scrape endpoint calls generate() which iterates the values dicts.
- **Fix:** Add a global read-write lock or copy-on-read: in generate(), deep-copy each _values dict under the per-counter lock before iterating it outside the lock. Alternatively, switch to the official prometheus_client library.

### [Medium] Module-level dicts computed at import time in cli/pipeline.py cannot handle project switching
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 58-76)
- **Effort:** S
- **Description:** _SOURCE_MAP, _SOURCE_TO_TABLES, and _FEED_SOURCE_MAP are computed as module-level constants when pipeline.py is first imported. If ZENTINULL_PROJECT changes between invocations (e.g., in tests), these stale dicts reflect the original project's manifest. The _get_manifest() cached wrapper compounds this — it caches the manifest forever regardless of project switches.
- **Fix:** Replace module-level dicts with lazy function calls or per-invocation computation inside the run functions. Remove the _get_manifest._cache pattern or key it by project name.

### [Medium] Resolve_connect import of transforms REGISTRY is lazy inside hot loop
- **File:** `src/zentinull/resolve/attach.py` (Lines: 137-138)
- **Effort:** XS
- **Description:** from ..manifest.transforms import REGISTRY as TRANSFORM_REGISTRY is imported lazily inside resolve_normalized(), which is called for every attachment record with a 'normalized' link strategy. With thousands of attachment records, this import is re-executed (though cached by sys.modules after first hit). The hidden dependency from the attachment resolver to the transforms module is invisible to static analysis.
- **Fix:** Hoist the import to the top of attach.py. It's used conditionally but the module is already imported in practice by every caller.

### [Medium] Inline contextlib imports in critical pipeline paths — fragile error handling
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 732, 899, 991)
- **Effort:** XS
- **Description:** import contextlib appears inside try/finally blocks and conditional branches three times in cli/pipeline.py (around lines 732, 899, 991). This obscures the dependency on contextlib from static analysis and risks ImportError at the worst possible moment. These are all inside cleanup/finally paths, so a failed import would leak resources.
- **Fix:** Hoist import contextlib to the top of the module alongside the other stdlib imports.

### [Medium] _resolve_dotted() implemented independently in three separate modules
- **File:** `src/zentinull` (Lines: manifest/walker.py:26-41, ingestors/base.py:175-183, resolve/attach.py:74-82)
- **Effort:** S
- **Description:** The _resolve_dotted() helper function is defined independently in manifest/walker.py, ingestors/base.py, and resolve/attach.py. All three perform the same dotted-path resolution against a dict. The walker version also supports list indexing (numeric segments). The base.py and attach.py versions only handle dict traversal, silently returning None for list indices.
- **Fix:** Extract _resolve_dotted into a shared utility module (e.g., src/zentinull/util/dotted.py or just src/zentinull/normalizer.py) and have all three modules import from it.

### [Medium] Per-feed SQLite connection in run_feed() but shared connection in run_system() — inconsistent
- **File:** `src/zentinull/ingest/runner.py` (Lines: 68-72, 135-140)
- **Effort:** S
- **Description:** run_feed() (line 68) opens and closes a SQLite connection per feed call via base.db(feed.system) and conn.close(). Meanwhile run_system() (line 135) opens ONE SQLite connection and reuses across all feeds for that system. The single-feed path (run_feed) is wasteful when called multiple times for the same system.
- **Fix:** Have run_feed() accept an optional existing connection parameter. When running feeds within run_system(), pass the shared connection. The standalone run_feed() can open its own.

### [Medium] Export duplicates field extraction pipeline between export_for_splink.py and cli/pipeline.py
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 180-214)
- **Effort:** S
- **Description:** cli/pipeline.py's export_source() replicates the entire source-SQLite-connect → raw-rows → walk_feed → normalize-derived-fields → strip-sentinels → fill-missing pipeline that export_for_splink.py's export() already implements for all sources. export_source() only adds single-source filtering and per-source CSV output.
- **Fix:** Replace export_source() to call export_for_splink.export() with a source filter parameter, or make export() accept an optional source filter and write individual CSVs. Duplicating 30+ lines of extraction logic is fragile and high-maintenance.

### [Medium] Lazy import of worker inside server lifespan — opaque startup dependency
- **File:** `src/zentinull/api/server.py` (Lines: 28-29)
- **Effort:** XS
- **Description:** from ..worker import loop as worker_loop is imported lazily inside _scheduler_loop() instead of at module level. The comment says 'Mirrors the pattern from sentinull' but sentinull's pattern serves to break a genuine circular import. There is no such cycle here — worker.py imports manifest and config, not server.
- **Fix:** Hoist the import to the top of server.py for clarity and static-analysis transparency.

### [Medium] Lazy import of pipeline inside router endpoint obscures dependency chain
- **File:** `src/zentinull/api/router.py` (Lines: 83-84)
- **Effort:** XS
- **Description:** from ..cli.pipeline import run_pipeline is imported lazily inside the /pipeline/run endpoint handler. There is no circular import risk because pipeline.py does not import from api/ — the lazy import only hides the dependency from static analysis and makes the first pipeline trigger noticeably slower.
- **Fix:** Hoist to the top-level imports in router.py.

### [Medium] Export 'flat' query uses f-string SQL — SQL injection risk via table name
- **File:** `src/zentinull/export_for_splink.py` (Lines: 62)
- **Effort:** XS
- **Description:** conn.execute(f"SELECT * FROM {table_name}") constructs SQL via string interpolation with feed.store values sourced from the manifest. While currently safe (manifest values are trusted), this is a latent injection point if manifest loading ever accepts user input or if feed.store is derived from external data.
- **Fix:** Use duckdb or sqlite3 parameterized identifiers or validate table_name against a whitelist before executing. A minimum fix: assert table_name.isidentifier() before the f-string.

## Phase 3: Structural (M+ effort Refactors)
### [High] Blocking subprocess.run in button handlers freezes Streamlit UI
- **File:** `dashboard.py` (Lines: 86-112)
- **Effort:** L
- **Description:** The _run_serve function for individual stages (ingest, export, splink, load) uses subprocess.run() with timeout=600 (10 minutes) inside Streamlit button callbacks. Streamlit is single-threaded for script execution — this blocks the entire UI for up to 10 minutes with no responsive feedback beyond the spinner. The user cannot interact with any other dashboard element while a stage runs.
- **Fix:** Replace subprocess.run with asyncio subprocess or run stages via API POST endpoints (like pipeline does). If API endpoints don't exist for individual stages, add them to router.py. At minimum, run subprocess in a thread via loop.run_in_executor and poll for completion, or launch as fire-and-forget and show status via the /dashboard API's status polling.

### [Medium] Oversized file: api/db.py at 1234 lines
- **File:** `src/zentinull/api/db.py` (Lines: 1)
- **Effort:** M
- **Description:** MeshDB class is 1234 lines with 18 methods covering device lookup, search, dashboard stats, mesh stats, anomalies, metrics, events, attachments, VLANs, device trace, and SOT resolution. The class has too many responsibilities — it's both the query layer and the business logic layer.
- **Fix:** Split into separate classes/modules: MeshDB core (CRUD), MeshStats (dashboard/anomalies/aggregations), DeviceTrace (trace/linked-by-user/attachments/graph), and TimeSeries (metrics/events). Each module < 400 lines.

### [Medium] Oversized file: cli/pipeline.py at 997 lines
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 1)
- **Effort:** M
- **Description:** The pipeline orchestrator file contains 7 independent stage functions (ingest, export, splink, load, valentine, attach, incremental) plus the orchestration wrappers, all at module level. Several functions have 50+ line bodies with deeply nested try/except blocks.
- **Fix:** Extract each pipeline stage into its own module under cli/stages/ (ingest.py, export.py, splink.py, load.py, valentine.py, attach.py). Keep pipeline.py as the thin orchestration coordinator that calls each stage module.

### [Low] CI pipeline has no GitHub Secrets configured — no integration tests possible
- **File:** `.github/workflows/ci.yml` (Lines: 1-82)
- **Effort:** L
- **Description:** The CI workflow (4 jobs: lint, typecheck, test, benchmark) uses zero GitHub Secrets and sets zero env vars for authentication. All tests run with default placeholder credentials that fail against any real API. The test suite (pytest) only exercises mocks/unit tests. Integration/regression tests against staging sources with limited-scope credentials are impossible without adding secrets to the CI environment.
- **Fix:** Option A (recommended): Add a CI-only 'staging' environment with limited-read API credentials stored as GitHub Secrets (ZBX_TOKEN_STAGING, FG_API_KEY_STAGING, etc.), add a 'integration' CI job that runs against staging sources with a schedule trigger (not on every PR). Option B: Document intentionally that CI is unit-test-only and add a separate nightly integration workflow.

## Phase 4: Coverage (Test Gaps)
### [High] Untested module: src/zentinull/worker.py — scheduled background worker has zero test coverage
- **File:** `src/zentinull/worker.py` (Lines: 1-128)
- **Effort:** L
- **Description:** The background scheduler module (WorkerState, loop(), dry_run()) that manages per-source incremental sync intervals and daily Splink runs has no dedicated tests. Contains async loop logic with signal handling, state management, and schedule computation.
- **Fix:** Add tests/unit/test_worker.py covering WorkerState.should_run(), loop() start/stop/scheduling behavior (mocked asyncio), and dry_run() output.

### [High] Untested module: src/zentinull/ingest/auth_factory.py — auth object construction has no dedicated tests
- **File:** `src/zentinull/ingest/auth_factory.py` (Lines: 1-58)
- **Effort:** M
- **Description:** build_auth() maps manifest Auth configs to concrete auth classes (APIKeyAuth, OAuth2RefreshAuth, LDAPBindAuth). Contains conditional logic for config resolution (ZBX_TOKEN vs FG_HOST, SDP vs ME OAuth). Only implicitly tested via integration in test_ingest_mock.py which uses kind='none'.
- **Fix:** Add tests/unit/test_auth_factory.py with unit tests for build_auth covering api_key, oauth_refresh (SDP vs ME paths), ldap, and 'none' kinds.

### [High] Critical untested scripts: dq.py, run_splink.py, seed_demo_data.py, e2e_enrichment.py, hw_extract_batch.py, build_training_set.py, run_ingest.py
- **File:** `scripts/` (Lines: (7 scripts with zero test coverage))
- **Effort:** XL
- **Description:** Seven scripts in scripts/ have no test coverage at all: dq.py (314 lines, interactive DQ shell with URL I/O, cmd.Cmd subclass), run_splink.py (639 bytes wrapper), seed_demo_data.py (13KB), e2e_enrichment.py (21KB, full end-to-end pipeline with API calls), hw_extract_batch.py (5KB), build_training_set.py (5KB), run_ingest.py (544 bytes). Only bench.py and bench_api.py have test coverage (test_bench_scripts.py). The serve.py cmd_seed and cmd_bench_api mock-test these at the dispatcher level only.
- **Fix:** Prioritize tests for scripts/run_splink.py (critical pipeline path) and scripts/seed_demo_data.py (demo data generation). Add integration tests for scripts/e2e_enrichment.py. dq.py can be lower priority given its interactive nature.

### [Medium] Untested module: src/zentinull/ingest/strategies/paged_json_detail.py — two-phase detail enrichment strategy has zero test coverage
- **File:** `src/zentinull/ingest/strategies/paged_json_detail.py` (Lines: 1-142)
- **Effort:** M
- **Description:** The paged_json_detail_fetch() strategy (used by ManageEngine) is registered but has no tests. Contains two-phase pagination + per-record detail HTTP calls, time delays, and optional secondary detail call logic.
- **Fix:** Add tests in tests/ingestors/test_ingest_mock.py covering paged_json_detail_fetch: list+detail merge, pagination modes, detail_max limiting, secondary detail call, error handling.

### [Medium] Untested module: src/zentinull/api/metrics.py — Prometheus-format metrics collector has no tests
- **File:** `src/zentinull/api/metrics.py` (Lines: 1-145)
- **Effort:** S
- **Description:** The Metrics class with _Counter, _Histogram, _LabeledCounter, _LabeledHistogram and generate() output has no tests. Contains thread-safe locking logic and label-based metric emission.
- **Fix:** Add tests/test_api_metrics.py covering counter inc(), histogram observe() bucket assignment, labels(), generate() Prometheus output format.

### [Medium] Untested module: src/zentinull/cli/render.py — brutalist log renderer has no tests
- **File:** `src/zentinull/cli/render.py` (Lines: 1-165)
- **Effort:** S
- **Description:** The rich-powered terminal renderer (render_stage, render_banner, render_line, render_lines, _color_keyval) has no tests despite containing regex parsing and formatting logic.
- **Fix:** Add tests/test_cli_render.py covering _render_line with various severity levels, _color_keyval regex, render_stage/stage rule output.

### [Medium] Untested module: src/zentinull/valentine.py — Valentine schema-matching layer has no tests
- **File:** `src/zentinull/valentine.py` (Lines: 1-142)
- **Effort:** L
- **Description:** The Valentine auto-matching module (_load_source_dfs, run_valentine, _flatten, _save_registry, connected-component clustering) has no tests despite its complexity and database I/O.
- **Fix:** Add tests/test_valentine.py covering: _flatten nested dict flattening, MANUAL_REGISTRY fallback when library missing, connected component building, registry merge.

### [Medium] Untested module: src/zentinull/ingest_adapter.py — parallel ingest orchestration has partial coverage
- **File:** `src/zentinull/ingest_adapter.py` (Lines: 1-94)
- **Effort:** M
- **Description:** run_ingest() with ThreadPoolExecutor, _ingest_one_system error isolation, single-system fast path, and get_system_label/get_all_system_labels have no dedicated tests. Only tested indirectly through pipeline orchestration mocks.
- **Fix:** Add tests/test_ingest_adapter.py covering: parallel execution with multiple systems, single-system pool bypass, per-system error isolation, label functions.

### [Medium] Untested module: src/zentinull/manifest/walker.py — manifest field extraction walker has no dedicated tests
- **File:** `src/zentinull/manifest/walker.py` (Lines: 1-205)
- **Effort:** M
- **Description:** walk_feed(), _extract_field(), _resolve_dotted(), _extract_source_id(), _flatten_raw(), _collect_extra_attributes() have no dedicated unit tests. Only implicitly tested through the export integration tests.
- **Fix:** Add tests/test_manifest_walker.py covering: walk_feed with multi-path specs, _resolve_dotted with list indices, _extract_field with transforms, comma-joined multi-MAC values, _collect_extra_attributes with _SYSTEM_COLS exclusion.

### [Low] Untested module: src/zentinull/manifest/transforms.py — transform registry has no tests
- **File:** `src/zentinull/manifest/transforms.py` (Lines: 1-40)
- **Effort:** XS
- **Description:** The transform registry (employee_name_from_url, etc.) is small (~40 lines) but has no dedicated unit tests. Only the employee_name_from_url transform has tests in test_manifest.py.
- **Fix:** Add tests covering each registered transform function in tests/test_manifest.py or a separate tests/test_manifest_transforms.py.

### [Low] Untested function: config.py validate_config() — config validation path has no tests
- **File:** `src/zentinull/config.py` (Lines: 162-179)
- **Effort:** XS
- **Description:** validate_config() tests data_dir existence, mesh parent, and OAuth file path validation but has no test coverage.
- **Fix:** Add tests for validate_config() covering: missing data_dir warning, missing mesh parent, optional oauth file warnings.

### [Low] CI gap: coverage threshold not enforced in CI pipeline
- **File:** `.github/workflows/ci.yml` (Lines: 45-47)
- **Effort:** XS
- **Description:** The CI test step runs pytest --cov but does not enforce a minimum coverage threshold (e.g. --cov-fail-under=80). Coverage can regress without CI catching it.
- **Fix:** Add --cov-fail-under=70 to the pytest invocation in CI. Start with 70% as a baseline that allows for uncovered optional modules.

### [Low] CI gap: mypy runs only on src/zentinull, skipping tests and scripts
- **File:** `.github/workflows/ci.yml` (Lines: 30-32)
- **Effort:** XS
- **Description:** The typecheck CI step runs `mypy src/zentinull/` but does not check tests/ or scripts/. Type errors in test helpers or script modules are invisible.
- **Fix:** Add parallel mypy run for tests/: `mypy tests/ --ignore-missing-imports` to the typecheck step.

### [Low] Duplicated test helper: _make_paths defined in 7 separate test files
- **File:** `tests/cli/conftest.py` (Lines: 14-25)
- **Effort:** S
- **Description:** The _make_paths() helper is independently defined in tests/cli/conftest.py, test_backup.py, test_db_mgmt.py, test_pipeline.py, test_status_api.py, test_audit_mapping.py, test_export.py, and test_serve.py. Each duplicates ProjectPaths construction logic. This creates maintenance burden when ProjectPaths fields change.
- **Fix:** Move _make_paths to a shared conftest fixture or helper module (e.g. tests/helpers.py) and import it in all consumer test files.

### [Low] Duplicated test helper: _make_args defined in 2 test files
- **File:** `tests/test_audit_mapping.py` (Lines: 99-101)
- **Effort:** XS
- **Description:** The _make_args() helper that builds argparse.Namespace is duplicated in test_audit_mapping.py and test_serve.py.
- **Fix:** Extract _make_args into a shared test helper module and import in both files.

### [Trivial] Test duplication: _fmt_bytes tested in both test_backup.py and test_format_helpers.py
- **File:** `tests/cli/test_backup.py` (Lines: 37-71)
- **Effort:** XS
- **Description:** The _fmt_bytes helper from cli/backup.py is tested in both test_backup.py (class TestFmtBytes, 7 tests with 0/512/1536/1048576/1073741824/2199023255552) and test_format_helpers.py (6 tests with 500/2048/5242880/1073741824/2199023255552/0). These cover nearly identical boundaries.
- **Fix:** Remove the redundant _fmt_bytes tests from test_format_helpers.py (keep _fmt_size tests there, which are unique to db_mgmt.py). Keep the TestFmtBytes class in test_backup.py.

## Phase 5: Documentation
### [High] README claims '12 REST endpoints' — actual is 19
- **File:** `README.md` (Lines: project structure section, `api/router.py` line)
- **Effort:** XS
- **Description:** The README project structure says `router.py: 12 REST endpoints` but router.py now has 19 `@router.get/post` decorators: /health, /pipeline/run, /device/{query}, /device/{query}/trace, /batch, /search, /dashboard, /mesh, /clusters, /anomalies, /diagnostics/unmapped-fields, /device/{query}/metrics, /device/{query}/timeline, /device/{query}/attachments, /device/{query}/history, /device/{query}/stats, /device/{query}/metric-summary, /device-view, /metrics. The count hasn't been updated as endpoints were added.
- **Fix:** Update `12 REST endpoints` to `19 REST endpoints` in the project structure tree in README.md.

### [High] README says '6 frozen Pydantic models' — actual is 21
- **File:** `README.md` (Lines: project structure `api/models.py` line)
- **Effort:** XS
- **Description:** The README project structure shows `models.py: 6 frozen Pydantic models` but src/zentinull/api/models.py defines 21 BaseModel classes (SourceRecord, ClusterInfo, DeviceStory, MeshStats, DashboardStats, AnomaliesReport, MetricRecord, EventRecord, DeviceMetricsResponse, DeviceTimelineResponse, AttachmentRecord, DeviceAttachmentsResponse, ClusterListResponse, MetricLatest, MetricAggregate, DeviceStatsBlock, DeviceStatsResponse, DeviceMetricSummaryResponse, HistoryEntry, DeviceHistoryResponse, DeviceTraceResponse).
- **Fix:** Update `6 frozen Pydantic models` to `21 frozen Pydantic models` in README.md project structure.

### [High] README claims '448+ tests, 92% coverage' — AGENTS.md says 675 tests
- **File:** `README.md` (Lines: Quality table)
- **Effort:** XS
- **Description:** The README quality table says `448+ tests, 92% coverage` but AGENTS.md says `675 tests across 38 test files`. The 448+ count is stale — tests have grown significantly since it was written. AGENTS.md also says 92% coverage target which aligns, but the test count doesn't.
- **Fix:** Update test count from `448+ tests` to `675+ tests` or run `pytest --collect-only` and use the actual count. Update the README quality table.

### [Medium] README references deleted file 'src/zentinull/pipeline.py' in project structure
- **File:** `README.md` (Lines: 82-83)
- **Effort:** XS
- **Description:** The project structure tree shows `├── pipeline.py         # Original pipeline orchestrator (subprocess)` under `src/zentinull/`. This file no longer exists — the original pipeline.py was replaced by `src/zentinull/cli/pipeline.py`. The README tree still lists it as if it's present.
- **Fix:** Remove the `pipeline.py` entry from the project tree or replace with a note that it was moved to `cli/pipeline.py`.

### [Medium] README Quick Start references missing script 'scripts/pipeline.py'
- **File:** `README.md` (Lines: 44)
- **Effort:** XS
- **Description:** The 'Quick start (native)' section says `python scripts/pipeline.py` but this script does not exist in the project. pipeline functionality is now accessed via `python serve.py pipeline` instead.
- **Fix:** Replace `python scripts/pipeline.py` with `python serve.py pipeline` in the native quick-start instructions.

### [Medium] AGENTS.md claims '18 frozen Pydantic response models' — actual is 21
- **File:** `AGENTS.md` (Lines: 352)
- **Effort:** XS
- **Description:** AGENTS.md 'Important Files' table says `src/zentinull/api/models.py | 18 frozen Pydantic response models`. The actual file contains 21 BaseModel subclasses. Three models were added since the count was written.
- **Fix:** Update to `21 frozen Pydantic response models` in AGENTS.md important files table.

### [Medium] AGENTS.md references nonexistent endpoint '/device/{query}/vlans'
- **File:** `AGENTS.md` (Lines: 350)
- **Effort:** XS
- **Description:** AGENTS.md says `src/zentinull/api/router.py | 15+ REST endpoints ... /device/{query}/vlans via device_vlans()`. There is no router endpoint at /device/{query}/vlans. The device_vlans() method exists in db.py and is called internally by device_trace(), but it cannot be accessed as a standalone REST endpoint. This is misleading documentation.
- **Fix:** Remove the `/device/{query}/vlans` path from the router.py description in AGENTS.md. Either note that VLAN info is returned inside `/device/{query}/trace` response, or add the endpoint if intended.

### [Medium] AGENTS.md says '6 ATTACHMENT feeds' — manifest has 7 ATTACHMENT feeds
- **File:** `AGENTS.md` (Lines: attach stage description and feeds list)
- **Effort:** XS
- **Description:** AGENTS.md architecture table says `6 ATTACHMENT feeds` but projects/default/manifest.py actually defines 7 ATTACHMENT feeds: zbx_items, sdp_requests, sp_employees, sp_accountinfo, sp_devicenotes, sp_employeedocs (n8n-based), and sp_ComponentPurchases. sp_employeedocs was added and the count wasn't updated.
- **Fix:** Update '6 ATTACHMENT feeds' to '7 ATTACHMENT feeds' in AGENTS.md architecture table.

### [Medium] AGENTS.md stale '15+ REST endpoints' claim — actual is 19
- **File:** `AGENTS.md` (Lines: 350)
- **Effort:** XS
- **Description:** AGENTS.md says `15+ REST endpoints` in the important files table for router.py. The actual count is 19 router-decorated endpoints. While '15+' is technically still true and includes the '+', it's imprecise and should be updated to the actual count.
- **Fix:** Update `15+ REST endpoints` to `19 REST endpoints` in AGENTS.md.

### [Low] AGENTS.md claims '5-stage pipeline' — actual pipeline has 6 stages plus valentine
- **File:** `AGENTS.md` (Lines: Architecture & Data Flow section)
- **Effort:** S
- **Description:** AGENTS.md pipeline diagram shows 5 stages: ingest → export → splink → load → attach. Memory notes and code show the pipeline actually has 6+ stages: ingest → export → splink → validate/load → valentine → attach. The valentine stage (auto field registry discovery) was inserted between load and attach. Also, memory notes indicate a 'discover' stage after load.
- **Fix:** Update the pipeline flow diagram and stage table in AGENTS.md to reflect the valentine/discover stage and validate stage between splink and load.

### [Low] AGENTS.md live state counts are stale (740 clusters, 10,725 attachments)
- **File:** `AGENTS.md` (Lines: Project Overview paragraph)
- **Effort:** XS
- **Description:** AGENTS.md says `740 resolved clusters, 10,725 attachment links, 8,491 Zabbix metrics loaded`. These static counts describe one snapshot in time and will drift as the pipeline runs. The document should note they're approximate or mark them as a snapshot.
- **Fix:** Add a caveat like 'As of last full pipeline run' to the state counts, or use relative language like '~740 clusters' and note they reflect the last snapshot.

### [Low] DATA_CORRELATION.md endpoint table omits 6 REST endpoints present in router
- **File:** `DATA_CORRELATION.md` (Lines: 10-52)
- **Effort:** S
- **Description:** DATA_CORRELATION.md's endpoint reference tables cover /device/{q}, /device/{q}/trace, /batch, /device/{q}/metrics, /device/{q}/metric-summary, /device/{q}/stats, /device/{q}/timeline, /device/{q}/attachments, /search, /clusters, /anomalies, /dashboard, /mesh — but omits /health, /diagnostics/unmapped-fields, /device/{query}/history, /pipeline/run, /device-view, and /metrics (Prometheus). Readers using this doc as a reference won't know about those endpoints.
- **Fix:** Add the missing endpoints to the surface contract tables in DATA_CORRELATION.md.

### [Low] DATA_CORRELATION.md uses {q} param name in endpoint docs but router uses {query}
- **File:** `DATA_CORRELATION.md` (Lines: 13-14, 23-25)
- **Effort:** XS
- **Description:** The DATA_CORRELATION.md endpoint reference table shows paths like `/device/{q}` and `/device/{q}/trace` but the actual router uses `/device/{query}` as the path parameter name. This is a minor naming inconsistency that could cause confusion when mapping docs to code.
- **Fix:** Rename `{q}` to `{query}` in all DATA_CORRELATION.md endpoint paths to match the actual router parameter naming.

### [Low] DATA_CORRELATION.md claims '31 enriched concepts' but valentine field registry may have more
- **File:** `DATA_CORRELATION.md` (Lines: 102-136)
- **Effort:** S
- **Description:** DATA_CORRELATION.md lists exactly 31 enriched concepts but the actual field_registry_auto.json and manual registry may have evolved. This count should be verified against the current field registry.
- **Fix:** Check the current `data/field_registry_auto.json` and manual registry to verify the enriched concept count, or reference the live registry dynamically.

### [Trivial] FIELD_MAPPING_AUDIT_REPORT.md date is 2026-07-14 — may be stale after pipeline changes
- **File:** `FIELD_MAPPING_AUDIT_REPORT.md` (Lines: 1-2)
- **Effort:** S
- **Description:** The field mapping audit report is dated 2026-07-14 and covers the original manifest pipelines. Since then, several field paths have been corrected (zbx serial/mac paths, me_ec/manufacturer/model fallbacks, me_mdm field name fixes) and the valentine enrichment layer was added. The report's specific path-mismatch findings may no longer reflect the current state of the code.
- **Fix:** Re-run the audit-mapping tool and regenerate the report, or add a disclaimer about staleness and a last-verified date.

## Phase 6: Housekeeping (Sprawl, Artifacts)
### [Medium] scripts/run_ingest.py is a trivial shim duplicating serve.py ingest
- **File:** `scripts/run_ingest.py` (Lines: 1-17)
- **Effort:** XS
- **Description:** This 17-line script just calls run_ingest() and logs results. It is fully subsumed by 'python serve.py ingest' which does the same thing with source/skip filtering. Having a separate script creates two code paths to maintain and confuses users about which to use.
- **Fix:** Either delete run_ingest.py and update docs/README to say 'python serve.py ingest', or turn it into a thin CLI that delegates to serve.py. Update all Makefile targets (run-ingest) and README references.

### [Medium] scripts/run_splink.py adds zero value as a separate script
- **File:** `scripts/run_splink.py` (Lines: 1-16)
- **Effort:** XS
- **Description:** This 14-line script imports splink_runner.run() and calls it with hardcoded paths. It is a duplicate of 'python serve.py splink' which also runs splink via the CLI pipeline module. The only difference is this script handles the .tmp CSV fallback inline while the CLI pipeline does not.
- **Fix:** Consolidate the .tmp CSV fallback into splink_runner.run() or the CLI pipeline, then delete scripts/run_splink.py. Update Makefile run-splink target.

### [Medium] serve.py directly imports from scripts/ creating circular package boundary
- **File:** `serve.py` (Lines: 370-380)
- **Effort:** M
- **Description:** serve.py imports cmd_seed from scripts.seed_demo_data and cmd_bench from scripts.bench, making the unified CLI depend on ad-hoc scripts. This means scripts/ is treated both as a separate entry-points directory AND as an importable module by the main CLI, creating a muddy boundary where scripts are expected to be both standalone executables and importable utilities.
- **Fix:** Move seed_demo_data(), bench(), bench_api() logic into src/zentinull/cli/ as subcommands (e.g., cli/seed.py, cli/bench.py) and import from there. Keep scripts/ as standalone entry points that import from the CLI module if needed.

### [Medium] data/pipeline.log grows unboundedly (1.9MB and growing every run)
- **File:** `data/pipeline.log` (Lines: 1-10)
- **Effort:** S
- **Description:** The pipeline log file at data/pipeline.log is 1.9MB with 16,000+ lines spanning July 12-23 with no rotation. Every ingest/export/splink/load run appends to the same file. This will eventually fill disk on long-running deployments, especially in Docker where the log is persisted in the data volume.
- **Fix:** Add log rotation (e.g., RotatingFileHandler with maxBytes=5MB, backupCount=3) to logging_config.py setup(), or add a --log-rotation option and a cleanup subcommand to serve.py. Alternatively, recommend logrotate for production.

### [Medium] scripts/__pycache__/ contains cached bytecode from 3 different Python versions
- **File:** `scripts/__pycache__/` (Lines: multiple)
- **Effort:** XS
- **Description:** The scripts/__pycache__ directory has .pyc files for Python 3.12, 3.13, and 3.14 (multiple versions per script). This is a sign of the project being developed across different Python installations, and stale cached bytecodes may mask import errors or cause 'wrong version' interpreter errors.
- **Fix:** Run 'find . -type d -name __pycache__ -exec rm -rf {} +' and update .gitignore to ensure __pycache__ is universally ignored. Add '**/__pycache__/' to .gitignore if not fully covered.

### [Medium] stale planning documentation cluttering project root
- **File:** `PLAN_SENTINEL_PORT.md` (Lines: 1-22)
- **Effort:** S
- **Description:** PLAN_SENTINEL_PORT.md (44KB), FIELD_MAPPING_AUDIT_REPORT.md (24KB), DATA_CORRELATION.md (22KB), and IDEAS_IMPLEMENTATION.md (17KB) are planning/audit docs from July 12-16 that refer to the Phase 0-7 refactor which is now committed. They are stale — the refactor is done, field mappings have been updated, and new features (Valentine, enriched views, mesh trace) are not reflected. These docs add cognitive load for every new developer reading the project root.
- **Fix:** Move planning docs to a planning/ or archive/ directory, or delete them. For FIELD_MAPPING_AUDIT_REPORT.md specifically, either regenerate it against current code or archive it with a note about its date.

### [Medium] splink/models/ and splink/output/ are empty directories
- **File:** `splink/` (Lines: both dirs)
- **Effort:** XS
- **Description:** The splink/models/ and splink/output/ directories under the project root are completely empty. They appear to be remnants of an earlier Splink output structure — actual Splink output now goes to export/splink_output/ and models are stored per-manifest by splink_runner.
- **Fix:** Remove empty directories: rmdir splink/models splink/output and splink/ itself if empty after.

### [Low] Dead code: render_separator() in cli/render.py
- **File:** `src/zentinull/cli/render.py` (Lines: 154-159)
- **Effort:** XS
- **Description:** render_separator() is defined with full implementation but never imported or called anywhere in the codebase.
- **Fix:** Remove the function and its import. If needed later, git history preserves it.

### [Low] Dead code: stream_command() in cli/streaming.py
- **File:** `src/zentinull/cli/streaming.py` (Lines: 141-154)
- **Effort:** XS
- **Description:** stream_command() is a convenience wrapper over run_streaming() that is never imported or called from any module. Its docstring example duplicates the function it wraps.
- **Fix:** Remove. All callers use run_streaming() directly.

### [Low] Dead code: _requires_device() method in dq.py
- **File:** `scripts/dq.py` (Lines: 72-76)
- **Effort:** XS
- **Description:** _requires_device() is defined as a decorator pattern ('Returns fn if device loaded, else prints error') but is never applied to any method. The device check in each do_* method is done manually with 'if self._device is None' instead of using this decorator.
- **Fix:** Either apply @_requires_device to all do_* methods that need a device, or remove the dead decorator function.

### [Low] Dead shim scripts: run_ingest.py and run_splink.py
- **File:** `scripts/run_ingest.py` (Lines: 1-15)
- **Effort:** XS
- **Description:** scripts/run_ingest.py and scripts/run_splink.py are thin wrappers (3-10 lines of real code) that simply import and call functions already accessible via the CLI pipeline. They are legacy artifacts from before the unified pipeline CLI.
- **Fix:** Deprecate with a warning pointing to 'python -m zentinull.cli.pipeline', then remove after one release cycle. Keep if directly referenced by cron/scheduler scripts.

### [Low] 5 pipeline columns created but only 4 stages used
- **File:** `dashboard.py` (Lines: 215, 220-226)
- **Effort:** XS
- **Description:** st.columns(5) allocates 5 columns for the pipeline status row, but stage_order only has 4 entries. The 5th column is unused, leaving dead space in the KPI row. This reduces the available width for the 4 real metrics.
- **Fix:** Change to st.columns(4) to match the 4 stages, or if space was reserved for a future stage, leave as-is and add a comment.

### [Low] Hardcoded 6 in freshness column count not derived from manifest
- **File:** `dashboard.py` (Lines: 231)
- **Effort:** XS
- **Description:** fcols = st.columns(min(len(freshness), 6)) hardcodes 6 as the max column count for data freshness. If the manifest is extended to 7+ systems, only the first 6 sources will render in the freshness row (sorted alphabetically by key). The 7th and beyond are silently dropped.
- **Fix:** Compute max columns from len(manifest.systems) or remove the cap entirely (the len(freshness) cap already prevents over-allocating). Alternatively, use a flow layout.

### [Low] Stage label 'Mesh load' is ambiguous for the load stage
- **File:** `dashboard.py` (Lines: 219)
- **Effort:** XS
- **Description:** The load stage is labeled 'Mesh load' in stage_labels. The actual serve.py subcommand is simply 'load' which loads Splink cluster output into DuckDB. The label 'Mesh load' could be misinterpreted as a mesh-database loading indicator rather than the pipeline load stage.
- **Fix:** Change label from 'Mesh load' to 'Load' for consistency with other stage labels and the serve.py subcommand name.

### [Low] .env.example contains real Active Directory domain DC=moonlite,DC=local instead of placeholder
- **File:** `.env.example` (Lines: 35)
- **Effort:** XS
- **Description:** .env.example line 35 has AD_SEARCH_BASE=DC=moonlite,DC=local — this is the real AD domain from the production .env file. It's a mild information disclosure risk if the repo is public or shared with external contractors. The config.py default uses DC=example,DC=local which is a proper placeholder.
- **Fix:** Change .env.example AD_SEARCH_BASE to DC=example,DC=local to match the config.py default, keeping the real domain only in the gitignored .env file.

### [Low] data/hw_extract_cache.json is a 2-byte stale cache file
- **File:** `data/hw_extract_cache.json` (Lines: 1)
- **Effort:** XS
- **Description:** The hardware extraction cache file is 2 bytes (likely '{}' or empty JSON). Its companion input file hw_extract_input.json does not exist. This is a leftover artifact from the LM Studio hardware extraction experiment (scripts/hw_extract_batch.py).
- **Fix:** Delete data/hw_extract_cache.json. Add hw_extract_cache.json and hw_extract_cache.json to .gitignore to prevent re-contamination.

### [Low] .benchmarks/ directory stores stale historical benchmark data
- **File:** `.benchmarks/` (Lines: 1)
- **Effort:** XS
- **Description:** The .benchmarks/ directory with api_results.json (9KB) stores historical benchmark timings from scripts/bench.py and scripts/bench_api.py. These are generated artifacts that reference specific commit states and may produce false regression alerts for the wrong baseline after significant refactoring.
- **Fix:** Add .benchmarks/ to .gitignore if not already (it appears to be tracked via the gitignore line covering it). Or clear it and re-benchmark on current code.

### [Low] src/zentinull.egg-info/ build artifact present in source tree
- **File:** `src/zentinull.egg-info/` (Lines: multiple)
- **Effort:** XS
- **Description:** The egg-info directory contains PKG-INFO, SOURCES.txt, requires.txt generated by pip install -e . It's a build artifact that shouldn't be in the interactive workspace but isn't gitignored. It includes stale references in PKG-INFO to nonexistent scripts/pipeline.py.
- **Fix:** Add src/*.egg-info/ to .gitignore and delete the directory. Run 'pip install -e .' to regenerate it in a virtual environment.

### [Low] .coverage (52KB) is a generated coverage data file at project root
- **File:** `.coverage` (Lines: 1)
- **Effort:** XS
- **Description:** The .coverage file at the project root contains coverage measurement data from pytest --cov runs. It's not in .gitignore and may accidentally be committed. It's also what generated the stale code coverage numbers in the README.
- **Fix:** Add .coverage to .gitignore and delete the file. Coverage data should be ephemeral.

### [Low] data/me_oauth.json and data/sdp_oauth.json store OAuth tokens in runtime data directory
- **File:** `data/*oauth.json` (Lines: 1)
- **Effort:** S
- **Description:** OAuth refresh tokens for ManageEngine and ServiceDesk Plus are stored in the data/ directory as me_oauth.json and sdp_oauth.json. These are correctly gitignored but are sensitive credentials exposed in the runtime data directory alongside pipeline artifacts, risking accidental inclusion in backups, Docker volumes, or logs.
- **Fix:** Move OAuth token files to a dedicated secrets/ or config/ directory with restricted permissions (0600), or store them in .env. Update the auth factory paths to match.

### [Low] scripts/dq.py is a full cmd.Cmd interactive shell at scripts/ level
- **File:** `scripts/dq.py` (Lines: 1-314)
- **Effort:** M
- **Description:** The dq.py interactive device query shell is a 314-line standalone script using cmd.Cmd. It duplicates URI construction and API interaction patterns that already exist in the API layer. As a script/ entry point, it's not discoverable via 'serve.py' and has no help integration.
- **Fix:** Consider moving the interactive shell into serve.py as a 'serve.py dq' or 'serve.py shell' subcommand, or keep as a script but add a 'serve.py dq' alias that invokes it.

### [Low] .omo/ and .codegraph/ agent artifacts directory at project root
- **File:** `.omo/` (Lines: multiple)
- **Effort:** XS
- **Description:** The .omo/ (agent plans/audits) and .codegraph/ (3.5MB codegraph.db) directories contain Oh My Pi and code-graph agent artifacts. The .codegraph/ data alone is 3.5MB on disk. These are development environment artifacts that bloat the project and may interfere with CI/build contexts.
- **Fix:** Add .omo/ and .codegraph/ to .gitignore if not already covered. These are developer-machine artifacts.

### [Low] data/pipeline.lock is a runtime lock file in the data directory
- **File:** `data/pipeline.lock` (Lines: 1)
- **Effort:** S
- **Description:** pipeline.lock is a 5-byte advisory lock file created during pipeline runs. It is a runtime artifact that should be cleaned up on graceful shutdown but may persist after crashes, blocking subsequent pipeline runs.
- **Fix:** Ensure lock file cleanup in signal handlers and on exception paths in pipeline.py. Consider writing it to a temp directory instead of data/ so it's not backed up.

### [Low] Ingest adapter is a thin delegation layer with no encapsulation
- **File:** `src/zentinull/ingest_adapter.py` (Lines: 1-98)
- **Effort:** XS
- **Description:** ingest_adapter.py is 98 lines, of which ~30 are docstrings, ~20 are imports and constants, and the core function run_ingest() simply wraps run_system() in a ThreadPoolExecutor. The helper functions get_system_label() and get_all_system_labels() are one-liners. This module could be collapsed into cli/pipeline.py without loss of clarity.
- **Fix:** Either inline the ThreadPoolExecutor logic directly into cli/pipeline.py's run_ingest() (where it's already the sole caller), or keep as-is but move the thin label helpers into a shared utility. Low priority — the abstraction does provide a clean separation boundary.

### [Low] Duplicate _try_flock / file locking pattern in pipeline.py and status.py
- **File:** `src/zentinull/cli` (Lines: pipeline.py:52-61, status.py:27-33)
- **Effort:** XS
- **Description:** Both cli/pipeline.py and cli/status.py implement the same fcntl.flock advisory file locking pattern with nearly identical function signatures (pipeline uses _try_flock returning bool; status uses _flock with exclusive param). The Windows-guard import pattern is duplicated.
- **Fix:** Extract a shared flock utility (e.g., src/zentinull/util/flock.py or into config.py) with both try_flock (non-blocking) and lock (blocking) variants.

### [Low] cling/flock vs status._flock — different signatures for same concept
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 52-61)
- **Effort:** XS
- **Description:** Pipeline's _try_flock uses exclusive non-blocking lock and returns bool. Status's _flock takes an exclusive: bool parameter and raises on failure. Having two locking patterns with different semantics for the same filesystem risks confusion when they lock the same status file.
- **Fix:** Unify into a single flock utility with clear shared/exclusive, blocking/non-blocking semantics. pipeline.py's use case (PID lock file) is fundamentally different from status.py's (status.json), so the split is acceptable — but the duplicated code is unnecessary.

### [Low] global _config_loaded flag with lock in config.py — pre-mature optimization
- **File:** `src/zentinull/config.py` (Lines: 52-57)
- **Effort:** XS
- **Description:** _ensure_loaded() uses a global boolean + threading.Lock to call _load_dotenv() exactly once. Given that _load_dotenv() is idempotent (reads .env, sets os.environ) and called at most a handful of times during startup, the double-checked locking pattern adds complexity without measurable benefit.
- **Fix:** Simplify: just call _load_dotenv() at module level. If lazy-first-call semantics are needed, use a simple flag without the lock (Python's GIL makes the boolean race benign for a side-effect-free flag write).

### [Low] Function-attribute caching with _get_manifest._cache bypasses multi-project support
- **File:** `src/zentinull/cli/pipeline.py` (Lines: 55-57)
- **Effort:** XS
- **Description:** _get_manifest() uses function-attribute caching (_get_manifest._cache = load_manifest()) which persists the manifest for the entire process lifetime. If ZENTINULL_PROJECT changes between calls (possible in test suites), the cached manifest is from the wrong project. The manifest/__init__.py load_manifest() itself has no cache — this is an ad-hoc one.
- **Fix:** Remove the function-attribute cache and call load_manifest() directly each time, or key the cache by the current project name. load_manifest() already guards against repeated loading via importlib (sys.modules caching).

### [Low] Dashboard stats Pydantic model strips source_count_dist after prior breakage
- **File:** `src/zentinull/api/db.py` (Lines: 599-613)
- **Effort:** S
- **Description:** Memory lesson #6 (captured earlier) documents that Pydantic v2 stripped source_count_dist from DashboardStats because the model lacked that field. The field was added to models.py (line 68) but the db.py dashboard() method still returns a dict with this key — meaning the fix only covered the Pydantic contract breakage but the dashboard SQL query wasn't audited for other missing fields.
- **Fix:** Run a contract audit: compare every key returned by db.dashboard() against DashboardStats.model_fields to confirm no other fields are silently stripped.

### [Trivial] validate_config() only checks OAuth file existence, not credential emptiness
- **File:** `src/zentinull/config.py` (Lines: 251-265)
- **Effort:** S
- **Description:** validate_config() (called in server.py lifespan) only warns if explicitly-configured OAuth files are missing. It does NOT check whether required credentials are empty (AD_PASSWORD='', FG_API_KEY='', ZBX_TOKEN='', ME_CLIENT_SECRET='', SDP_CLIENT_SECRET=''). An operator could deploy with all placeholder defaults, the server starts fine, and all ingests silently return 0 rows because auth fails for every source.
- **Fix:** Add optional startup warnings in validate_config() when critical credential env vars are still empty defaults. Gate these warnings behind a flag or add them as INFO-level logs so they don't block deployment.

### [Trivial] OAuth token file paths default to data/ but .dockerignore doesn't exclude data/*oauth.json from build context
- **File:** `.dockerignore` (Lines: 1-50)
- **Effort:** XS
- **Description:** OAuth token files (data/me_oauth.json, data/sdp_oauth.json) are gitignored via data/*oauth.json (in .gitignore line 47), but .dockerignore doesn't have a matching rule. In a production Docker build, this isn't exploitable because (a) token files are runtime-generated and live in the data/ Docker volume, never in the build context, and (b) the .dockerignore already has data/*.sqlite and export/ entry patterns. Not exploitable, but inconsistent — future additions of sensitive files to data/ may not be considered.
- **Fix:** Add data/*oauth.json to .dockerignore for consistency and defense-in-depth, even though the files shouldn't be in the build context.

### [Trivial] fg_base_url is duplicated as both a Config field and the FG_BASE_URL endpoint key creates indirect coupling
- **File:** `projects/default/manifest.py; src/zentinull/config.py; src/zentinull/ingest/runner.py` (Lines: 250, 143, 223, 38)
- **Effort:** XS
- **Description:** The FortiGate endpoint in manifest.py references endpoint base='FG_BASE_URL' (uppercase), which runner.py resolves by calling getattr(get_config(), 'fg_base_url') via .lower(). This indirection is fragile: if the Config field name changes to 'fortigate_base_url', the manifest breaks at runtime with AttributeError. There's no test that validates all endpoint base names resolve to valid Config fields.
- **Fix:** Add a test in tests/test_contracts.py that iterates all feed endpoints with a 'base' key and verifies getattr(get_config(), base.lower()) succeeds. Consider renaming the manifest field from the inferred lowercase convention to an explicit method.

### [Trivial] render.py imports os inside function instead of at module level
- **File:** `src/zentinull/cli/render.py` (Lines: 194-195)
- **Effort:** XS
- **Description:** import os and import sys appear inside is_brutalist_enabled() and _init_console() rather than at the top of the file. os is a stdlib module with no import side effects — no benefit to lazy loading.
- **Fix:** Hoist import os and import sys to the top of render.py.

### [Trivial] streaming.py lazily imports threading and os inline
- **File:** `src/zentinull/cli/streaming.py` (Lines: 57, 65)
- **Effort:** XS
- **Description:** import threading and import os appear inside run_streaming() rather than at module level. No circular import risk — these are stdlib modules.
- **Fix:** Hoist to the top of the file alongside the other stdlib imports.
