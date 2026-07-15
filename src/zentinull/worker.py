"""Scheduled worker — runs per-source ingest on independent schedules.

Usage:
    python -m zentinull.worker              # default schedule
    python -m zentinull.worker --dry-run    # show what would run

Schedule is configured via env vars:
    ZENTINULL_SCHED_ZBX=600        # Zabbix every 10 min (default: 600)
    ZENTINULL_SCHED_FG=1800        # FortiGate every 30 min (default: 1800)
    ZENTINULL_SCHED_ME=7200        # ManageEngine every 2h (default: 7200)
    ZENTINULL_SCHED_SDP=7200       # ServiceDesk Plus every 2h (default: 7200)
    ZENTINULL_SCHED_AD=21600       # Active Directory every 6h (default: 21600)
    ZENTINULL_SCHED_SP=43200       # SharePoint every 12h (default: 43200)
    ZENTINULL_SCHED_SPLINK=86400   # Splink daily (default: 86400)

High-frequency sources (Zabbix, FortiGate) run via incremental sync.
Low-frequency sources (AD, SharePoint) run via incremental sync.
Splink runs full pipeline on its own schedule for entity resolution.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from typing import Any

from .config import _load_dotenv
from .logging_config import get_logger, setup
from .manifest import load_manifest

_load_dotenv()
setup(level="INFO")

log = get_logger("worker")

# Default intervals in seconds — derived from manifest System.schedule
_manifest = load_manifest()
DEFAULTS: dict[str, int] = {key: system.schedule or 3600 for key, system in _manifest.systems.items()}

# Splink schedule (separate from source schedules)
SPLINK_DEFAULT = 86400  # 24h

# Source groups — each system is its own group
_SOURCE_GROUPS: dict[str, list[str]] = {key: [key] for key in _manifest.systems}


def _get_interval(source: str) -> int:
    env_key = f"ZENTINULL_SCHED_{source.upper()}"
    return int(os.environ.get(env_key, str(DEFAULTS.get(source, 3600))))


def _get_splink_interval() -> int:
    return int(os.environ.get("ZENTINULL_SCHED_SPLINK", str(SPLINK_DEFAULT)))


class WorkerState:
    def __init__(self) -> None:
        self.last_run: dict[str, float] = {s: 0.0 for s in DEFAULTS}
        self.last_splink: float = 0.0
        self.running: bool = False
        self.should_stop: bool = False

    def should_run(self, source: str) -> bool:
        interval = _get_interval(source)
        elapsed = time.time() - self.last_run[source]
        return elapsed >= interval

    def should_run_splink(self) -> bool:
        interval = _get_splink_interval()
        elapsed = time.time() - self.last_splink
        return elapsed >= interval


async def _run_sync(sources: list[str]) -> int:
    from .cli.pipeline import run_incremental_sync

    def _do_sync() -> int:
        return run_incremental_sync(sources)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_sync)


async def _run_full_pipeline() -> None:
    from .cli.pipeline import run_export, run_load, run_splink

    def _do_pipeline() -> None:
        run_export()
        run_splink()
        run_load()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_pipeline)


async def loop() -> None:
    state = WorkerState()

    def _handle_stop(signum: int, _frame: Any) -> None:
        log.info({"event": "shutdown_signal", "signal": signum})
        state.should_stop = True

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    log.info(
        {
            "event": "worker_start",
            "schedule": {s: _get_interval(s) for s in DEFAULTS},
            "splink_interval": _get_splink_interval(),
        }
    )

    while not state.should_stop:
        for source, sources in _SOURCE_GROUPS.items():
            if state.should_run(source) and not state.running:
                state.running = True
                try:
                    log.info({"event": "sync_start", "source": source, "interval": _get_interval(source)})
                    device_count = await _run_sync(sources)
                    state.last_run[source] = time.time()
                    log.info({"event": "sync_done", "source": source, "devices": device_count})
                except Exception as e:
                    log.error({"event": "sync_fail", "source": source, "error": str(e)})
                finally:
                    state.running = False

        if state.should_run_splink() and not state.running:
            state.running = True
            try:
                log.info({"event": "splink_start", "interval": _get_splink_interval()})
                await _run_full_pipeline()
                state.last_splink = time.time()
                log.info({"event": "splink_done"})
            except Exception as e:
                log.error({"event": "splink_fail", "error": str(e)})
            finally:
                state.running = False

        await asyncio.sleep(10)


async def dry_run() -> None:
    log.info({"event": "dry_run"})
    for source in DEFAULTS:
        interval = _get_interval(source)
        hours = interval / 3600
        log.info({"event": "schedule", "source": source, "interval_s": interval, "interval_h": round(hours, 1)})
    splink_interval = _get_splink_interval()
    log.info(
        {
            "event": "schedule",
            "source": "splink",
            "interval_s": splink_interval,
            "interval_h": round(splink_interval / 3600, 1),
        }
    )


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        asyncio.run(dry_run())
    else:
        asyncio.run(loop())
