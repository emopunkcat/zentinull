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
    """Load clusters.csv into DuckDB mesh using temp-and-swap (delegated)."""
    from .cli.pipeline import run_load

    run_load()


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


def _main() -> None:
    """CLI entry point for ``python -m zentinull.pipeline``."""
    skip_ingest_flag = "--skip-ingest" in sys.argv
    dry_run_flag = "--dry-run" in sys.argv
    try:
        run(skip_ingest=skip_ingest_flag, dry_run=dry_run_flag)
    except Exception:
        log.exception("pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    _main()
