# Sentinel Intelligence → Zentinull Implementation Plan

## Overview

Port Sentinel's semantic intelligence, change tracking, and diagnostics
into Zentinull's existing manifest/strategy/DuckDB architecture.
Zentinull is the base; every change is additive to it.

## Phase A — SOT Hierarchy (5 files, ~140 lines)

### Goal
Every field in a golden record has an explicit primary/secondary source
priority. The consolidated view picks a canonical value instead of showing
all values with no preference.

### A1. Add `sot` field to `ResolutionProfile`

**File:** `src/zentinull/manifest/types.py`
**Change:** After `lambda_recall: float = 0.5` (line 169), add:

```python
sot: Mapping[str, tuple[str, str]] = field(default_factory=dict)
# per-field (primary_source, secondary_source).
# e.g. {"name": ("sp", ""), "serial_number": ("me", "sp")}
```

`field` is already imported (`types.py:11`); the dataclass is `frozen=True`, which
is fine — `field(default_factory=dict)` works on frozen dataclasses.

**Verification:** `python -c "from zentinull.manifest.types import ResolutionProfile; print(ResolutionProfile.__dataclass_fields__['sot'])"` — outputs the field.

### A2. Add SOT validation check #11

**File:** `src/zentinull/manifest/__init__.py`
**Change:** Inside `_validate()`, after check #10 (ends line 235) and **before**
the `if errors:` raise block at line 237, add:

```python
# 11. SOT keys must be in profile fields, SOT values must be valid system keys
for prof_name, profile in manifest.profiles.items():
    for sot_field, (primary, secondary) in profile.sot.items():
        valid_fields = set(profile.fields) | set(profile.derived)
        if sot_field not in valid_fields:
            errors.append(
                f"profile '{prof_name}' SOT field '{sot_field}' not in "
                f"profile fields/derived"
            )
        for src_key in (primary, secondary):
            if src_key and src_key not in manifest.systems:
                errors.append(
                    f"profile '{prof_name}' SOT source '{src_key}' "
                    f"not a valid system"
                )
```

**Verification:** Run `pytest tests/test_manifest.py -v`. Existing validation tests still pass. Add a temporary bad SOT entry to confirm failure — passes after removal.

### A3. Add SOT entries to manifest

**File:** `projects/default/manifest.py`
**Change:** Inside `DEVICE_PROFILE = ResolutionProfile(...)`, add after `lambda_recall`:

```python
sot={
    "name": ("sp", ""),
    "serial_number": ("me", "sp"),
    "mac_address": ("ad", "fg"),
    "ip_address": ("ad", "zbx"),
    "manufacturer": ("me", "sp"),
    "model": ("me", "sp"),
    "os": ("me", "sp"),
    "os_version": ("me", "sdp"),
    "assigned_user": ("sp", "me"),
    "imei": ("sdp", "me"),
    "asset_tag": ("sp", "sdp"),
}
```

> **Corrected (verified against source):** the profile field is `name`, not
> `device_name` (`projects/default/manifest.py:92`), and there is **no `mdm`
> system** — valid system keys are `sp, me, fg, zbx, ad, sdp`
> (`manifest.py:34-83`). ManageEngine MDM data arrives under `me`; `sdp` is the
> only system declaring `imei`. Check #11 would have rejected the original
> entries at manifest load.

**Verification:** `pytest tests/test_manifest.py -v -k validation`. All manifest validation tests pass including the new check #11.

### A4. Add `sot_resolve()` function

**File:** New file `src/zentinull/resolve/sot.py` (~50 lines)

```python
"""SOT resolution — picks canonical value per field from per-source records."""

from __future__ import annotations
from typing import Any
from ..manifest.types import ResolutionProfile

def sot_resolve(
    profile: ResolutionProfile,
    source_records: dict[str, dict[str, Any]],
    coverage: Mapping[str, float] | None = None,
) -> dict[str, tuple[str, str | None, str]]:
    """Resolve canonical values per field.

    Args:
        profile: ResolutionProfile with sot dict.
        source_records: {source_key: {field: value}} per source in the cluster.

    Returns:
        {field: (value, source_tag, priority)}
        priority: "primary" | "secondary" | "best_effort"
    """
    result: dict[str, tuple[str, str | None, str]] = {}
    for field, (primary, secondary) in profile.sot.items():
        # Primary
        val = source_records.get(primary, {}).get(field)
        if val:
            result[field] = (str(val), primary, "primary")
            continue
        # Secondary
        if secondary:
            val = source_records.get(secondary, {}).get(field)
            if val:
                result[field] = (str(val), secondary, "secondary")
                continue
        # Best effort — highest-coverage source first (deterministic),
        # falling back to caller order when no coverage map is given.
        # Pass {k: s.coverage for k, s in manifest.systems.items()}.
        cov = coverage or {}
        for src in sorted(source_records, key=lambda s: -cov.get(s, 0.0)):
            val = source_records[src].get(field)
            if val:
                result[field] = (str(val), src, "best_effort")
                break
        else:
            result[field] = ("", None, "none")
    return result
```

Also export from `src/zentinull/resolve/__init__.py` (append `from .sot import sot_resolve`).

**Verification:**
```python
from zentinull.resolve.sot import sot_resolve
from zentinull.manifest.types import ResolutionProfile

profile = ResolutionProfile(
    name="test", fields=("name", "serial"), derived={},
    comparisons=(), blocking=(), deterministic=(), em_passes=(),
    predict_threshold=0, cluster_threshold=0,
    sot={"name": ("sp", "me"), "serial": ("me", "sp")},
)
records = {"sp": {"name": "WS28"}, "me": {"name": "ws28", "serial": "ABC"}}
result = sot_resolve(profile, records)
assert result["name"] == ("WS28", "sp", "primary")
assert result["serial"] == ("ABC", "me", "primary")
```

