"""Structured logging — shared across all Zentinull modules.

Usage:
    from logging_config import get_logger
    log = get_logger("ingest.sp")
    log.info({"event": "fetched", "table": "sp_devices", "rows": 581, "elapsed_ms": 1234})
    # → 19:21:23.456 [zig.ingest.sp] INFO  table=sp_devices rows=581 elapsed_ms=1234
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Key=value structured output — human-readable, grep-friendly.

    If the log message is a dict, it's rendered as key=value pairs.
    Otherwise treated as a normal string.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]
        name = record.name
        level = record.levelname

        # If msg is a dict, render as key=value
        if isinstance(record.msg, dict):
            parts = [f"{k}={_fmt_val(v)}" for k, v in record.msg.items()]
            msg = " ".join(parts)
        else:
            msg = record.msg % record.args if record.args else str(record.msg)

        base = f"{ts} [{name}] {level:5s} {msg}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{self.formatException(record.exc_info)}"

        return base


def _fmt_val(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, int | float):
        return str(v)
    if isinstance(v, bool):
        return str(v).lower()
    s = str(v)
    if " " in s or "=" in s or not s:
        return json.dumps(s)
    return s


class JsonFormatter(logging.Formatter):
    """JSON-line output — for log aggregation systems."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).isoformat()
        if isinstance(record.msg, dict):
            obj: dict[str, Any] = {
                "ts": ts,
                "logger": record.name,
                "level": record.levelname,
                **record.msg,
            }
        else:
            obj = {
                "ts": ts,
                "logger": record.name,
                "level": record.levelname,
                "msg": record.msg % record.args if record.args else str(record.msg),
            }
        if record.exc_info and record.exc_info[1]:
            obj["error"] = str(record.exc_info[1])
        return json.dumps(obj, default=str)


# ── Logger factory ───────────────────────────────────────────────────────────

_loggers: dict[str, logging.Logger] = {}
_initialized = False


def setup(*, level: str = "INFO", json_output: bool = False, log_file: Path | str | None = None) -> None:
    """Initialize logging globally. Call once at startup."""
    global _initialized

    root = logging.getLogger("zig")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    fmt = JsonFormatter() if json_output else StructuredFormatter()

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setLevel(root.level)
    stdout.setFormatter(fmt)
    root.addHandler(stdout)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setLevel(root.level)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Get a structured logger for a component.

    Names follow dotted hierarchy:
        ingest.sp, ingest.me, ingest.fg, ingest.zbx, ingest.ad, ingest.sdp
        pipeline, splink
        api.server, api.db, api.router
    """
    if not _initialized:
        setup()

    full_name = f"zig.{name}"
    if full_name not in _loggers:
        _loggers[full_name] = logging.getLogger(full_name)
    return _loggers[full_name]


# ── Helpers ──────────────────────────────────────────────────────────────────


class StepTimer:
    """Context manager for timing a block with structured logging.

    with StepTimer(log, "splink.predict"):
        linker.predict()
    # → 2026-07-10T19:21:23.456 [zig.splink] INFO  step=splink.predict elapsed_ms=2140
    """

    def __init__(self, log: logging.Logger, step: str) -> None:
        self._log = log
        self._step = step
        self._t0: float = 0

    def __enter__(self) -> StepTimer:
        self._t0 = time.perf_counter()
        self._log.info({"step": self._step, "status": "started"})
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        status = "error" if args[0] else "done"
        self._log.info(
            {
                "step": self._step,
                "status": status,
                "elapsed_ms": elapsed_ms,
            }
        )
        return False  # type: ignore[return-value]
