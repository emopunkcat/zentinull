"""
Pipeline orchestrator: ingest → export → entity resolution → DuckDB mesh.

Usage:
    python pipeline.py              Full run: ingest + export + splink + load
    python pipeline.py --skip-ingest  Export + splink + load (data already fresh)
    python pipeline.py --dry-run      Print what would run
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb

from .logging_config import StepTimer, get_logger, setup

ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = sys.executable or "python3"

setup(level="INFO")
log = get_logger("pipeline")


def _run_step(step: str, args: list[str], timeout: int = 120) -> None:
    """Run a subprocess step."""
    with StepTimer(log, step):
        result = subprocess.run(
            [PYTHON, *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            log.error(
                {"step": step, "status": "failed", "exit_code": result.returncode, "stderr": result.stderr[-300:]}
            )
            raise RuntimeError(f"{step} failed with code {result.returncode}")


def _run_splink() -> None:
    """Run entity resolution."""
    script = ROOT / "scripts" / "run_splink.py"
    with StepTimer(log, "splink"):
        result = subprocess.run(
            [PYTHON, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            log.error(
                {"step": "splink", "status": "failed", "exit_code": result.returncode, "stderr": result.stderr[-500:]}
            )
            raise RuntimeError("Splink failed")


def _load_to_duckdb() -> None:
    """Load clusters.csv into DuckDB mesh database."""
    mesh_path = ROOT / "data" / "mesh.duckdb"
    clusters_csv = ROOT / "export" / "splink_output" / "clusters.csv"

    if not clusters_csv.exists():
        raise FileNotFoundError(f"{clusters_csv} not found — run splink first")

    with StepTimer(log, "duckdb.load"):
        conn = duckdb.connect(str(mesh_path))

        # Load clusters CSV into source_records table
        conn.execute(
            """
            CREATE OR REPLACE TABLE source_records AS
            SELECT * FROM read_csv_auto(?)
        """,
            [str(clusters_csv)],
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

        device_count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]  # type: ignore[index]
        record_count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]  # type: ignore[index]
        conn.close()

        log.info({"event": "mesh_loaded", "devices": device_count, "records": record_count})


def run(*, skip_ingest: bool = False, dry_run: bool = False) -> None:
    """Run the full pipeline."""
    if dry_run:
        log.info({"event": "dry_run"})
        if not skip_ingest:
            log.info({"event": "dry_run_step", "step": 1, "description": "run_ingest.py (6 sources → SQLite)"})
        log.info({"event": "dry_run_step", "step": 2, "description": "export_for_splink.py (SQLite → CSV)"})
        log.info({"event": "dry_run_step", "step": 3, "description": "run_splink.py (entity resolution)"})
        log.info({"event": "dry_run_step", "step": 4, "description": "Load clusters.csv → DuckDB mesh"})
        return

    steps: list[tuple[str, list[str]]] = []
    if not skip_ingest:
        steps.append(("ingest", ["scripts/run_ingest.py"]))
    steps.append(("export", ["-m", "zentinull.export_for_splink"]))

    for step_name, args in steps:
        _run_step(step_name, args, timeout=300 if step_name == "ingest" else 60)
    _run_splink()
    _load_to_duckdb()

    log.info({"event": "pipeline_complete", "steps": len(steps) + 2})


if __name__ == "__main__":
    skip_ingest = "--skip-ingest" in sys.argv
    dry_run = "--dry-run" in sys.argv
    try:
        run(skip_ingest=skip_ingest, dry_run=dry_run)
    except Exception:
        log.exception("pipeline failed")
        sys.exit(1)