Import `Mapping` from `collections.abc`. The `coverage` parameter makes the
best-effort fallback deterministic — `System.coverage` is already declared per
system in the manifest (sp=0.55, ad=0.60, ...), so ties break by declared
source quality instead of dict insertion order.

### A5. Generate `DEVICES_SQL` from SOT (consistency everywhere)

**File:** `src/zentinull/api/schema.py` + `src/zentinull/cli/pipeline.py`

Without this, SOT only affects the story endpoint while `devices` — and
therefore `/search`, `/clusters`, `/dashboard`, `/anomalies` — keeps picking
consolidated values by **alphabetical accident**: `DEVICES_SQL`
(api/schema.py:34-58) uses `MIN(CASE WHEN field != '' THEN field END)` for
every field. Same device, two different serials shown depending on endpoint.

Replace the static `DEVICES_SQL` constant with a generator:

```python
def build_devices_sql(profile: ResolutionProfile) -> str:
    """CREATE OR REPLACE TABLE devices — SOT-priority consolidated values.

    For each field with an SOT entry, emit a priority COALESCE chain:
        COALESCE(
            NULLIF(MAX(CASE WHEN source = '<primary>' AND f != '' THEN f END), ''),
            NULLIF(MAX(CASE WHEN source = '<secondary>' AND f != '' THEN f END), ''),
            NULLIF(MIN(CASE WHEN f != '' THEN f END), ''),   -- best effort
            ''
        ) AS f
    Fields without an SOT entry keep the current MIN() shape. device_name,
    source_count, sources, record_count aggregates are unchanged.
    """
```

`run_load()` (pipeline.py:459 currently executes the constant) calls
`conn.execute(build_devices_sql(_get_manifest().profiles["device"]))` instead.
Keep a `DEVICES_SQL = build_devices_sql(<empty-sot stub>)`-free design — the
constant is deleted; tests referencing it (`tests/api/test_schema.py`) switch
to calling the generator with a fixture profile.

This is the same manifest→generate pattern as `create_extra_view()`. After A5,
`sot_resolve()` in `_build_story()` (E3) is guaranteed to agree with the
`devices` table because both derive from the same `profile.sot`.

**Verification:** after a full load, for a known multi-source device:
`SELECT serial_number FROM devices WHERE cluster_id = ?` equals the E3
story's `sot["serial_number"]["value"]`; `pytest tests/api/test_schema.py`.

---

## Phase B — Better Splink Inputs + Cluster Validation (6 files, ~90 lines)

