#!/usr/bin/env python3
"""
API performance benchmark runner.

Creates a seeded test DuckDB, wires it to the FastAPI app, uses TestClient
to exercise all endpoints multiple times, and reports per-endpoint timing.

Usage:
    python scripts/bench_api.py          # run once, print & save
    python scripts/bench_api.py --json   # machine-readable JSON only

Stores historical results in .benchmarks/api_results.json for trend tracking.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, stdev
from typing import Any

import duckdb
from fastapi.testclient import TestClient

# Lazy imports: fastapi app + mesh schema loaded inside functions so that
# importing this module for unit tests does not pull in the whole stack.

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BENCH_DIR = ROOT / ".benchmarks"
BENCH_FILE = BENCH_DIR / "api_results.json"

REGRESSION_THRESHOLD_PCT = 20  # default: fail if avg response time degrades >20%


DEFAULT_ITERATIONS = 7
WARMUP_ITERATIONS = 2

#: Endpoint benchmarks — (method, path_template, body, description)
ENDPOINT_BENCHMARKS: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/health", None, "health check"),
    ("GET", "/dashboard", None, "dashboard KPIs"),
    ("GET", "/mesh", None, "mesh stats"),
    ("GET", "/clusters", None, "cluster list"),
    ("GET", "/anomalies", None, "anomaly report"),
    ("GET", "/search?q=ws28", None, "search by name"),
    ("GET", "/device/ws28", None, "device lookup"),
    ("GET", "/device/ws28/metrics", None, "device metrics"),
    ("GET", "/device/ws28/timeline", None, "device timeline"),
    ("GET", "/device/ws28/stats", None, "device stats"),
    ("GET", "/device/ws28/metric-summary", None, "metric summary"),
    ("GET", "/device-view?q=ws28", None, "HTML device view"),
    ("POST", "/batch", {"queries": ["ws28", "dc01"]}, "batch lookup"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Seeded test database (mirrors tests/api/conftest.py pattern)
# ═══════════════════════════════════════════════════════════════════════════════


def _create_seeded_db(path: Path) -> None:
    """Create a temporary DuckDB with seeded data — matches test fixture shape."""
    from zentinull.api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL

    conn = duckdb.connect(str(path))
    now = datetime.now(UTC)

    # ── source_records ────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE source_records (
            cluster_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            name_clean TEXT DEFAULT '',
            serial_number TEXT DEFAULT '',
            mac_address TEXT DEFAULT '',
            mac_clean TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            model TEXT DEFAULT '',
            os TEXT DEFAULT '',
            os_version TEXT DEFAULT '',
            assigned_user TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            imei TEXT DEFAULT '',
            asset_tag TEXT DEFAULT ''
        )
    """)

    src_rows: list[tuple] = [
        (
            "c1",
            "sp",
            "sp_42",
            "WS28",
            "ws28",
            "SN001",
            "aa:bb:cc:dd:ee:ff",
            "aabbccddeeff",
            "Dell",
            "OptiPlex 7080",
            "Windows 10",
            "",
            "jdoe",
            "192.168.1.100",
            "",
            "",
        ),
        (
            "c1",
            "me",
            "me_101",
            "WS28",
            "ws28",
            "SN001",
            "aa:bb:cc:dd:ee:ff",
            "aabbccddeeff",
            "Dell",
            "OptiPlex 7080",
            "Windows 10",
            "",
            "jdoe",
            "",
            "",
            "",
        ),
        ("c1", "fg", "fg_7", "ws28", "ws28", "", "", "", "", "", "Windows 10", "", "", "192.168.1.100", "", ""),
        (
            "c2",
            "ad",
            "ad_12",
            "DC01",
            "dc01",
            "SN002",
            "11:22:33:44:55:66",
            "112233445566",
            "",
            "",
            "Server 2022",
            "",
            "",
            "10.0.0.1",
            "",
            "",
        ),
        ("c2", "zbx", "zbx_3", "dc01", "dc01", "SN002", "", "", "", "", "", "", "", "", "10.0.0.1", ""),
        ("c3", "sp", "sp_99", "", "", "", "", "", "", "", "", "", "", "", "", ""),
        (
            "c4",
            "me_mdm",
            "mdm_55",
            "phone01",
            "phone01",
            "SN003",
            "",
            "",
            "Apple",
            "iPhone 15",
            "iOS 17",
            "",
            "jsmith",
            "",
            "356789012345678",
            "",
        ),
    ]
    for row in src_rows:
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # ── devices ──────────────────────────────────────────────────────────
    conn.execute(DEVICES_SQL)

    # ── metrics ──────────────────────────────────────────────────────────
    conn.execute(METRICS_SQL)
    metrics_data = [
        ("c1", "zbx", "cpu_pct", 45.2, None, [], now, now),
        ("c1", "me", "cpu_pct", 42.8, None, [], now, now),
        ("c1", "zbx", "disk_pct", 67.1, None, [], now, now),
        ("c1", "me", "disk_pct", 65.0, None, [], now, now),
        ("c1", "me", "memory_pct", 58.3, None, [], now, now),
    ]
    for row in metrics_data:
        conn.execute(
            "INSERT INTO metrics (cluster_id, source, metric_name, value, text_value, tags, recorded_at, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # ── events ───────────────────────────────────────────────────────────
    conn.execute(EVENTS_SQL)
    events_data: list[tuple] = [
        ("c1", "zbx", "alert", "CPU usage above threshold", "info", now, now),
        ("c1", "me", "warning", "Disk space low", "warning", now, now),
        ("c2", "zbx", "alert", "Host unreachable", "critical", now, now),
    ]
    for row in events_data:
        conn.execute(
            "INSERT INTO events (cluster_id, source, event_type, detail, severity, recorded_at, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Benchmark runner
# ═══════════════════════════════════════════════════════════════════════════════


def _benchmark_endpoint(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    iterations: int,
    warmup: int,
) -> dict[str, float]:
    """Run a single endpoint benchmark, return timing stats in milliseconds."""
    timings: list[float] = []

    for i in range(warmup + iterations):
        start = time.perf_counter()
        if method == "GET":
            resp = client.get(path)
        elif method == "POST":
            resp = client.post(path, json=body or {})
        else:
            raise ValueError(f"Unsupported method: {method}")
        elapsed = (time.perf_counter() - start) * 1000  # ms

        # Verify success to avoid benchmarking error paths
        if resp.status_code >= 500:
            raise RuntimeError(f"{method} {path} returned {resp.status_code} (warmup iteration {i}): {resp.text[:200]}")

        if i >= warmup:
            timings.append(elapsed)

    if len(timings) < 2:
        return {"min_ms": timings[0], "max_ms": timings[0], "avg_ms": timings[0]}

    avg = sum(timings) / len(timings)
    # For small sample sizes, avoid complex stats
    return {
        "min_ms": round(min(timings), 2),
        "max_ms": round(max(timings), 2),
        "avg_ms": round(avg, 2),
        "p50_ms": round(median(timings), 2),
        "p95_ms": round(sorted(timings)[int(len(timings) * 0.95)], 2),
        "stdev_ms": round(stdev(timings), 2) if len(timings) > 2 else 0.0,
    }


def run_benchmarks(
    iterations: int = DEFAULT_ITERATIONS,
    warmup: int = WARMUP_ITERATIONS,
) -> list[dict[str, Any]]:
    """Run all endpoint benchmarks and return results list."""
    import tempfile

    from zentinull.api.db import MeshDB
    from zentinull.api.server import app

    # Create seeded test database
    tmp_dir = Path(tempfile.mkdtemp(prefix="zentinull_bench_"))
    db_path = tmp_dir / "mesh.duckdb"
    _create_seeded_db(db_path)

    # Wire to the app (replace any existing state)
    app.state.db = MeshDB(db_path)
    client = TestClient(app)

    results: list[dict[str, Any]] = []
    for method, path, body, desc in ENDPOINT_BENCHMARKS:
        try:
            stats = _benchmark_endpoint(client, method, path, body, iterations, warmup)
            results.append(
                {
                    "method": method,
                    "endpoint": path,
                    "description": desc,
                    "iterations": iterations,
                    "warmup": warmup,
                    **stats,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "method": method,
                    "endpoint": path,
                    "description": desc,
                    "error": str(exc),
                }
            )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# History persistence
# ═══════════════════════════════════════════════════════════════════════════════


def _load_history() -> list[dict[str, Any]]:
    """Load historical benchmark results."""
    if BENCH_FILE.exists():
        try:
            data = json.loads(BENCH_FILE.read_text())
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(history: list[dict[str, Any]]) -> None:
    """Save benchmark results, keeping last 20 entries."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    trimmed = history[-20:]
    BENCH_FILE.write_text(json.dumps(trimmed, indent=2, default=str))


# ═══════════════════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════════════════


def _print_results(results: list[dict[str, Any]]) -> None:
    """Pretty-print benchmark results."""
    print("═" * 72)
    title = "  Zentinull API Benchmarks"
    print(f"  {title}")
    print("═" * 72)
    print()

    for r in results:
        method_fmt = f"{r['method']:>4s}"
        path_fmt = f"{r['endpoint']:<40s}"
        if "error" in r:
            print(f"  {method_fmt}  {path_fmt}  ERROR: {r['error']}")
            continue

        label = f"  {method_fmt}  {path_fmt}"
        stats = (
            f"avg={r['avg_ms']:>7.1f}ms  "
            f"p50={r['p50_ms']:>7.1f}ms  "
            f"p95={r['p95_ms']:>7.1f}ms  "
            f"min={r['min_ms']:>6.1f}ms  "
            f"max={r['max_ms']:>7.1f}ms"
        )
        print(f"{label}  {stats}")

    print()

    # Summary
    successes = [r for r in results if "error" not in r]
    if successes:
        avg_times = [r["avg_ms"] for r in successes]
        print(f"  Total endpoints: {len(results)} ({len(successes)} ok, {len(results) - len(successes)} failed)")
        print(f"  Average of averages: {sum(avg_times) / len(avg_times):.1f}ms")
        print(f"  Fastest endpoint:    {min(avg_times):.1f}ms")
        print(f"  Slowest endpoint:    {max(avg_times):.1f}ms")


def _print_trend(history: list[dict[str, Any]]) -> None:
    """Show performance trend vs previous run."""
    if len(history) < 2:
        return

    prev = history[-2]
    curr = history[-1]
    if "summary" not in curr or "summary" not in prev:
        return

    prev_avg = prev["summary"]["avg_of_avgs"]
    curr_avg = curr["summary"]["avg_of_avgs"]
    diff = curr_avg - prev_avg
    direction = "↑ slower" if diff > 0.5 else "↓ faster" if diff < -0.5 else "≈ stable"
    print(f"  Trend vs previous run: {diff:+.1f}ms  {direction}")


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary dict for historical tracking."""
    successes = [r for r in results if "error" not in r]
    if not successes:
        return {}
    avg_times = [r["avg_ms"] for r in successes]
    return {
        "endpoints_ok": len(successes),
        "endpoints_total": len(results),
        "avg_of_avgs": round(sum(avg_times) / len(avg_times), 2),
        "fastest_avg": round(min(avg_times), 2),
        "slowest_avg": round(max(avg_times), 2),
    }


def _get_baseline(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Get the most recent successful run's summary from history (skip current=last entry)."""
    if len(history) < 2:
        return None
    for entry in reversed(history[:-1]):
        if entry.get("summary", {}).get("endpoints_ok", 0) > 0:
            return entry["summary"]
    return None


def detect_regression(
    current_summary: dict[str, Any],
    history: list[dict[str, Any]],
    threshold_pct: float = REGRESSION_THRESHOLD_PCT,
) -> dict[str, Any] | None:
    """Detect performance regression vs baseline history.

    Returns a dict with regression details if detected, None if OK.
    """
    baseline = _get_baseline(history)
    if not baseline or not current_summary:
        return None

    prev_avg = baseline["avg_of_avgs"]
    curr_avg = current_summary["avg_of_avgs"]
    if prev_avg <= 0:
        return None

    pct_change = ((curr_avg - prev_avg) / prev_avg) * 100
    if pct_change > threshold_pct:
        return {
            "baseline_ms": prev_avg,
            "current_ms": curr_avg,
            "change_pct": round(pct_change, 1),
            "threshold_pct": threshold_pct,
            "direction": "regression",
        }
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    """Run benchmarks, print results, save history.

    Flags:
        --json                     Output JSON only.
        --ci                       Enable regression gate (non-zero exit on >threshold degradation).
        --regression-threshold=N   Override regression threshold percent (default 20).
        iterations=N               Override iteration count (default 7).
    """
    raw_args = argv or sys.argv[1:]
    json_only = "--json" in raw_args
    ci_mode = "--ci" in raw_args
    custom_iterations = DEFAULT_ITERATIONS
    threshold = REGRESSION_THRESHOLD_PCT
    for a in raw_args:
        with contextlib.suppress(ValueError, IndexError):
            key, val = a.split("=", 1)
            if key == "iterations":
                custom_iterations = int(val)
            elif key == "--regression-threshold":
                threshold = float(val)

    results = run_benchmarks(iterations=custom_iterations)
    summary = _summarize(results)

    aggregate = {
        "timestamp": datetime.now(UTC).isoformat(),
        "iterations": custom_iterations,
        "summary": summary,
        "endpoints": results,
    }

    history = _load_history()
    history.append(aggregate)
    _save_history(history)

    if json_only or ci_mode:
        output = json.dumps(aggregate, indent=2, default=str)
        print(output)

        if ci_mode:
            regression = detect_regression(summary, history, threshold)
            if regression:
                print(
                    f"\n  REGRESSION DETECTED: {regression['change_pct']:+.1f}% "
                    f"({regression['baseline_ms']:.1f}ms → {regression['current_ms']:.1f}ms, "
                    f"threshold={regression['threshold_pct']:.0f}%)",
                    file=sys.stderr,
                )
                return 2
    else:
        _print_results(results)
        _print_trend(history)
        print()
        print(f"  History saved to {BENCH_FILE} ({len(history)} entries)")
        print("═" * 72)

    # If any endpoint failed, report with non-zero exit
    failures = [r for r in results if "error" in r]
    if failures:
        print(f"\n  WARNING: {len(failures)} endpoint(s) returned errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
