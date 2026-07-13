"""
Pipeline orchestrator — streaming subprocess model with per-source ingest control,
Splink option support, and temp-and-swap DuckDB loading.

Replaces the capture_output=True subprocess model in src/zentinull/pipeline.py.
"""

from __future__ import annotations

import fcntl
import os
import sys
from typing import Any

import duckdb

from ..api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL, SOURCE_RECORDS_SQL
from ..config import CSV_DIR, DATA_DIR, MESH_DB, ROOT, SPLINK_OUTPUT_DIR
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
