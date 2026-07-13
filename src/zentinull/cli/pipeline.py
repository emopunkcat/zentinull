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
import fcntl
import json
import os
import sqlite3
import sys
from typing import Any

import duckdb

from ..api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL, SOURCE_RECORDS_SQL
from ..config import CSV_DIR, DATA_DIR, MESH_DB, ROOT, SPLINK_OUTPUT_DIR
from ..contracts import SPLINK_FIELDS
from ..export_for_splink import _COMPUTED_FIELDS, _SYSTEM_COLS, DEVICE_TABLES, FIELD_MAP
from ..export_for_splink import export as _run_export_fn
from ..logging_config import StepTimer, get_logger
from .render import is_brutalist_enabled, render_banner, render_stage
from .status import record_done, record_fail, record_start
from .streaming import run_streaming

PYTHON = sys.executable or "python3"

log = get_logger("cli.pipeline")

SOURCE_MAP: dict[str, tuple[str, str]] = {
    "sp": ("sharepoint", "SharePoint"),
    "me": ("manageengine", "ManageEngine"),
    "fg": ("fortigate", "FortiGate"),
    "zbx": ("zabbix", "Zabbix"),
    "ad": ("ad", "Active Directory"),
    "sdp": ("servicedeskplus", "ServiceDesk Plus"),
}

SOURCE_TO_TABLES: dict[str, list[str]] = {
    "sp": ["sp"],
    "me": ["me_ec", "me_mdm"],
    "fg": ["fg"],
    "zbx": ["zbx"],
    "ad": ["ad"],
    "sdp": ["sdp"],
}

# ── Ingest ────────────────────────────────────────────────────────────────────


def run_ingest(sources: list[str] | None = None, skip_sources: list[str] | None = None) -> dict[str, int]:
    """Run ingestors in-process via direct import.

    If sources is None, all 6 sources are run.
    Each source runs in its own module's ingest() function.

    Returns dict of source_name → row_count.
    """
    from ..ingestors import ad, fortigate, manageengine, servicedeskplus, sharepoint, zabbix

    module_by_key: dict[str, Any] = {
        "sp": sharepoint,
        "me": manageengine,
        "fg": fortigate,
        "zbx": zabbix,
        "ad": ad,
        "sdp": servicedeskplus,
    }

    if sources is None:
        sources = list(SOURCE_MAP.keys())

    skip_set = set(skip_sources or [])
    source_keys = [k for k in sources if k not in skip_set and k in SOURCE_MAP]

    record_start("ingest")
    results: dict[str, int] = {}

    with StepTimer(log, "ingest"):
        for key in source_keys:
            _module_key, display_name = SOURCE_MAP[key]
            mod = module_by_key[key]
            log.info({"event": "ingesting", "source": display_name})
            try:
                n: int = mod.ingest()
                results[display_name] = n
                log.info({"event": "ingested", "source": display_name, "rows": n})
            except Exception as e:
                log.error({"event": "ingest_failed", "source": display_name, "error": str(e)})
                results[display_name] = -1

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

    csv_path = CSV_DIR / "devices.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Export did not produce {csv_path}")

    with open(csv_path, encoding="utf-8") as f:
        total = sum(1 for _ in f) - 1  # minus header row

    record_done("export", total=total)
    return max(total, 0)


