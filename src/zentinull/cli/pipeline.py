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

from ..api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL, SOURCE_RECORDS_SQL
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


_REMOTE_PORT = 9999


def run_remote_ingest(
    host: str,
    port: int = _REMOTE_PORT,
    sources: list[str] | None = None,
    skip_sources: list[str] | None = None,
) -> dict[str, int]:
    """Trigger ingest on a remote proxy and download the SQLite databases.

    Contacts the remote daemon at host:port, triggers ingest for the requested
    sources (or all 6), downloads each resulting .sqlite file into the local
    data/ directory, then returns the {source_name: row_count} results.
    """
    import httpx

    base = f"http://{host}:{port}"
    results: dict[str, int] = {}
    record_start("ingest")
    source_keys = list(SOURCE_MAP.keys())

    if sources is not None:
        source_keys = [k for k in source_keys if k in sources]
    skip_set = set(skip_sources or [])
    source_keys = [k for k in source_keys if k not in skip_set]

    with StepTimer(log, "ingest"):
        for key in source_keys:
            _, display_name = SOURCE_MAP[key]
            log.info({"event": "remote_ingesting", "source": display_name, "host": host})
            try:
                resp = httpx.post(f"{base}/ingest/{key}", timeout=600)
                resp.raise_for_status()
                body = resp.json()
                rows = body.get("rows", -1)
                results[display_name] = rows
                log.info({"event": "remote_ingested", "source": display_name, "rows": rows})

                # Download the SQLite file
                db_resp = httpx.get(f"{base}/data/{key}.sqlite", timeout=120)
                db_resp.raise_for_status()
                db_path = ROOT / "data" / f"{key}.sqlite"
                db_path.write_bytes(db_resp.content)
                log.info({"event": "db_downloaded", "source": key, "path": str(db_path), "bytes": len(db_resp.content)})
            except httpx.HTTPStatusError as e:
                log.error({"event": "remote_ingest_failed", "source": display_name, "error": str(e)})
                results[display_name] = -1
            except httpx.RequestError as e:
                log.error({"event": "remote_unreachable", "source": display_name, "error": str(e)})
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
    remote_host: str | None = None,
    remote_port: int = _REMOTE_PORT,
) -> None:
    """Run the full pipeline: ingest → export → splink → load.

    Each stage is streamed and status-tracked independently.
    """
    if not skip_ingest:
        log.info({"event": "pipeline_stage", "stage": "ingest"})
        if remote_host:
            run_remote_ingest(host=remote_host, port=remote_port, sources=sources, skip_sources=skip_sources)
        else:
            run_ingest(sources=sources, skip_sources=skip_sources)

    log.info({"event": "pipeline_stage", "stage": "export"})
    total = run_export()
    log.info({"event": "export_done", "records": total})

    log.info({"event": "pipeline_stage", "stage": "splink"})
    run_splink()

    log.info({"event": "pipeline_stage", "stage": "load"})
    device_count = run_load()

    log.info({"event": "pipeline_complete", "devices": device_count})