> **Redesigned (2026-07-16):** the original Phase B ported Sentinel's 280-line
> `_is_equivalent()` rule engine plus a `nuance.json` sidecar and rewrote
> Splink's cluster assignments post-hoc. That reintroduces exactly the
> hand-tuned rule system Zentinull's Splink/ML resolution was built to replace,
> and leaves two competing resolution authorities to maintain. Every case the
> monolith handled maps to one of two Zentinull-native mechanisms instead:
> a **derived-field transform** (feed Splink a better column) or a
> **post-cluster annotation** (flag odd results for review — never rewrite).
> Dropped entirely: `_is_equivalent()`, `_names_equivalent()` (Splink's
> levenshtein + `name_clean`/`name_fallback` already covers it),
> `resolve_user_full_name()` (needs Sentinel's `sp_employees` table),
> `nuance.json`, and all cluster-ID rewriting.

### Goal
Splink stays the single resolution authority. Give it normalized columns it
can actually match on (OS family), and flag — never override — suspicious
cluster decisions for human review.

### B1. Add `os_family` derived field

The one equivalence Splink genuinely can't learn from raw strings: "Windows 10
Pro" vs "Windows 10 Enterprise" (edition noise swamps the signal). Normalize it
into the manifest's existing derived-field mechanism.

**File 1:** `src/zentinull/normalizer.py` — add `normalize_os_family(val) -> str`
(~20 lines): sentinel-strip, lowercase, then map through a small inline
`_OS_FAMILY_MAP` dict ported from Sentinel's
`human_nuance.json → normalization_rules.operating_system.family_map`
(windows/macos/ios/android/linux buckets). This is the **only** piece of
human_nuance.json that crosses over — as a code constant, not a config file.

**File 2:** `src/zentinull/manifest/transforms.py` — register it:
`"os_family": normalize_os_family` in `REGISTRY` (transforms.py:16-22; existing
entries: mac, serial, name, lower, first_of_list, join_list).

**File 3:** `projects/default/manifest.py` — in `DEVICE_PROFILE`:
- append `"os_family"` to `fields`
- add `"os_family": ("os", "os_family")` to `derived`
- replace the `os` comparison (manifest.py:120) with
  `Comparison(kind="exact", column="os_family", term_frequency_adjustments=True)`

**File 4:** `src/zentinull/export_for_splink.py` — derived fields are computed
inline at export (verified: `name_clean`/`mac_clean`/`name_fallback` are
hardcoded at lines 78-88, `profile.derived` is declarative metadata only). Add
alongside them: `rec["os_family"] = normalize_os_family(rec.get("os", ""))`,
and add `"os_family"` to `_COMPUTED_FIELDS` (line 21).

Optional while-in-there: extend `_SERIAL_PREFIX_RE` in `normalizer.py` with
Sentinel's vendor prefixes (Dell `1S`, leading `S` before alnum) —
`normalize_serial` (normalizer.py:124-141) currently strips only `SN-`/`S/N:`
style prefixes. Low risk: it is applied uniformly to every source.

**Verification:** `pytest tests/test_normalizer.py tests/test_manifest.py
tests/test_export.py` — plus one new parametrized test:
`normalize_os_family("Windows 10 Enterprise") == normalize_os_family("Microsoft Windows 10 Pro") == "windows"`.
Then a full `splink` run — cluster count should be **≤ baseline** (previously
OS-split devices now merge into single clusters).

### B2. Post-Splink cluster validation (annotate, never rewrite)

**File:** New `src/zentinull/resolve/validate.py` (~50 lines)

```python
def validate_clusters(clusters_csv: Path) -> list[dict[str, str]]:
    """Flag suspicious Splink decisions. Read-only — never modifies clusters.

    Checks (per cluster / cross-cluster, using normalized values):
    1. SERIAL_CONFLICT   — one cluster, >1 distinct non-empty serial_number
                           (possible false-positive merge)
    2. SPLIT_IDENTITY    — same non-empty serial_number in >1 cluster
                           (possible false-negative split; Splink threshold miss)
    Returns [{cluster_id, kind, field, values, detail}, ...] and writes
    PATHS.splink_output_dir / "cluster_annotations.csv".
    """
```

**Pipeline wiring:** `src/zentinull/cli/pipeline.py` — after `run_splink()`
(called at line 840 inside `run_pipeline()`, def at line 790), before
`run_load()` (line 845):

```python
log.info({"event": "pipeline_stage", "stage": "validate"})
record_start("validate")
with StepTimer(log, "validate"):
    from ..resolve.validate import validate_clusters
    annotations = validate_clusters(PATHS.splink_output_dir / "clusters.csv")
record_done("validate", annotations=len(annotations))
```

No skip flag needed — the pass is read-only and fast (one CSV scan). `run_load()`
loads `cluster_annotations.csv` into a DuckDB `cluster_annotations` table in the
same bridge step as C1; Phase D's `anomalies()` surfaces the counts.

**Verification:** Full `python serve.py pipeline`; check
`cluster_annotations.csv` exists and `SELECT kind, COUNT(*) FROM
cluster_annotations GROUP BY kind` returns plausible counts (drift audit in the
DB shows known serial conflicts today — those must appear as SERIAL_CONFLICT).

---

## Phase C — Change Tracking (3 files, ~120 lines)

### Goal
Track every field value change per source record. History is **authoritative in
the per-source SQLite stores** (they survive pipeline runs) and is **re-loaded
into DuckDB on every `run_load()`** — the mesh is rebuilt from scratch via
temp-and-swap (pipeline.py:447-483), so any table created only in DuckDB would
be wiped on every full load. Zentinull currently has zero audit trail.

### C1. field_history table — per-source SQLite + DuckDB load step

**File 1:** `src/zentinull/ingestors/base.py` — extend `ensure_raw_store()` to
also create the history table in each per-source SQLite:

```sql
CREATE TABLE IF NOT EXISTS field_history (
    source_id        TEXT NOT NULL,
    source           TEXT NOT NULL,
    field            TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    changed_at       TEXT DEFAULT (datetime('now')),
    batch_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_fh_source ON field_history(source_id);
CREATE INDEX IF NOT EXISTS idx_fh_field ON field_history(field);
```

**File 2:** `src/zentinull/cli/pipeline.py` inside `run_load()` — after
`SOURCE_RECORDS_SQL`/`DEVICES_SQL` (lines 456-459), add a bridge step that
re-populates DuckDB from the SQLite stores, joining `source_id → cluster_id`
through the freshly loaded `source_records` table (its columns include both
`source`, `source_id`, and `cluster_id`):

```sql
CREATE TABLE field_history (
    cluster_id       VARCHAR,
    source           VARCHAR NOT NULL,
    source_id        VARCHAR NOT NULL,
    field            VARCHAR NOT NULL,
    old_value        VARCHAR,
    new_value        VARCHAR,
    changed_at       TIMESTAMP,
    batch_id         VARCHAR
);
CREATE INDEX idx_fh_cluster ON field_history(cluster_id);
CREATE INDEX idx_fh_field ON field_history(field);
```

then per source SQLite (via `sqlite_scan` or a Python read + `executemany`):
`INSERT INTO field_history SELECT sr.cluster_id, fh.source, fh.source_id,
fh.field, fh.old_value, fh.new_value, CAST(fh.changed_at AS TIMESTAMP),
fh.batch_id FROM <sqlite fh> LEFT JOIN source_records sr ON sr.source =
fh.source AND sr.source_id = fh.source_id`.

Corrections vs the original draft (verified against source):
- The original put the CREATE only in `run_load()`'s temp DB — that table would
  be **empty after every full load** (temp-and-swap discards the old mesh) and
  the SQLite-side writes from C2 would never reach the API. The SQLite stores
  are now the system of record; DuckDB is a rebuildable projection.
- The original DuckDB DDL had a trailing comma after `batch_id VARCHAR,` —
  a syntax error.
- The original schema keyed DuckDB history by `cluster_id` while C2 writes
  `source_id` rows — no mapping existed. The load-time join above provides it;
  `cluster_id` is nullable because a record may have left the mesh.

**Verification:** After `python serve.py load`, DuckDB shows `field_history`
with rows matching the per-source SQLite contents (zero rows on first run).

### C2. Add `capture_field_history()` to upsert

**File:** `src/zentinull/ingestors/base.py`
**Change:** Inside `upsert_raw_rows()` (line 204): the existing-row fetch at
line 243 selects only `raw_hash`; unchanged rows are skipped at line 245 and
changed rows are UPDATEd then counted at `written += 1` (line 259).

Insert a new function `capture_field_history()` at module level (~50 lines):

> **Corrected diff source:** the raw-store tables have **only meta columns**
> (`source_id, raw_json, raw_hash, remote_updated_at, fetched_at` —
> base.py:250-257). There are no per-field columns, so diffing a `SELECT *` row
> against the raw API dict compares nothing to everything. The diff must be
> **old = flatten(json.loads(old raw_json)) vs new = flatten(new row dict)**,
> using the same dotted-key flattening as `manifest/walker._flatten_raw()`
> (import it — do not duplicate).

```python
# Sentinel values treated as "no data" — skip tracking
_FH_NULL_SENTINELS = frozenset({"", "--", "-", "N/A", "null", "None"})

def _fh_clean(val: str | None) -> str | None:
    """Normalize for comparison; return None for sentinel/empty."""
    if val is None:
        return None
    s = str(val).strip()
    return None if not s or s in _FH_NULL_SENTINELS else s

def _fh_is_meta(col: str) -> bool:
    """True if key is infrastructure/noise, not data (`@odata.etag` etc.)."""
    return col.startswith("@") or col.endswith("@odata.type")

def capture_field_history(
    conn: sqlite3.Connection,
    source_id: str,
    old_raw: str | None,
    new_row: dict[str, Any],
    source: str,
    batch_id: str,
) -> int:
    """Diff old raw_json vs new row (both flattened), write field_history rows."""
    if not old_raw:
        return 0
    try:
        old_dict = json.loads(old_raw)
    except (json.JSONDecodeError, TypeError):
        return 0
    from ..manifest.walker import _flatten_raw
    old_flat = dict(_flatten_raw(old_dict))
    new_flat = dict(_flatten_raw(new_row))
    written = 0
    for col in old_flat.keys() | new_flat.keys():
        if _fh_is_meta(col):
            continue
        old_clean = _fh_clean(old_flat.get(col))
        new_clean = _fh_clean(new_flat.get(col))
        if new_clean == old_clean:
            continue  # no change (or both empty)
        conn.execute(
            "INSERT INTO field_history (source_id, source, field, "
            "old_value, new_value, batch_id) VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, source, col, old_clean, new_clean, batch_id),
        )
        written += 1
    return written
```

Note: do **not** exclude keys by `endswith("_id")` (the original draft did) —
that silently drops real data fields like `user_id` and `hostid`.

Then in `upsert_raw_rows()`, widen the line-243 fetch to
`SELECT raw_hash, raw_json` and, inside the `if existing:` UPDATE branch
(lines 248-253), diff before overwriting:

```python
existing = conn.execute(
    f"SELECT raw_hash, raw_json FROM {table} WHERE source_id = ?", (source_id,)
).fetchone()

if existing and existing[0] == new_hash:
    continue  # unchanged — skip (unchanged from today)

if existing:
    capture_field_history(conn, source_id, existing[1], row, source, batch_id)
    # ... existing UPDATE ...
```

`source` and `batch_id` are not in `upsert_raw_rows`'s signature
(`(conn, table, rows, id_path, updated_path=None)`) — add
`source: str = ""` and `batch_id: str = ""`. The callers are in
**`src/zentinull/ingest/runner.py`** (not `ingestors/runner.py`) at **two**
call sites — lines 106 and 201 — both must pass the feed's system key and a
per-run batch id (a `uuid4().hex[:12]` minted once per `run_ingest`).

While-in-there (both verified against base.py):
- **Batch the existing-row fetch.** The loop currently issues one
  `SELECT ... WHERE source_id = ?` per row (base.py:243); with `raw_json` added
  that doubles the payload. Pre-fetch in chunks before the loop:
  `SELECT source_id, raw_hash, raw_json FROM {table} WHERE source_id IN (...)`
  (500 ids per chunk) into a dict — the per-row loop then does zero reads.
- **Retention.** `field_history` is append-only and per-source SQLite grows
  unboundedly. At the top of each ingest run:
  `DELETE FROM field_history WHERE changed_at < datetime('now', '-180 days')`
  (window via `FH_RETENTION_DAYS` env var, default 180, read in config.py).

**Verification:** Run a single-source ingest twice with no API changes. First run
writes rows with no history. Second run writes zero changes. Then modify a source
record's value in the SQLite table directly, run ingest again — `field_history`
shows one change row for the modified field.

---

## Phase D — Diagnostics (2 files, ~80 lines)

### Goal
Populate `anomalies()` with zombie detection, hardware drift, and cluster
review annotations. Surface unmapped-field diagnostics from `extra_attributes`.

### D1. Zombie + hardware drift queries

**File:** `src/zentinull/api/db.py`
**Change:** Inside `anomalies()` (line 502), after the existing singleton/no-name/no-serial
queries, add:

> **Prerequisite (verified):** `source_records` is a raw `read_csv_auto(...,
> all_varchar=true)` load of clusters.csv (api/schema.py:28-31) — it has **no
> `fetched_at` column**, and the original zombie SQL fails at bind time. Extend
> the C1 bridge step in `run_load()` to also build a freshness table from the
> per-source SQLite raw stores (they all carry `fetched_at`):
>
> ```sql
> CREATE TABLE record_freshness (
>     source VARCHAR NOT NULL, source_id VARCHAR NOT NULL,
>     fetched_at TIMESTAMP
> );
> ```
>
> populated per store with `SELECT :source, source_id, CAST(fetched_at AS
> TIMESTAMP) FROM <table>`. The zombie query then joins through it.

> Make the window configurable: `ZOMBIE_STALE_DAYS` env var in `config.py`
> (default 90, same convention as `SPLINK_THRESHOLD`), interpolated into the
> interval, and accepted as `?days=` on `/anomalies`.

```python
# Zombie detection — no source record fetched in 90 days
zombies = conn.execute("""
    SELECT * FROM devices
    WHERE device_name != '(unnamed)'
    AND source_count <= 2
    AND cluster_id NOT IN (
        SELECT DISTINCT sr.cluster_id FROM source_records sr
        JOIN record_freshness rf
          ON rf.source = sr.source AND rf.source_id = sr.source_id
        WHERE rf.fetched_at >= NOW() - INTERVAL '90 days'
    )
    LIMIT 50
""").fetchall()
zombie_cols = [d[0] for d in conn.description]
zombies_total = conn.execute("""
    SELECT COUNT(*) FROM devices
    WHERE device_name != '(unnamed)'
    AND source_count <= 2
    AND cluster_id NOT IN (
        SELECT DISTINCT sr.cluster_id FROM source_records sr
        JOIN record_freshness rf
          ON rf.source = sr.source AND rf.source_id = sr.source_id
        WHERE rf.fetched_at >= NOW() - INTERVAL '90 days'
    )
""").fetchone()
assert zombies_total is not None
zombies_count: int = zombies_total[0]

# Hardware drift — serial_number conflicts within clusters
drift_rows = conn.execute("""
    SELECT sr.cluster_id, sr.serial_number, sr.source
    FROM source_records sr
    JOIN (
        SELECT cluster_id
        FROM source_records
        WHERE serial_number != ''
        AND serial_number IS NOT NULL
        GROUP BY cluster_id
        HAVING COUNT(DISTINCT serial_number) > 1
    ) d ON sr.cluster_id = d.cluster_id
    WHERE sr.serial_number != ''
    AND sr.serial_number IS NOT NULL
    ORDER BY sr.cluster_id, sr.source
    LIMIT 100
""").fetchall()
drift_total = conn.execute("""
    SELECT COUNT(DISTINCT cluster_id) FROM source_records
    WHERE serial_number != '' AND serial_number IS NOT NULL
    AND cluster_id IN (
        SELECT cluster_id FROM source_records
        WHERE serial_number != '' AND serial_number IS NOT NULL
        GROUP BY cluster_id HAVING COUNT(DISTINCT serial_number) > 1
    )
""").fetchone()
assert drift_total is not None
drift_count: int = drift_total[0]

# Build drift cluster list
drift_clusters: dict[str, dict] = {}
for row in drift_rows:
    cid = row[0]
    if cid not in drift_clusters:
        drift_clusters[cid] = {"cluster_id": cid, "serials": {}}
    drift_clusters[cid]["serials"][row[2]] = row[1]
drift_list = [{"cluster_id": c, "serials": d["serials"]} for c, d in drift_clusters.items()]
```

Add to the return dict:

```python
"zombies": zombies_count,
"zombie_list": [
    self._row_to_cluster_info(dict(zip(zombie_cols, r, strict=True))).model_dump()
    for r in zombies
],
"hardware_drift": drift_count,
"hardware_drift_list": drift_list,
```

Drift audit built in Phase E3 should use `normalize_os_family()` for the
`os` field (not raw `v.lower()`), so "Windows 10 Pro" vs "Windows 10
Enterprise" gets verdict `EQUIVALENT` instead of `MISMATCH` — consistent with
what Splink is now told. Other fields fall back to `v.lower()`.
Append to the `anomalies()` return block (from B2 bridge table loaded in
run_load, cf. cluster_annotations table):

```python
"review_total": review_count,
"review_list": [
    {"cluster_id": r[0], "kind": r[1], "field": r[2], "values": r[3], "detail": r[4]}
    for r in review_rows
],
```

Also add `review_total: int = 0` and `review_list: list[dict[str, Any]] =
Field(default_factory=list)` to `AnomaliesReport` (models.py). The
`cluster_annotations` table is loaded in the C1 bridge step (alongside
`record_freshness` and `field_history`).

`SELECT *` + a per-query `zombie_cols` capture (not the earlier `cols`) because
`_row_to_cluster_info` (db.py:1004) expects full device rows and
`conn.description` reflects only the most recent query. Drift SQL confirmed
valid: `source_records` has `cluster_id`, `serial_number`, `source` (all
VARCHAR — string comparisons are safe).

### D2. Surface unmapped-field diagnostics from `extra_attributes`

> **Corrected approach:** the original draft added a `dead_letters` accumulation
> inside `walker._collect_extra_attributes()`. Verified redundant: unmapped keys
> are **already preserved per record** in the `extra_attributes` JSON column
> (walker.py:144-171 — the exact keys the draft would have dead-lettered), and
> the valentine stage already builds a `v_extra` view over those keys
> (`api/schema.py: create_extra_view`, called from `run_valentine_stage()`,
> pipeline.py:508-511). Changing `walk_feed()`'s return shape would break
> `export_for_splink.py` and its tests for zero new information.

**File:** `src/zentinull/api/db.py`
**Change:** Add a `unmapped_fields()` method that aggregates directly from the
mesh (no pipeline change):

```python
def unmapped_fields(self, limit: int = 100) -> list[dict[str, Any]]:
    """Top unmapped raw fields per source, from extra_attributes JSON."""
    conn = self._conn()
    try:
        rows = conn.execute("""
            SELECT source, je.key AS field, COUNT(*) AS occurrences
            FROM source_records,
                 json_each(CASE WHEN extra_attributes IN ('', '{}')
                                THEN '{}' ELSE extra_attributes END) je
            GROUP BY source, je.key
            ORDER BY occurrences DESC
            LIMIT ?
        """, [limit]).fetchall()
        return [
            {"source": r[0], "field": r[1], "occurrences": r[2]} for r in rows
        ]
    finally:
        conn.close()
```

Expose as `GET /diagnostics/unmapped-fields` in `api/router.py` (same
`_db(request)` + `model_dump` convention as other endpoints).

**Verification:** Run `python serve.py load`, then
`curl :8001/diagnostics/unmapped-fields` — returns non-empty field counts for
at least one source.

---

## Phase E — API Surface (3 files, ~130 lines)

### Goal
Add SOT tags and drift audit to `DeviceStory`. Add `/device/{query}/history`
endpoint. Extend `AnomaliesReport`.

### E1. Extend `AnomaliesReport`

**File:** `src/zentinull/api/models.py`
**Change:** Add fields after existing `no_serial_list` (line 102):

```python
zombies: int = 0
zombie_list: list[ClusterInfo] = Field(default_factory=list)
hardware_drift: int = 0
hardware_drift_list: list[dict[str, Any]] = Field(default_factory=list)
```

### E2. Add SOT + drift fields to `DeviceStory`

**File:** `src/zentinull/api/models.py`
**Change:** Add fields after `records` in `DeviceStory` (line 65):

```python
sot: dict[str, dict[str, str]] = Field(default_factory=dict)
# {"name": {"value": "WS28", "source": "sp", "priority": "primary"}, ...}
drift_audit: list[dict[str, Any]] = Field(default_factory=list)
# [{"field": "serial", "label": "Serial Number", "sources": {...}, "verdict": "MATCH"}, ...]
```

### E3. Populate SOT in `_build_story()`

**File:** `src/zentinull/api/db.py`
**Change:** Inside `_build_story()` (line 181), after fetching source_records rows
and before returning `DeviceStory`, resolve SOT and drift:

```python
from ..manifest import load_manifest
from ..resolve.sot import sot_resolve

profile = load_manifest().profiles["device"]
# source_records columns ARE the profile field names — no alias layer exists.
# Skip meta + derived columns; keep only data-bearing fields.
_META = {"source", "source_id", "extra_attributes"}
data_fields = [
    f for f in profile.fields if f not in _META and f not in profile.derived
]

# Build per-source record maps for SOT resolution
src_records: dict[str, dict[str, Any]] = {}
for r in rows:
    rd = dict(zip(sr_cols, r, strict=True))
    src = rd.get("source", "")
    if src not in src_records:
        src_records[src] = {}
    for field in data_fields:
        val = _safe(rd.get(field))
        if val:
            src_records[src][field] = val

coverage = {k: s.coverage for k, s in load_manifest().systems.items()}
sot_result = sot_resolve(profile, src_records, coverage=coverage)
device_sot: dict[str, dict[str, str]] = {}
for field, (value, source, priority) in sot_result.items():
    device_sot[field] = {
        "value": value or "",
        "source": source or "",
        "priority": priority,
    }

# Build drift audit — per-field cross-source comparison
drift_audit: list[dict[str, Any]] = []
for field in data_fields:
    values: dict[str, str] = {}
    for src in src_records:
        val = _safe(src_records[src].get(field, ""))
        if val:
            values[src] = val
    if len(values) >= 2:
        unique = set(v.lower() for v in values.values())
        verdict = "MATCH" if len(unique) == 1 else "MISMATCH"
    elif len(values) == 1:
        verdict = "SINGLE_SOURCE"
    else:
        continue
    drift_audit.append({
        "field": field,
        "label": field.replace("_", " ").title(),
        "sources": values,
        "verdict": verdict,
    })
```

> **Corrected (verified):** `ResolutionProfile` has **no `fields_aliases` or
> `comparison_fields` attributes** (types.py:158-169) — the original snippet
> AttributeErrors immediately. `source_records` column names are exactly the
> profile field names (`name`, `serial_number`, ...), so no alias mapping is
> needed; iterate `profile.fields` minus meta/derived. SOT keys therefore use
> `name` (see corrected A3), not `hostname`/`device_name`.
> `load_manifest()` exists at `manifest/__init__.py:81` with signature
> `(project: str | None = None)`; cache the profile at MeshDB init rather than
> re-loading per request.

Pass into the `DeviceStory(...)` return at line 282:

```python
return DeviceStory(
    ...,
    sot=device_sot,
    drift_audit=drift_audit,
)
```

### E4. Add `/device/{query}/history` endpoint

**File:** `src/zentinull/api/router.py`
**Change:** Add after the attachments endpoint (line 273) and before
`/device/{query}/stats` (line 284). `_db(request)` (line 63) and
`_resolve_cluster(db, query)` (line 328) exist with these signatures;
returning `.model_dump()` under `response_model` matches the file's existing
convention. `field_history` exists in DuckDB only after the corrected C1
load-time bridge runs.

While-in-there: `/device/{query}/trace` (router.py:114) is the **only** device
endpoint with no `response_model` — add a `DeviceTraceResponse` model in the
same models.py pass, closing the last untyped hole in the device API.

```python
from .models import DeviceHistoryResponse, HistoryEntry

@router.get("/device/{query}/history", response_model=DeviceHistoryResponse)
async def device_history(query: str, request: Request) -> DeviceHistoryResponse:
    """Field change history for a device cluster."""
    log.info({"event": "request", "endpoint": "/device/{query}/history", "query": query})
    db = _db(request)
    cluster_id = _resolve_cluster(db, query)

    conn = db._conn()
    try:
        rows = conn.execute(
            "SELECT cluster_id, source, field, old_value, new_value, "
            "changed_at::VARCHAR FROM field_history "
            "WHERE cluster_id = ? ORDER BY changed_at DESC LIMIT 100",
            [cluster_id],
        ).fetchall()
        cols = [d[0] for d in conn.description]
        history = [
            HistoryEntry(
                field=r[cols.index("field")],
                old_value=r[cols.index("old_value")] or "",
                new_value=r[cols.index("new_value")] or "",
                changed_at=r[cols.index("changed_at")] or "",
                source=r[cols.index("source")] or "",
            ).model_dump()
            for r in rows
        ]
    finally:
        conn.close()
    return DeviceHistoryResponse(
        query=query,
        cluster_id=cluster_id,
        history=history,
    ).model_dump()
```

Add new models in `api/models.py`:

```python
class HistoryEntry(BaseModel):
    field: str
    old_value: str = ""
    new_value: str = ""
    changed_at: str = ""
    source: str = ""

class DeviceHistoryResponse(BaseModel):
    query: str
    cluster_id: str
    history: list[HistoryEntry] = Field(default_factory=list)
```

**Verification:** `pytest tests/test_serve.py -v -k history` (new test) or
`curl http://localhost:8001/device/ws28/history` after running pipeline.

---

## Phase F — Runtime Hardening (3 files, ~90 lines)

### Goal
Lazy config loading (no import-time eval), timeout middleware,
per-request connection singleton, startup validation.

### F1. Lazy config loading

**File:** `src/zentinull/config.py`
**Change:** Replace lines 80-81 (`_load_dotenv()` + `PATHS = resolve_paths()`):

```python
from functools import lru_cache
from threading import Lock

_config_loaded: bool = False
_config_lock = Lock()

def get_paths(project: str | None = None) -> ProjectPaths:
    """Lazily resolve project paths. Safe to call at import time."""
    global _config_loaded
    if not _config_loaded:
        with _config_lock:
            if not _config_loaded:
                _load_dotenv()
                _config_loaded = True
    return resolve_paths(project)

@lru_cache(maxsize=1)
def get_config() -> Config:
    """Lazily load and cache full config. Thread-safe."""
    return Config(
        paths=get_paths(),
        # ... all env-var lookups moved here
    )
```

All callers that previously imported `PATHS` or `DATA_DIR` etc. at module scope
now call `get_paths()` or `get_config()`. Corrections (verified):

- **No `Config` class exists** in config.py — `get_config()` must define it (a
  frozen dataclass holding `paths` + the env-var settings currently evaluated
  at module scope: `API_HOST/API_PORT`, `LOG_*`, ingestor auth constants,
  `SPLINK_*`).
- The true module-scope `PATHS` importers are **10 files in src/** —
  `export_for_splink.py`, `valentine.py`, `api/db.py`, `api/server.py`,
  `cli/backup.py`, `cli/db_mgmt.py`, `cli/pipeline.py`, `cli/status.py`,
  `ingestors/base.py`, `resolve/splink_runner.py` — plus 3 scripts
  (`build_training_set.py`, `run_splink.py`, `seed_demo_data.py`).
  **`worker.py` does not import PATHS** (the original list was wrong), and
  `serve.py`'s three imports are already function-local.
- `api/server.py` also imports and calls `_load_dotenv` directly (server.py:12,
  18) — remove that once `get_paths()` owns dotenv loading.
- **`@lru_cache` on `get_config()` conflicts with `ZENTINULL_PROJECT` /
  `--project` switching** (see `tests/test_project_isolation.py`, which relies
  on `resolve_paths(project)` re-evaluating). Keep `get_paths(project)`
  uncached, or key the cache by project.
- **Delete the backward-compat alias block while in there** (config.py:83-95:
  `DATA_DIR`, `EXPORT_DIR`, `CSV_DIR`, `SPLINK_OUTPUT_DIR`, `BENCHMARKS_DIR`,
  `MESH_DB`, `STATUS_FILE`, `PIPELINE_LOG`). Verified: zero module-scope
  importers anywhere in src/, scripts/, serve.py, or dashboard.py — everything
  imports `PATHS` directly. Re-grep once before deleting.

Replace `from ..config import PATHS` with `from ..config import get_paths` and
call `get_paths()` at function entry.

**Verification:** `python -c "from zentinull.config import get_paths; print(get_paths())"`
works without `.env` present.

### F2. Timeout middleware

**File:** `src/zentinull/api/server.py`
**Change:** After the existing `add_request_id` middleware (server.py:72-104),
add (also add `from fastapi.responses import JSONResponse` — **not currently
imported**):

```python
@app.middleware("http")
async def timeout_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Enforce 30s request deadline. Log WARNING at 5s."""
    deadline = 30.0
    try:
        start = time.time()
        response = await asyncio.wait_for(call_next(request), timeout=deadline)
        elapsed = time.time() - start
        if elapsed > 5.0:
            log.warning({"event": "slow_request", "path": request.url.path,
                         "elapsed": round(elapsed, 2)})
        return response
    except asyncio.TimeoutError:
        log.error({"event": "request_timeout", "path": request.url.path,
                   "deadline": deadline})
        return JSONResponse(
            {"error": "Request timed out", "deadline": deadline},
            status_code=504,
        )
```

**Verification:** Add a `time.sleep(35)` in a test endpoint handler. Hit it.
Returns 504 with the error body.

### F3. Per-request connection singleton

**File:** `src/zentinull/api/server.py`
**Change:** After timeout middleware, add:

```python
@app.middleware("http")
async def request_db_conn(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Open one DuckDB connection per request, close after response."""
    db: MeshDB | None = getattr(request.app.state, "db", None)
    if db is None:
        return await call_next(request)
    from .db import MeshDB
    conn = db._conn()
    request.state.db_conn = conn
    try:
        return await call_next(request)
    finally:
        conn.close()
```

Then in `_db(request)`, prefer `request.state.db_conn` if available — return
wrapper that uses the singleton connection. The per-request connection means
`device_trace()`, `device_metrics()`, and `device_timeline()` can skip their
own `self._conn()` calls and reuse the middleware-opened connection.

> **Scope note (verified):** `_db()` returns a `MeshDB` instance, not a
> connection, and `device_trace`/`device_metrics`/`device_timeline` each call
> `self._conn()` internally (db.py:49, 572, 659, 823). Reusing the middleware
> connection means threading an optional `conn` parameter through those MeshDB
> methods — a wider refactor than the middleware alone. Acceptable staged
> version: land the middleware first, migrate methods one at a time.

**Verification:** Add logging to `_conn()` — observe one open per request
for compound endpoints, not N per sub-query.

### F4. Startup `validate_config()`

**File:** `src/zentinull/api/server.py`
**Change:** Inside `lifespan()` (server.py:36), before the scheduler task is
created (line 46), add:

```python
from ..config import validate_config
for w in validate_config():
    log.warning({"event": "config_warning", "message": w})
```

`validate_config()` already exists in `config.py` at line 156 (returns
`list[str]` of warnings) — just wire it into lifespan.

**Verification:** Start server with a missing credential. Logs a config warning
but still starts (degraded mode).

---

## Phase G — Tests (~320 lines, 6 test files)

### G1. `test_os_family.py` + `test_validate.py`

Two small test files replacing the originally planned `test_semantic.py`
(the semantic reconciliation port was dropped — see Phase B redesign):
- `tests/test_os_family.py`: parametrized unit tests for
  `normalize_os_family()` — Windows editions collapse to one family
  ("Windows 10 Pro" ≡ "Windows 10 Enterprise" ≡ "Microsoft Windows 11"),
  macOS versions, iOS/iPadOS, Android, Linux distros, sentinel values → "",
  unknown OS passthrough.
- `tests/test_validate.py`: unit tests for `validate_clusters()` against a
  small tmp-path CSV — a cluster with two distinct serials (SERIAL_CONFLICT),
  one serial split across two clusters (SPLIT_IDENTITY), and a clean cluster
  producing no annotations.

### G2. `test_sot.py`

Unit tests for `sot_resolve()` with 5 scenarios:
- Primary source present → picks primary
- Primary missing, secondary present → picks secondary
- Both missing → picks best_effort, **highest declared coverage wins**
  (two sources hold values; assert the higher-coverage one is chosen)
- All missing → returns empty string

### G3. `test_field_history.py`

Integration test against SQLite:
- Insert a record → upsert with same data → zero history rows
- Upsert with changed field → one history row with old/new values
- Upsert with changed field to sentinel value → one history row

### G4. Extend router + mesh tests

File is **`tests/api/test_router_endpoints.py`** (not `tests/`); both
`TestDeviceLookup` (line 15) and `TestAnomalies` (line 236) exist. But these
tests run against a **mocked MeshDB** (`tests/api/conftest.py:261-271`) —
asserting SOT priority there only tests the mock. Split the assertions:
- `tests/api/test_router_endpoints.py`: shape only — `sot`/`drift_audit` keys
  accepted by `DeviceStory`, `"zombies"`/`"hardware_drift"` pass through
  `AnomaliesReport` (mock returns them).
- `tests/api/test_db_mesh.py` (against `seeded_meshdb`): behavioral —
  `story.sot["name"]["priority"] == "primary"` for a seeded multi-source
  cluster; `anomalies()["hardware_drift"]` counts a seeded serial conflict.

---

## Execution Order

| Phase | Dependencies | Files Changed | Lines | Can Parallel? |
|-------|-------------|---------------|-------|---------------|
| A SOT Hierarchy (incl. SOT-generated DEVICES_SQL) | None | 5 | ~140 | — |
| B Splink Inputs + Validation | None | 6 | ~90 | With A |
| C Change Tracking | None | 3 | ~120 | With A, B |
| D Diagnostics | C1 bridge (record_freshness + field_history load) | 2 | ~90 | After C1 |
| E API Surface | A (sot_resolve), C (field_history in DuckDB) | 3 | ~130 | After A, C |
| F Runtime Hardening | None | 3 | ~90 | With A-E |
| G Tests | phases A-F | 6 | ~320 | After A-F |

Phases A, B, C, and F are mutually independent — can run in parallel in a
4-agent fan-out. Phase D depends on C1's load-time bridge (`record_freshness`).
Phase E depends on A (`sot_resolve()`) and C (DuckDB `field_history`).
Phase G is always last.

**Total:** ~1,000 net new lines, minimal deletions, ~23 files touched.

---

## Verification Log (2026-07-16)

This plan was ground-truth verified against both repos. Confirmed-exact line
anchors: types.py:169, manifest/__init__.py checks #1-#10 (146-235),
analyzer.py:30/46/69/122/141/151/243/559 (ends ~836), base.py:204/243/245/259,
pipeline.py:840/845 (inside `run_pipeline`, def 790), db.py:181/282/502/1004,
models.py:54/65/94/102, router.py:63/273/284/328, config.py:80-81/156,
server.py:72-104. Corrections applied above: A3 field/system names, B1
`_DOMAIN_SUFFIX_RE` provenance + `resolve_user_full_name` DB dependency, B2
clusters.csv path + `skip_semantic`/nuance scoping, C SQLite-authoritative
history + raw_json diffing + load-time bridge, D1 `record_freshness`
prerequisite, D2 extra_attributes-based diagnostics (walker unchanged), E3
profile attribute fix, F1 call-site list + Config class + lru_cache caveat,
F2 JSONResponse import, G1/G4 test-file reality.

**Phase B redesign (same day):** the `_is_equivalent()`/nuance.json port was
replaced with an `os_family` derived-field transform (normalizer.py +
transforms.py REGISTRY at transforms.py:16-22 + manifest derived + inline
computation in export_for_splink.py:78-88, `_COMPUTED_FIELDS` at line 21) and
a read-only `validate_clusters()` annotation pass — Splink remains the single
resolution authority; nothing rewrites its cluster assignments.