def export_source(source_key: str) -> int:
    """Export a single source to its own CSV file.

    Returns record count for that source.
    """
    if source_key not in SOURCE_TO_TABLES:
        raise ValueError(f"Unknown source key: {source_key}")

    splink_lower: dict[str, str] = {sf.lower(): sf for sf in SPLINK_FIELDS if sf not in _COMPUTED_FIELDS}
    all_rows: list[dict[str, str]] = []

    for table_key in SOURCE_TO_TABLES[source_key]:
        db_file = table_key.split("_")[0]
        db_path = DATA_DIR / f"{db_file}.sqlite"
        if not db_path.exists():
            log.warning({"event": "skip", "source": table_key, "reason": "db_not_found"})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        table_name = DEVICE_TABLES[table_key]
        if table_name not in tables:
            log.warning({"event": "skip", "source": table_key, "reason": "table_not_found"})
            conn.close()
            continue

        col_rows = conn.execute(f"SELECT name FROM pragma_table_info('{table_name}')").fetchall()
        typed_cols = [r[0] for r in col_rows]

        explicit = FIELD_MAP.get(table_key, {})
        mapper = dict(explicit)
        mapped_source_cols = set(explicit.keys())
        for col in typed_cols:
            if col in _SYSTEM_COLS or col in mapped_source_cols:
                continue
            match = splink_lower.get(col.lower())
            if match and match not in mapper.values():
                mapper[col] = match
                mapped_source_cols.add(col)

        extra_source_cols = [c for c in typed_cols if c not in _SYSTEM_COLS and c not in mapped_source_cols]

        try:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": table_key, "reason": "error", "error": str(e)})
            conn.close()
            continue

        for row in rows:
            row_dict = dict(row)
            rec = {f: "" for f in SPLINK_FIELDS}
            rec["source"] = table_key
            for col_name, splink_field in mapper.items():
                val = row_dict.get(col_name, "")
                if val and str(val).strip():
                    val_str = str(val).strip()
                    existing = rec.get(splink_field, "")
                    if existing:
                        if val_str not in existing.split(","):
                            rec[splink_field] = f"{existing},{val_str}"
                    else:
                        rec[splink_field] = val_str

            name_raw = rec.get("name", "")
            rec["name_clean"] = name_raw.lower().split(".")[0] if name_raw else ""
            mac_raw = rec.get("mac_address", "")
            mac_clean = mac_raw.lower().replace(":", "").replace("-", "").replace(".", "").split(",")[0]
            rec["mac_clean"] = mac_clean if len(mac_clean) == 12 else ""
            if not rec["source_id"]:
                rec["source_id"] = str(row_dict.get("id", ""))

            extra: dict[str, str] = {}
            for col in extra_source_cols:
                val = row_dict.get(col, "")
                if val and str(val).strip():
                    extra[col] = str(val).strip()
            raw = row_dict.get("raw_json", "")
            if raw and isinstance(raw, str):
                try:
                    raw_dict = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    raw_dict = {}
                if isinstance(raw_dict, dict):
                    for k, v in raw_dict.items():
                        if (
                            k not in typed_cols
                            and k not in extra
                            and k not in _SYSTEM_COLS
                            and k not in mapped_source_cols
                            and v
                            and str(v).strip()
                        ):
                            extra[k] = str(v).strip()
            rec["extra_attributes"] = json.dumps(extra) if extra else ""
            all_rows.append(rec)

        conn.close()

    out_path = CSV_DIR / f"{source_key}.csv"
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SPLINK_FIELDS)
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


def run_load() -> int:
    """Load clusters.csv into DuckDB mesh using temp-and-swap.

    Builds mesh in data/mesh.duckdb.tmp, then atomically renames to mesh.duckdb.
    Returns total device count.
    """
    csv_path = SPLINK_OUTPUT_DIR / "clusters.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found — run splink first")

    mesh_path = MESH_DB
    tmp_path = DATA_DIR / "mesh.duckdb.tmp"

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


def run_incremental_load(sources: list[str]) -> int:
    """Upsert per-source CSVs into DuckDB and rebuild devices table.

    Unlike run_load() which rebuilds everything from Splink output,
    this preserves existing cluster_ids and only adds/updates source records.
    New records get temporary cluster_ids until next Splink run merges them.

    Returns device count after rebuild.
    """
    if not MESH_DB.exists():
        log.warning({"event": "incremental_load_skip", "reason": "mesh_not_found"})
        return 0

    record_start("incremental_load")

    with StepTimer(log, "incremental_load"):
        conn = duckdb.connect(str(MESH_DB), read_only=False)
        try:
            import contextlib

            with contextlib.suppress(Exception):
                conn.execute("SELECT DISTINCT cluster_id FROM source_records").fetchall()

            for source_key in sources:
                csv_path = CSV_DIR / f"{source_key}.csv"
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
                            for col in SPLINK_FIELDS:
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
                            all_cols = ["cluster_id"] + list(SPLINK_FIELDS)
                            cols = ", ".join(all_cols)
                            placeholders = ", ".join(["?"] * len(all_cols))
                            values = [cluster_id] + [row_dict.get(c, "") for c in SPLINK_FIELDS]
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
    lock_path = DATA_DIR / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")  # noqa: SIM115
    have_lock = False
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        run_ingest(sources=sources)

        for source_key in sources:
            if source_key in SOURCE_TO_TABLES:
                if is_brutalist_enabled():
                    render_stage(f"EXPORT {source_key.upper()}")
                log.info({"event": "sync_stage", "stage": "export", "source": source_key})
                export_source(source_key)

        if not MESH_DB.exists():
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
    """Run the full pipeline: ingest → export → splink → load.

    Each stage is streamed and status-tracked independently.
    Uses a PID-based lock file to prevent concurrent pipeline runs.
    """

    # ── Concurrency guard ──────────────────────────────────────────────
    lock_path = DATA_DIR / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")  # noqa: SIM115
    have_lock = False
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
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

        log.info({"event": "pipeline_complete", "devices": device_count})
    finally:
        if have_lock:
            lock_file.close()
            import contextlib

            with contextlib.suppress(OSError):
                lock_path.unlink()
        else:
            lock_file.close()
