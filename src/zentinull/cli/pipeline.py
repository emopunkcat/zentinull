"""
Pipeline orchestrator — streaming subprocess model with per-source ingest control,
Splink option support, and temp-and-swap DuckDB loading.

Replaces the capture_output=True subprocess model in src/zentinull/pipeline.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import duckdb

from ..export_for_splink import export as _run_export_fn
from ..logging_config import StepTimer, get_logger
from .status import record_done, record_fail, record_start
from .streaming import run_streaming

ROOT = Path(__file__).resolve().parent.parent.parent.parent
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

    csv_path = ROOT / "export" / "csv" / "devices.csv"
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
    csv_path = ROOT / "export" / "splink_output" / "clusters.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found — run splink first")

    mesh_path = ROOT / "data" / "mesh.duckdb"
    tmp_path = ROOT / "data" / "mesh.duckdb.tmp"

    record_start("load")

    with StepTimer(log, "duckdb.load"):
        # Clean up stale temp file
        if tmp_path.exists():
            tmp_path.unlink()

        conn = duckdb.connect(str(tmp_path))

        try:
            # Load clusters CSV into source_records table
            conn.execute(
                """
                CREATE OR REPLACE TABLE source_records AS
                SELECT * FROM read_csv_auto(?)
                """,
                [str(csv_path)],
            )

            # Build devices table: one row per cluster, consolidated
            conn.execute("""
                CREATE OR REPLACE TABLE devices AS
                SELECT
                    cluster_id,
                    COALESCE(
                        NULLIF(MIN(CASE WHEN name_clean != '' THEN name_clean END), ''),
                        NULLIF(MIN(CASE WHEN name != '' THEN name END), ''),
                        '(unnamed)'
                    ) AS device_name,
                    COUNT(DISTINCT source) AS source_count,
                    LIST(DISTINCT source ORDER BY source) AS sources,
                    COALESCE(NULLIF(MIN(CASE WHEN serial_number != '' THEN serial_number END), ''), '') AS serial_number,
                    COALESCE(NULLIF(MIN(CASE WHEN mac_clean != '' THEN mac_clean END), ''), '') AS mac_address,
                    COALESCE(NULLIF(MIN(CASE WHEN manufacturer != '' THEN manufacturer END), ''), '') AS manufacturer,
                    COALESCE(NULLIF(MIN(CASE WHEN model != '' THEN model END), ''), '') AS model,
                    COALESCE(NULLIF(MIN(CASE WHEN os != '' THEN os END), ''), '') AS os,
                    COALESCE(NULLIF(MIN(CASE WHEN assigned_user != '' THEN assigned_user END), ''), '') AS assigned_user,
                    COALESCE(NULLIF(MIN(CASE WHEN ip_address != '' THEN ip_address END), ''), '') AS ip_address,
                    COALESCE(NULLIF(MIN(CASE WHEN imei != '' THEN imei END), ''), '') AS imei,
                    COUNT(*) AS record_count
                FROM source_records
                GROUP BY cluster_id
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_name ON devices(device_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_cluster ON source_records(cluster_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_mac ON source_records(mac_clean)")

            # Metrics & Events (append-only)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    cluster_id TEXT NOT NULL, source TEXT NOT NULL,
                    metric_name TEXT NOT NULL, value DOUBLE, text_value TEXT,
                    tags TEXT[], recorded_at TIMESTAMP NOT NULL,
                    ingested_at TIMESTAMP DEFAULT now()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    cluster_id TEXT NOT NULL, source TEXT NOT NULL,
                    event_type TEXT NOT NULL, detail TEXT,
                    severity TEXT DEFAULT 'info', recorded_at TIMESTAMP NOT NULL,
                    ingested_at TIMESTAMP DEFAULT now()
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_cluster_time ON metrics(cluster_id, recorded_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name, recorded_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_cluster_time ON events(cluster_id, recorded_at)")

            conn.execute("CHECKPOINT")

            device_count_row = conn.execute("SELECT COUNT(*) FROM devices").fetchone()
            record_count_row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            if device_count_row is None or record_count_row is None:
                raise RuntimeError("Failed to read row counts from temp mesh")
            device_count: int = int(device_count_row[0])
            record_count_val: int = int(record_count_row[0])
            conn.close()

            # Swap: remove old mesh, rename temp → mesh
            if mesh_path.exists():
                mesh_path.unlink()
            tmp_path.rename(mesh_path)

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
    """
    if not skip_ingest:
        log.info({"event": "pipeline_stage", "stage": "ingest"})
        run_ingest(sources=sources, skip_sources=skip_sources)

    log.info({"event": "pipeline_stage", "stage": "export"})
    total = run_export()
    log.info({"event": "export_done", "records": total})

    log.info({"event": "pipeline_stage", "stage": "splink"})
    run_splink()

    log.info({"event": "pipeline_stage", "stage": "load"})
    device_count = run_load()

    log.info({"event": "pipeline_complete", "devices": device_count})
