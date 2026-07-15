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
from typing import Any

import duckdb

from ..api.schema import ATTACHMENTS_SQL, DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL, SOURCE_RECORDS_SQL
from ..config import PATHS, ROOT
from ..export_for_splink import export as _run_export_fn
from ..ingest_adapter import run_ingest as _run_ingest_from_adapter
from ..logging_config import StepTimer, get_logger
from ..manifest import Manifest, get_system_feeds, load_manifest
from ..manifest.types import Role
from ..manifest.walker import walk_feed
from ..normalizer import normalize_mac, normalize_name, normalize_serial, strip_sentinels
from ..resolve.attach import resolve_feed_attachments
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


def _get_manifest() -> Manifest:
    """Load and cache the manifest for the current project."""
    if not hasattr(_get_manifest, "_cache"):
        _get_manifest._cache = load_manifest()  # type: ignore
    return _get_manifest._cache  # type: ignore


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

# Feed key → source column value mapping (preserves current CSV source values for backward compat)
_FEED_SOURCE_MAP: dict[str, str] = {
    "sp_devices": "sp",
    "me_ec": "me_ec",
    "me_mdm": "me_mdm",
    "fg_clients": "fg",
    "fg_dhcp": "fg_dhcp",
    "zbx_hosts": "zbx",
    "ad_computers": "ad",
    "sdp_assets": "sdp",
}

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
    record_start("export")
    with StepTimer(log, "export"):
        _run_export_fn()

    csv_path = PATHS.csv_dir / "devices.csv"
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

    csv_path = PATHS.csv_dir / "devices.csv"
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
        db_path = PATHS.data_dir / f"{db_file}.sqlite"
        if not db_path.exists():
            log.warning({"event": "skip", "source": feed_key, "reason": "db_not_found"})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if feed.store not in tables:
            log.warning({"event": "skip", "source": feed_key, "reason": "table_not_found"})
            conn.close()
            continue

        try:
            rows = conn.execute(f"SELECT * FROM {feed.store}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": feed_key, "reason": "error", "error": str(e)})
            conn.close()
            continue

        extracted = walk_feed(feed, rows)

        for rec in extracted:
            rec["source"] = _FEED_SOURCE_MAP.get(feed_key, feed_key)
            # Derived fields for Splink matching
            rec["name_clean"] = normalize_name(rec.get("name", ""))
            rec["mac_clean"] = normalize_mac(rec.get("mac_address", ""))
            rec["serial_number"] = normalize_serial(rec.get("serial_number", ""))
            if rec.get("manufacturer"):
                rec["manufacturer"] = rec["manufacturer"].lower()
            # Strip sentinels on all target fields
            for fld in splink_fields:
                if fld in ("source", "source_id", "name_clean", "mac_clean", "extra_attributes"):
                    continue
                rec[fld] = strip_sentinels(rec.get(fld, ""))
            # Fill missing fields
            for fld in splink_fields:
                if fld not in rec:
                    rec[fld] = ""

        all_rows.extend(extracted)
        conn.close()

    out_path = PATHS.csv_dir / f"{source_key}.csv"
    PATHS.csv_dir.mkdir(parents=True, exist_ok=True)
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
    zbx_db = PATHS.data_dir / "zbx.sqlite"
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
    Returns total device count.
    """
    csv_path = PATHS.splink_output_dir / "clusters.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found — run splink first")

    mesh_path = PATHS.mesh_path
    tmp_path = PATHS.data_dir / "mesh.duckdb.tmp"

    record_start("load")

    with StepTimer(log, "duckdb.load"):
        # Clean up stale temp file
        if tmp_path.exists():
            tmp_path.unlink()

        conn = duckdb.connect(str(tmp_path))

        try:
            # Load clusters CSV into source_records table
            conn.execute(SOURCE_RECORDS_SQL, [str(csv_path)])

            # Build devices table: one row per cluster, consolidated
            conn.execute(DEVICES_SQL)

            # Metrics & Events (append-only)
            conn.execute(METRICS_SQL)
            conn.execute(EVENTS_SQL)
            conn.execute(ATTACHMENTS_SQL)

            # Load Zabbix items into metrics table
            _load_zbx_metrics(conn)

            # Indexes
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


def run_attach() -> int:
    """Run attachment resolution for all ATTACHMENT feeds.

    Called after run_load() produces a fresh mesh. Reads raw stores, resolves
    links, writes into the attachments table.
    """
    if not PATHS.mesh_path.exists():
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
            sqlite_db_path = str(PATHS.data_dir / f"{feed.system}.sqlite")

            try:
                rows = resolve_feed_attachments(feed, feed_key, str(PATHS.mesh_path), sqlite_db_path)
            except Exception as e:
                log.error({"event": "attach_failed", "feed": feed_key, "error": str(e)})
                continue

            if not rows:
                log.info({"event": "attach_empty", "feed": feed_key})
                continue

            conn = duckdb.connect(str(PATHS.mesh_path), read_only=False)
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
    if not PATHS.mesh_path.exists():
        log.warning({"event": "incremental_load_skip", "reason": "mesh_not_found"})
        return 0

    record_start("incremental_load")

    # Load profile fields for CSV column iteration
    manifest = _get_manifest()
    profile = manifest.profiles["device"]
    splink_fields = list(profile.fields)

    with StepTimer(log, "incremental_load"):
        conn = duckdb.connect(str(PATHS.mesh_path), read_only=False)
        try:
            import contextlib

            with contextlib.suppress(Exception):
                conn.execute("SELECT DISTINCT cluster_id FROM source_records").fetchall()

            for source_key in sources:
                csv_path = PATHS.csv_dir / f"{source_key}.csv"
                if not csv_path.exists():
                    log.warning({"event": "incremental_skip", "source": source_key, "reason": "csv_not_found"})
                    continue

                # Read CSV via Python csv.DictReader (avoids DuckDB read_csv_auto
                # delimiter-detection issues with tiny files).
                import uuid

                existing = conn.execute(
                    "SELECT source_id FROM source_records WHERE source = ?", [source_key]
                ).fetchall()
                existing_ids = {r[0] for r in existing}

                new_count = 0
                updated_count = 0

                with open(csv_path, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row_dict in reader:
                        sid = row_dict.get("source_id", "")

                        if sid in existing_ids:
                            set_clauses = []
                            values = []
                            for col in splink_fields:
                                if col in ("source", "source_id"):
                                    continue
                                set_clauses.append(f"{col} = ?")
                                values.append(row_dict.get(col, ""))
                            values.extend([source_key, sid])
                            conn.execute(
                                f"UPDATE source_records SET {', '.join(set_clauses)} WHERE source = ? AND source_id = ?",
                                values,
                            )
                            updated_count += 1
                        else:
                            cluster_id = f"new-{source_key}-{uuid.uuid4().hex[:8]}"
                            all_cols = ["cluster_id"] + list(splink_fields)
                            cols = ", ".join(all_cols)
                            placeholders = ", ".join(["?"] * len(all_cols))
                            values = [cluster_id] + [row_dict.get(c, "") for c in splink_fields]
                            conn.execute(f"INSERT INTO source_records ({cols}) VALUES ({placeholders})", values)
                            new_count += 1

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

            conn.execute(DEVICES_SQL)
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
    lock_path = PATHS.data_dir / "pipeline.lock"
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

        if not PATHS.mesh_path.exists():
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

    # ── Concurrency guard ──────────────────────────────────────────────
    lock_path = PATHS.data_dir / "pipeline.lock"
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

        if is_brutalist_enabled():
            render_stage("LOAD")
        log.info({"event": "pipeline_stage", "stage": "load"})
        device_count = run_load()

        if is_brutalist_enabled():
            render_stage("ATTACH")
        log.info({"event": "pipeline_stage", "stage": "attach"})
        attach_count = run_attach()
        log.info({"event": "attach_complete", "attachments": attach_count})

        log.info({"event": "pipeline_complete", "devices": device_count})
    finally:
        if have_lock:
            lock_file.close()
            import contextlib

            with contextlib.suppress(OSError):
                lock_path.unlink()
        else:
            lock_file.close()
