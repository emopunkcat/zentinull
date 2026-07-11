"""Pipeline status tracker — JSON-based, thread-safe.

Records last run time, row counts, success/failure per stage,
and data freshness per source to data/status.json.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATUS_FILE = ROOT / "data" / "status.json"

_lock = threading.Lock()


# ── Internal helpers ──────────────────────────────────────────────────────────


def _read() -> dict[str, Any]:
    """Read the status file, returning default empty structure if it doesn't exist."""
    if STATUS_FILE.exists():
        try:
            return cast(dict[str, Any], json.loads(STATUS_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return {"stages": {}, "freshness": {}}


def _write(data: dict[str, Any]) -> None:
    """Atomically write the status file."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(STATUS_FILE)


def _ms_since(iso_str: str) -> int:
    """Return milliseconds between an ISO timestamp and now, or 0 on parse failure."""
    try:
        start = datetime.fromisoformat(iso_str)
        return int((datetime.now(UTC) - start).total_seconds() * 1000)
    except (ValueError, TypeError):
        return 0


# ── Public API ────────────────────────────────────────────────────────────────


def record_start(stage: str) -> None:
    """Record that a pipeline stage started.

    Writes the current UTC timestamp as ``last_run`` and sets status to ``running``.
    """
    now = datetime.now(UTC).isoformat()
    with _lock:
        data = _read()
        data.setdefault("stages", {})[stage] = {"last_run": now, "status": "running"}
        _write(data)


def record_done(stage: str, **stats: int | str) -> None:
    """Record successful stage completion with optional stats.

    Stats become top-level keys in the stage object (e.g. ``total_records``, ``device_count``).
    Duration is computed from the last ``record_start`` timestamp for this stage.
    """
    now = datetime.now(UTC).isoformat()
    with _lock:
        data = _read()
        stage_data = data.setdefault("stages", {}).setdefault(stage, {})
        last_run = stage_data.get("last_run", "")
        stage_data.update(
            {
                "last_run": now,
                "status": "ok",
                "duration_ms": _ms_since(last_run),
            }
        )
        if stats:
            stage_data.update(stats)
        if stage == "load":
            data["last_full_pipeline"] = now
        _write(data)


def record_fail(stage: str, error: str) -> None:
    """Record stage failure with an error message."""
    now = datetime.now(UTC).isoformat()
    with _lock:
        data = _read()
        stage_data = data.setdefault("stages", {}).setdefault(stage, {})
        last_run = stage_data.get("last_run", "")
        stage_data.update(
            {
                "last_run": now,
                "status": "fail",
                "error": error,
                "duration_ms": _ms_since(last_run),
            }
        )
        _write(data)


def record_freshness(source: str, newest_record: str, row_count: int) -> None:
    """Record data freshness for a single source.

    Args:
        source: Source identifier (e.g. ``"sp"``, ``"me"``).
        newest_record: ISO timestamp of the newest record from this source.
        row_count: Total rows ingested for this source.
    """
    with _lock:
        data = _read()
        data.setdefault("freshness", {})[source] = {
            "newest_record": newest_record,
            "row_count": row_count,
        }
        _write(data)


def get_status() -> dict[str, Any]:
    """Return the full status dict from disk."""
    return _read()


def print_status() -> None:
    """Pretty-print current pipeline status as a table to stdout."""
    data = get_status()
    stages = data.get("stages", {})
    freshness = data.get("freshness", {})

    # ── Pipeline stages table ──
    if stages:
        print(f"{'Stage':<9} {'Last Run':<22} {'Status':<9} {'Duration':<11} Details")
        print("─" * 92)

        for stage_name, sd in stages.items():
            # Timestamp
            last_run_raw = sd.get("last_run", "N/A")
            last_run = _fmt_ts(last_run_raw)

            # Status badge
            status = sd.get("status", "unknown")
            status_map = {"ok": "OK", "fail": "FAIL", "running": "RUNNING"}
            status_display = status_map.get(status, status.upper())

            # Duration
            duration_ms = sd.get("duration_ms", 0)
            duration_display = _fmt_duration(duration_ms)

            # Details — skip reserved keys, format the rest
            reserved = {"last_run", "status", "duration_ms", "error"}
            detail_parts: list[str] = []
            if status == "fail" and "error" in sd:
                detail_parts.append(f"error={sd['error']}")
            for key, value in sd.items():
                if key in reserved:
                    continue
                detail_parts.append(_fmt_stat(key, value))
            details = " | ".join(detail_parts) if detail_parts else ""

            print(f"{stage_name:<9} {last_run:<22} {status_display:<9} {duration_display:<11} {details}")

        print("─" * 92)

    # ── Data freshness ──
    if freshness:
        print("\nData Freshness:")
        for source in sorted(freshness):
            fd = freshness[source]
            row_count = fd.get("row_count", "?")
            newest = _fmt_ts(fd.get("newest_record", "N/A"))
            print(f"  {source}: {row_count} records, newest from {newest}")

    if not stages and not freshness:
        print("No status data available.")


# ── Display helpers ───────────────────────────────────────────────────────────


def _fmt_ts(raw: str) -> str:
    """Format an ISO timestamp for display, or return the raw string on failure."""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(raw)


def _fmt_duration(ms: int) -> str:
    """Human-readable duration from milliseconds."""
    if ms <= 0:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _fmt_stat(key: str, value: object) -> str:
    """Format a single stat value for display."""
    if isinstance(value, dict):
        parts = [f"{k}:{v}" for k, v in value.items()]
        return " ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return f"{key}:{value}"
