"""
Pipeline orchestrator — streaming subprocess model with per-source ingest control,
Splink option support, and temp-and-swap DuckDB loading.

Replaces the capture_output=True subprocess model in src/zentinull/pipeline.py.

Two loading modes:
  - Full load (run_load): reads clusters.csv from Splink, rebuilds entire mesh
  - Incremental load (run_incremental_load): upserts per-source, rebuilds devices only
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from functools import lru_cache
from typing import Any

import duckdb

from ..api.schema import (
    ATTACHMENTS_SQL,
    EVENTS_SQL,
    INDEXES_SQL,
    METRICS_SQL,
    SOURCE_RECORDS_SQL,
    build_devices_sql,
    create_enriched_view,
    create_extra_view,
)
from ..config import ROOT, get_paths
from ..export_for_splink import _FEED_SOURCE_MAP, normalize_record
from ..export_for_splink import export as _run_export_fn
from ..ingest_adapter import run_ingest as _run_ingest_from_adapter
from ..ingestors.base import validate_identifier
from ..logging_config import StepTimer, get_logger
from ..manifest import Manifest, get_system_feeds, load_manifest
from ..manifest.types import Role
from ..manifest.walker import walk_feed
from ..resolve.attach import resolve_feed_attachments
from ..valentine import run_valentine
from .render import is_brutalist_enabled, render_banner, render_stage
from .status import record_done, record_fail, record_start
from .streaming import run_streaming

if sys.platform != "win32":
    import fcntl  # POSIX advisory file locking (absent on Windows)


def _try_flock(fh: Any) -> bool:
    """Non-blocking exclusive lock. True if acquired.

    Best-effort no-op on Windows (no fcntl) — single-host runs don't contend.
    """
    if sys.platform != "win32":
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
    return True


PYTHON = sys.executable or "python3"

log = get_logger("cli.pipeline")


@lru_cache(maxsize=1)
def _get_manifest() -> Manifest:
    """Load and cache the manifest for the current project."""
    return load_manifest()


def _build_source_map() -> dict[str, tuple[str, str]]:
    """Derive _SOURCE_MAP from manifest: system_key → (module_name, display_name)."""
    manifest = _get_manifest()
    return {key: (key, system.label) for key, system in manifest.systems.items()}


def _build_source_to_tables() -> dict[str, list[str]]:
    """Derive _SOURCE_TO_TABLES from manifest: system_key → list of anchor feed keys."""
    manifest = _get_manifest()
    result: dict[str, list[str]] = {}
    for system_key in manifest.systems:
        feed_keys = get_system_feeds(manifest, system_key)
        anchor_feeds = [fk for fk in feed_keys if manifest.feeds[fk].role == Role.ANCHOR]
        result[system_key] = anchor_feeds
    return result


# Module-level dicts derived from manifest
_SOURCE_MAP = _build_source_map()
_SOURCE_TO_TABLES = _build_source_to_tables()


# ── Ingest ────────────────────────────────────────────────────────────────────


def run_ingest(sources: list[str] | None = None, skip_sources: list[str] | None = None) -> dict[str, int]:
    """Run ingestors in-process via manifest-driven adapter.

    If sources is None, all systems in the manifest are run.
    Each system runs via its legacy ingest() function through the adapter.

    Returns dict of display_name → row_count.
    """
    manifest = _get_manifest()

    record_start("ingest")
    raw_results: dict[str, int] = {}

    with StepTimer(log, "ingest"):
        raw_results = _run_ingest_from_adapter(manifest, sources=sources, skip_sources=skip_sources)

    # Map system keys to display names for backward compatibility
    results: dict[str, int] = {}
    for system_key, count in raw_results.items():
        display_name = manifest.systems[system_key].label if system_key in manifest.systems else system_key
        results[display_name] = count

    succeeded = sum(1 for v in results.values() if v >= 0)
    record_done("ingest", total=len(results), succeeded=succeeded)
    return results


# ── Export ─────────────────────────────────────────────────────────────────────


def run_export() -> int:
    """Run zentinull.export_for_splink.export() in-process.

    Returns total record count (rows in the generated CSV, minus header).
    """
    paths = get_paths()
    record_start("export")
    with StepTimer(log, "export"):
        _run_export_fn()

    csv_path = paths.csv_dir / "devices.csv"
    tmp_path = paths.csv_dir / "devices.csv.tmp"
    if tmp_path.exists() and (not csv_path.exists() or tmp_path.stat().st_mtime > csv_path.stat().st_mtime):
        log.warning({"event": "export_stale_csv", "reason": "devices.csv locked, using .tmp"})
        csv_path = tmp_path
    if not csv_path.exists():
        raise FileNotFoundError(f"Export did not produce {csv_path}")

    with open(csv_path, encoding="utf-8") as f:
        total = sum(1 for _ in f) - 1  # minus header row

    record_done("export", total=total)

    # Record coverage baseline for drift detection
    _record_coverage_baseline()

    return max(total, 0)


def _record_coverage_baseline() -> None:
    """Compute per-source, per-field fill rates from devices.csv and persist."""
    import csv
    from collections import defaultdict

    from .status import record_coverage_baseline

    paths = get_paths()

    csv_path = paths.csv_dir / "devices.csv"
    if not csv_path.exists():
        return

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)

    manifest = _get_manifest()
    profile_fields = manifest.profiles["device"].fields

    baseline: dict[str, dict[str, int]] = {}
    for src, recs in by_source.items():
        n = len(recs)
        src_baseline: dict[str, int] = {}
        for field in profile_fields:
            if field in {"source", "source_id", "name_clean", "mac_clean", "extra_attributes"}:
                continue
            filled = sum(1 for r in recs if r.get(field, "").strip())
            src_baseline[field] = round(100 * filled / n) if n else 0
        baseline[src] = src_baseline

    record_coverage_baseline(baseline)


def export_source(source_key: str) -> int:
    """Export a single source to its own CSV file using the manifest walker.

    Returns record count for that source.
    """
    paths = get_paths()
    manifest = _get_manifest()
    profile = manifest.profiles["device"]
    splink_fields = list(profile.fields)
    if source_key not in _SOURCE_TO_TABLES:
        raise ValueError(f"Unknown source key: {source_key}")

    all_rows: list[dict[str, str]] = []
    feed_keys = _SOURCE_TO_TABLES[source_key]

    for feed_key in feed_keys:
        feed = manifest.feeds[feed_key]
        db_file = feed.system
        db_path = paths.data_dir / f"{db_file}.sqlite"
        if not db_path.exists():
            log.warning({"event": "skip", "source": feed_key, "reason": "db_not_found"})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        store_table = validate_identifier(feed.store)
        if store_table not in tables:
            log.warning({"event": "skip", "source": feed_key, "reason": "table_not_found"})
            conn.close()
            continue

        try:
            rows = conn.execute(f"SELECT * FROM {store_table}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": feed_key, "reason": "error", "error": str(e)})
            conn.close()
            continue

        extracted = walk_feed(feed, rows)

        for rec in extracted:
            normalize_record(rec, feed_key, splink_fields)
        all_rows.extend(extracted)
        conn.close()

    out_path = paths.csv_dir / f"{source_key}.csv"
    paths.csv_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=splink_fields)
        writer.writeheader()
        writer.writerows(all_rows)

    log.info({"event": "source_exported", "source": source_key, "records": len(all_rows)})
    return len(all_rows)


# ── Splink ─────────────────────────────────────────────────────────────────────


def run_splink(*, skip_training: bool = False, threshold: int | None = None) -> None:
    """Run scripts/run_splink.py as a streaming subprocess.

    If threshold is given, it is passed as an environment variable.
    """
    script = ROOT / "scripts" / "run_splink.py"
    if not script.exists():
        raise FileNotFoundError(f"Splink script not found: {script}")

    if skip_training:
        log.info({"event": "splink_skip_training", "note": "skip_training not yet implemented — running with defaults"})

    record_start("splink")

    env: dict[str, str] | None = None
    if threshold is not None:
        env = {**os.environ, "SPLINK_THRESHOLD": str(threshold)}

    with StepTimer(log, "splink"):
        try:
            run_streaming([PYTHON, str(script)], "splink", cwd=str(ROOT), env=env)
        except Exception as e:
            record_fail("splink", str(e))
            raise RuntimeError(f"Splink failed: {e}") from e

    record_done("splink")


# ── Load ───────────────────────────────────────────────────────────────────────


def _load_zbx_metrics(conn: duckdb.DuckDBPyConnection) -> int:
    """Load Zabbix items into the metrics table, linked to device clusters by hostid.

    Reads zbx.sqlite items, resolves each item's hostid → cluster_id via
    source_records (source='zbx', source_id=hostid), and inserts into metrics.
    Silently returns 0 if zbx.sqlite or items table is missing.
    """
    paths = get_paths()
    zbx_db = paths.data_dir / "zbx.sqlite"
    if not zbx_db.exists():
        return 0

    zbx_conn = sqlite3.connect(str(zbx_db))
    zbx_conn.row_factory = sqlite3.Row

    try:
        tables = [r[0] for r in zbx_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "hosts" not in tables or "items" not in tables:
            return 0

        # Build hostid → hostname lookup from hosts raw store
        host_rows = zbx_conn.execute("SELECT raw_json FROM hosts").fetchall()
        host_map: dict[str, str] = {}
        for r in host_rows:
            try:
                hdict = json.loads(r["raw_json"])
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            hid = str(hdict.get("hostid", "") or "")
            hn = str(hdict.get("host", "") or hdict.get("name", "") or "")
            if hid:
                host_map[hid] = hn

        item_rows = zbx_conn.execute("SELECT raw_json FROM items").fetchall()
        if not item_rows:
            return 0

        inserted = 0
        for item in item_rows:
            raw = item["raw_json"]
            try:
                item_dict = json.loads(raw)
            except (json.JSONDecodeError, ValueError, TypeError):
                log.warning({"event": "zbx_malformed_json", "raw_json_len": len(raw)})
                continue

            hostid = str(item_dict.get("hostid", "") or "")
            if not hostid:
                continue

            # Resolve hostid → cluster_id from source_records
            row = conn.execute(
                "SELECT cluster_id FROM source_records WHERE lower(source) = 'zbx' AND source_id::VARCHAR = ?",
                [hostid],
            ).fetchone()
            if row is None:
                # Fallback: match by name_clean (handles CSV without source_id)
                hostname = host_map.get(hostid, "")
                if not hostname:
                    continue
                name_clean = hostname.lower().split(".")[0]
                row = conn.execute(
                    "SELECT cluster_id FROM source_records WHERE lower(source) = 'zbx' AND lower(name_clean) = ?",
                    [name_clean.lower()],
                ).fetchone()
                if row is None:
                    continue

            cluster_id: str = str(row[0])
            metric_name: str = str(item_dict.get("name", ""))
            lastvalue: str = str(item_dict.get("lastvalue", ""))
            lastclock: str = str(item_dict.get("lastclock", ""))
            key_: str = str(item_dict.get("key_", ""))
            value_type: str = str(item_dict.get("value_type", ""))
            units: str = str(item_dict.get("units", ""))

            if not lastclock or not metric_name:
                continue

            # Parse timestamp from Unix epoch
            try:
                recorded_at = datetime.fromtimestamp(int(lastclock))
            except (ValueError, TypeError):
                continue

            # Parse numeric value; fall back to text
            value: float | None = None
            text_value: str = ""
            if lastvalue:
                try:
                    value = float(lastvalue)
                except (ValueError, TypeError):
                    text_value = lastvalue

            # Build tags list
            tags: list[str] = []
            if key_:
                tags.append(f"key={key_}")
            if value_type:
                tags.append(f"value_type={value_type}")
            if units:
                tags.append(f"units={units}")

            conn.execute(
                """
                INSERT INTO metrics (cluster_id, source, metric_name, value, text_value, tags, recorded_at)
                VALUES (?, 'zbx', ?, ?, ?, ?, ?)
                """,
                [cluster_id, metric_name, value, text_value, tags, recorded_at],
            )
            inserted += 1

        if inserted:
            log.info({"event": "zbx_metrics_loaded", "rows": inserted})
        return inserted

    finally:
        zbx_conn.close()


def run_load() -> int:
    """Load clusters.csv into DuckDB mesh using temp-and-swap.

    Builds mesh in data/mesh.duckdb.tmp, then atomically renames to mesh.duckdb.
    Also bridges field_history, record_freshness, and cluster_annotations
    from per-source SQLite stores and validate.py output.
    Returns total device count.
    """
    paths = get_paths()
    csv_path = paths.splink_output_dir / "clusters.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found — run splink first")

    mesh_path = paths.mesh_path
    tmp_path = paths.data_dir / "mesh.duckdb.tmp"

    record_start("load")

    with StepTimer(log, "duckdb.load"):
        # Clean up stale temp file
        if tmp_path.exists():
            tmp_path.unlink()

        conn = duckdb.connect(str(tmp_path))

        try:
            # ── Core tables ────────────────────────────────────────────────
            conn.execute(SOURCE_RECORDS_SQL, [str(csv_path)])

            # Build devices table: one row per cluster, consolidated
            profile = _get_manifest().profiles["device"]
            conn.execute(build_devices_sql(profile))

            # Metrics & Events (append-only)
            conn.execute(METRICS_SQL)
            conn.execute(EVENTS_SQL)
            conn.execute(ATTACHMENTS_SQL)

            # ── Bridge: field_history from per-source SQLite stores ────────
            conn.execute("""
                CREATE TABLE field_history (
                    cluster_id   VARCHAR,
                    source       VARCHAR NOT NULL,
                    source_id    VARCHAR NOT NULL,
                    field        VARCHAR NOT NULL,
                    old_value    VARCHAR,
                    new_value    VARCHAR,
                    changed_at   TIMESTAMP,
                    batch_id     VARCHAR
                )
            """)
            conn.execute("CREATE INDEX idx_fh_cluster ON field_history(cluster_id)")
            conn.execute("CREATE INDEX idx_fh_field ON field_history(field)")

            for sqlite_path in sorted(paths.data_dir.glob("*.sqlite")):
                try:
                    src_conn = sqlite3.connect(str(sqlite_path))
                    has_fh = src_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='field_history'"
                    ).fetchone()
                    if not has_fh:
                        src_conn.close()
                        continue

                    fh_rows = src_conn.execute(
                        "SELECT source, source_id, field, old_value, new_value, changed_at, batch_id FROM field_history"
                    ).fetchall()
                    if fh_rows:
                        conn.executemany(
                            "INSERT INTO field_history "
                            "(source, source_id, field, old_value, new_value, changed_at, batch_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            fh_rows,
                        )
                    src_conn.close()
                except Exception:
                    log.warning({"event": "bridge_fh_skip", "store": str(sqlite_path)})

            # Map source_id → cluster_id via source_records
            conn.execute("""
                UPDATE field_history
                SET cluster_id = source_records.cluster_id
                FROM source_records
                WHERE field_history.source = source_records.source
                  AND field_history.source_id = source_records.source_id
            """)

            # ── Bridge: record_freshness from per-source SQLite stores ─────
            conn.execute("""
                CREATE TABLE record_freshness (
                    source       VARCHAR NOT NULL,
                    source_id    VARCHAR NOT NULL,
                    fetched_at   TIMESTAMP
                )
            """)

            # Build store_name → source key mapping from manifest
            store_to_source: dict[str, str] = {}
            for feed in _get_manifest().feeds.values():
                store_to_source[feed.store] = feed.system

            for sqlite_path in sorted(paths.data_dir.glob("*.sqlite")):
                try:
                    src_conn = sqlite3.connect(str(sqlite_path))
                    has_raw = src_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_store'"
                    ).fetchone()
                    if not has_raw:
                        src_conn.close()
                        continue

                    # Derive source key from store name via mapping
                    store_name = sqlite_path.stem
                    source_key = store_to_source.get(store_name, store_name)

                    fresh_rows = src_conn.execute(
                        "SELECT source_id, fetched_at FROM raw_store WHERE fetched_at IS NOT NULL"
                    ).fetchall()
                    if fresh_rows:
                        conn.executemany(
                            "INSERT INTO record_freshness (source, source_id, fetched_at) VALUES (?, ?, ?)",
                            [(r[0], source_key, r[1]) for r in fresh_rows],
                        )
                    src_conn.close()
                except Exception:
                    log.warning({"event": "bridge_freshness_skip", "store": str(sqlite_path)})

            # ── Bridge: cluster_annotations from validate.py output ────────
            annotations_csv = paths.splink_output_dir / "cluster_annotations.csv"
            if annotations_csv.exists():
                conn.execute(
                    "CREATE TABLE cluster_annotations AS SELECT * FROM read_csv_auto(?, all_varchar=true)",
                    [str(annotations_csv)],
                )
            else:
                conn.execute("""
                    CREATE TABLE cluster_annotations (
                        cluster_id VARCHAR, kind VARCHAR,
                        field VARCHAR, values VARCHAR, detail VARCHAR
                    )
                """)

            # ── Metrics & Indexes ──────────────────────────────────────────
            _load_zbx_metrics(conn)
            conn.execute(INDEXES_SQL)
            conn.execute("CHECKPOINT")

            device_count_row = conn.execute("SELECT COUNT(*) FROM devices").fetchone()
            record_count_row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            if device_count_row is None or record_count_row is None:
                raise RuntimeError("Failed to read row counts from temp mesh")
            device_count: int = int(device_count_row[0])
            record_count_val: int = int(record_count_row[0])
            conn.close()

            # Swap: atomically replace old mesh with temp
            os.replace(tmp_path, mesh_path)

            log.info({"event": "mesh_loaded", "devices": device_count, "records": record_count_val})

        except Exception:
            conn.close()
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    record_done("load", devices=device_count)
    return device_count


def run_valentine_stage() -> int:
    """Run Valentine schema matching and refresh DuckDB views.

    Discovers cross-source column relationships via COMA matcher,
    merges with manual registry, and regenerates v_extra + v_device_enriched.
    Called after run_load(), before run_attach().
    """
    paths = get_paths()
    record_start("valentine")
    with StepTimer(log, "valentine"):
        registry = run_valentine()

        conn = duckdb.connect(str(paths.mesh_path), read_only=False)
        try:
            create_extra_view(conn)
            create_enriched_view(conn, registry)
            conn.execute("CHECKPOINT")
            row = conn.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_name='v_device_enriched'"
            ).fetchone()
            enriched_cols = int(row[0]) if row else 0
        finally:
            conn.close()

    log.info({"event": "valentine_complete", "enriched_columns": enriched_cols})
    record_done("valentine", total=enriched_cols)
    return enriched_cols


def run_attach() -> int:
    """Run attachment resolution for all ATTACHMENT feeds.

    Called after run_load() and run_valentine_stage() produce a fresh mesh.
    Reads raw stores, resolves links, writes into the attachments table.
    """
    paths = get_paths()
    if not paths.mesh_path.exists():
        log.warning({"event": "attach_skip", "reason": "mesh_not_found"})
        return 0

    manifest = _get_manifest()
    attachment_feeds = [k for k, v in manifest.feeds.items() if v.role == Role.ATTACHMENT]

    if not attachment_feeds:
        log.info({"event": "attach_skip", "reason": "no_attachment_feeds"})
        return 0

    record_start("attach")
    total = 0

    with StepTimer(log, "attach"):
        for feed_key in attachment_feeds:
            feed = manifest.feeds[feed_key]
            sqlite_db_path = str(paths.data_dir / f"{feed.system}.sqlite")

            try:
                rows = resolve_feed_attachments(feed, feed_key, str(paths.mesh_path), sqlite_db_path)
            except Exception as e:
                log.error({"event": "attach_failed", "feed": feed_key, "error": str(e)})
                continue

            if not rows:
                log.info({"event": "attach_empty", "feed": feed_key})
                continue

            conn = duckdb.connect(str(paths.mesh_path), read_only=False)
            try:
                conn.execute(ATTACHMENTS_SQL)
                # Remove stale links for this feed
                source_ids = [r["source_id"] for r in rows]
                conn.execute(
                    "DELETE FROM attachments WHERE feed_key = ? AND source_id = ANY(?)",
                    [feed_key, source_ids],
                )
                # Insert new links
                conn.executemany(
                    "INSERT INTO attachments (cluster_id, feed_key, source_id, field, value, confidence, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r["cluster_id"],
                            r["feed_key"],
                            r["source_id"],
                            r["field"],
                            r["value"],
                            r["confidence"],
                            r["payload"],
                        )
                        for r in rows
                    ],
                )
            finally:
                conn.close()

            total += len(rows)
            log.info({"event": "attach_done", "feed": feed_key, "rows": len(rows)})

    record_done("attach", total=total)
    return total


def run_incremental_load(sources: list[str]) -> int:
    """Upsert per-source CSVs into DuckDB and rebuild devices table.

    Unlike run_load() which rebuilds everything from Splink output,
    this preserves existing cluster_ids and only adds/updates source records.
    New records get temporary cluster_ids until next Splink run merges them.

    Returns device count after rebuild.
    """
    paths = get_paths()
    if not paths.mesh_path.exists():
        log.warning({"event": "incremental_load_skip", "reason": "mesh_not_found"})
        return 0

    record_start("incremental_load")

    # Load profile fields for CSV column iteration
    manifest = _get_manifest()
    profile = manifest.profiles["device"]
    splink_fields = list(profile.fields)

    with StepTimer(log, "incremental_load"):
        conn = duckdb.connect(str(paths.mesh_path), read_only=False)
        try:
            import contextlib

            with contextlib.suppress(Exception):
                conn.execute("SELECT DISTINCT cluster_id FROM source_records").fetchall()

            for source_key in sources:
                csv_path = paths.csv_dir / f"{source_key}.csv"
                if not csv_path.exists():
                    log.warning({"event": "incremental_skip", "source": source_key, "reason": "csv_not_found"})
                    continue

                # Derive all source column values for this system — a system can
                # have multiple ANCHOR feeds each mapping to a different source
                # value (e.g. fg_clients → 'fg', fg_dhcp → 'fg_dhcp'). Query
                # existing_ids for ALL of them so cross-feed records aren't
                # treated as "new" on every incremental sync.
                import uuid

                source_values: list[str] = []
                for fk in _SOURCE_TO_TABLES.get(source_key, []):
                    sv = _FEED_SOURCE_MAP.get(fk, fk)
                    if sv not in source_values:
                        source_values.append(sv)

                placeholders = ", ".join("?" for _ in source_values)
                existing_rows = conn.execute(
                    f"SELECT source_id, source, cluster_id FROM source_records WHERE source IN ({placeholders})",
                    source_values,
                ).fetchall()
                # Key by (source_id, source) because the same source_id can
                # appear under different source column values (e.g. same MAC in
                # both fg_clients and fg_dhcp). When duplicate keys exist (from
                # earlier buggy runs), prefer a Splink-assigned cluster_id over
                # a temporary new-* id so the collapse keeps the resolved cluster.
                cluster_by_key: dict[tuple[str, str], str] = {}
                for r in existing_rows:
                    key = (r[0], r[1])
                    cid = r[2] or ""
                    prev = cluster_by_key.get(key)
                    if prev is None or (prev.startswith("new-") and not cid.startswith("new-")):
                        cluster_by_key[key] = cid

                new_count = 0
                updated_count = 0

                all_cols = ["cluster_id"] + splink_fields
                insert_sql = (
                    f"INSERT INTO source_records ({', '.join(all_cols)}) VALUES ({', '.join(['?'] * len(all_cols))})"
                )

                with open(csv_path, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row_dict in reader:
                        sid = row_dict.get("source_id", "")
                        src = row_dict.get("source", "")

                        key = (sid, src)
                        cluster_id = cluster_by_key.get(key)
                        if cluster_id is None:
                            cluster_id = f"new-{source_key}-{uuid.uuid4().hex[:8]}"
                            cluster_by_key[key] = cluster_id
                            new_count += 1
                        else:
                            updated_count += 1
                        # True upsert: DELETE + INSERT keeps exactly one row per
                        # (source, source_id), collapsing any duplicates that
                        # accumulated from previous runs while preserving the
                        # existing cluster_id.
                        conn.execute(
                            "DELETE FROM source_records WHERE source = ? AND source_id = ?",
                            [src, sid],
                        )
                        values = [cluster_id] + [row_dict.get(c, "") for c in splink_fields]
                        conn.execute(insert_sql, values)

                import_count = new_count + updated_count
                log.info(
                    {
                        "event": "source_upserted",
                        "source": source_key,
                        "imported": import_count,
                        "new": new_count,
                        "updated": updated_count,
                    }
                )

            conn.execute(build_devices_sql(profile))
            conn.execute("CHECKPOINT")

            device_count_row = conn.execute("SELECT COUNT(*) FROM devices").fetchone()
            record_count_row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            device_count = int(device_count_row[0]) if device_count_row else 0
            record_count = int(record_count_row[0]) if record_count_row else 0

            log.info({"event": "incremental_load_done", "devices": device_count, "records": record_count})
        finally:
            conn.close()

    record_done("incremental_load", devices=device_count)
    return device_count


def run_incremental_sync(sources: list[str]) -> int:
    """Full incremental cycle: ingest → export → upsert → rebuild devices.

    This is the high-frequency path (10-30 min intervals).
    Skips Splink — entity resolution runs separately on a slower schedule.

    Returns device count.
    """
    paths = get_paths()
    lock_path = paths.data_dir / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")  # noqa: SIM115
    have_lock = False
    try:
        try:
            if not _try_flock(lock_file):
                raise OSError
            have_lock = True
            lock_file.write(str(os.getpid()))
            lock_file.flush()
        except OSError:
            try:
                old_pid = int(lock_path.read_text().strip())
                msg = f"Pipeline already running (PID {old_pid}). Delete {lock_path} if stale."
            except Exception:
                msg = "Pipeline already running. Delete lock_path if stale."
            raise RuntimeError(msg) from None

        if is_brutalist_enabled():
            render_banner()
            render_stage("INGEST")
        log.info({"event": "sync_stage", "stage": "ingest", "sources": sources})
        manifest = _get_manifest()
        from ..ingest import runner  # lazy import to avoid cycles

        for source_key in sources:
            record_start(f"ingest_{source_key}")
            try:
                feed_counts = runner.run_system(source_key, manifest, incremental=True)
                record_done(f"ingest_{source_key}", total=sum(feed_counts.values()))
            except Exception as exc:
                log.exception({"event": "system_ingest_failed", "system": source_key})
                record_fail(f"ingest_{source_key}", error=str(exc))

        for source_key in sources:
            if source_key in _SOURCE_TO_TABLES:
                if is_brutalist_enabled():
                    render_stage(f"EXPORT {source_key.upper()}")
                log.info({"event": "sync_stage", "stage": "export", "source": source_key})
                export_source(source_key)

        if not paths.mesh_path.exists():
            log.warning({"event": "sync_skip", "reason": "mesh_not_found", "hint": "run full pipeline first"})
            return 0

        if is_brutalist_enabled():
            render_stage("UPSERT")
        log.info({"event": "sync_stage", "stage": "incremental_load"})
        device_count = run_incremental_load(sources)

        log.info({"event": "sync_complete", "sources": sources, "devices": device_count})
        return device_count
    finally:
        if have_lock:
            lock_file.close()
            import contextlib

            with contextlib.suppress(OSError):
                lock_path.unlink()
        else:
            lock_file.close()


# ── Pipeline ───────────────────────────────────────────────────────────────────


def run_pipeline(
    *,
    skip_ingest: bool = False,
    sources: list[str] | None = None,
    skip_sources: list[str] | None = None,
) -> None:
    """Run the full pipeline: ingest → export → splink → load → attach.

    Each stage is streamed and status-tracked independently.
    Uses a PID-based lock file to prevent concurrent pipeline runs.
    """
    paths = get_paths()

    # ── Concurrency guard ──────────────────────────────────────────────
    lock_path = paths.data_dir / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")  # noqa: SIM115
    have_lock = False
    try:
        try:
            if not _try_flock(lock_file):
                raise OSError
            have_lock = True
            lock_file.write(str(os.getpid()))
            lock_file.flush()
        except OSError:
            try:
                old_pid = int(lock_path.read_text().strip())
                msg = f"Pipeline already running (PID {old_pid}). Delete {lock_path} if stale."
            except Exception:
                msg = f"Pipeline already running. Delete {lock_path} if stale."
            raise RuntimeError(msg) from None

        if is_brutalist_enabled():
            render_banner()

        if not skip_ingest:
            if is_brutalist_enabled():
                render_stage("INGEST")
            log.info({"event": "pipeline_stage", "stage": "ingest"})
            run_ingest(sources=sources, skip_sources=skip_sources)

        if is_brutalist_enabled():
            render_stage("EXPORT")
        log.info({"event": "pipeline_stage", "stage": "export"})
        total = run_export()
        log.info({"event": "export_done", "records": total})

        if is_brutalist_enabled():
            render_stage("SPLINK")
        log.info({"event": "pipeline_stage", "stage": "splink"})
        run_splink()

        log.info({"event": "pipeline_stage", "stage": "validate"})
        record_start("validate")
        with StepTimer(log, "validate"):
            from ..resolve.validate import validate_clusters

            annotations = validate_clusters(paths.splink_output_dir / "clusters.csv")
        record_done("validate", annotations=len(annotations))

        if is_brutalist_enabled():
            render_stage("LOAD")
        log.info({"event": "pipeline_stage", "stage": "load"})
        device_count = run_load()

        if is_brutalist_enabled():
            render_stage("DISCOVER")
        log.info({"event": "pipeline_stage", "stage": "valentine"})
        enriched_cols = run_valentine_stage()

        if is_brutalist_enabled():
            render_stage("ATTACH")
        log.info({"event": "pipeline_stage", "stage": "attach"})
        attach_count = run_attach()
        log.info({"event": "attach_complete", "attachments": attach_count})

        log.info({"event": "pipeline_complete", "devices": device_count, "enriched": enriched_cols})
    finally:
        if have_lock:
            lock_file.close()
            import contextlib

            with contextlib.suppress(OSError):
                lock_path.unlink()
        else:
            lock_file.close()
