#!/usr/bin/env python3
"""
Pipeline benchmark runner.

Runs the test suite, captures timing/coverage metrics,
stores historical results in .benchmarks/ for trend tracking.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BENCH_DIR = ROOT / ".benchmarks"
BENCH_FILE = BENCH_DIR / "results.json"


def _capture(label: str, cmd: list[str], cwd: str) -> dict:
    """Run *cmd* and return timing + output metrics."""
    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - start

    test_count = 0
    coverage_pct = 0.0
    # pytest writes summary to stdout (-q mode) or stderr (--cov mode)
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        m = re.search(r"(\d+) passed", line)
        if m:
            test_count = max(test_count, int(m.group(1)))
        if line.startswith("TOTAL"):
            parts = line.split()
            if len(parts) >= 4:
                with contextlib.suppress(ValueError):
                    coverage_pct = float(parts[-1].rstrip("%"))
    return {
        "label": label,
        "exit_code": result.returncode,
        "elapsed_s": round(elapsed, 2),
        "test_count": test_count,
        "coverage_pct": coverage_pct,
        "stderr_tail": result.stderr[-500:],
    }


def _load_history() -> list[dict]:
    """Load historical benchmark results."""
    if BENCH_FILE.exists():
        try:
            with open(str(BENCH_FILE)) as f:
                data = json.load(f)
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(history: list[dict]) -> None:
    """Save benchmark results."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(str(BENCH_FILE), "w") as f:
        json.dump(history, f, indent=2)


def _print_run(result: dict) -> None:
    """Print a single run's results."""
    status = "PASS" if result["exit_code"] == 0 else "FAIL"
    cov = f"{result['coverage_pct']:.1f}%" if result["coverage_pct"] else "N/A"
    print(f"  {result['label']:<30} {status}  {result['elapsed_s']:>7.2f}s  {result['test_count']:>4} tests  {cov}")


def main() -> int:
    """Run benchmarks and report."""
    cwd = str(ROOT)
    python = sys.executable or "python3"

    print("═" * 60)
    print("  Zentinull Benchmark Suite")
    print("═" * 60)

    runs = [
        ("test (pytest)", [python, "-m", "pytest", "tests/", "-q", "--tb=no"]),
        (
            "test-cov (pytest --cov)",
            [
                python,
                "-m",
                "pytest",
                "tests/",
                "-q",
                "--tb=no",
                "--cov=src/zentinull",
                "--cov-report=term-missing",
            ],
        ),
    ]
    results: list[dict] = []

    has_failure = False
    for label, cmd in runs:
        print(f"\n  Running: {label}")
        result = _capture(label, cmd, cwd)
        results.append(result)
        _print_run(result)
        if result["exit_code"] != 0:
            print(f"  WARNING: {label} failed (exit {result['exit_code']})")
            has_failure = True

    total_tests = max(r["test_count"] for r in results)
    avg_time = sum(r["elapsed_s"] for r in results) / len(results)
    overall_cov = max(r["coverage_pct"] for r in results)

    history = _load_history()
    previous = history[-1] if history else None

    print("\n─" * 60)
    print("  Summary")
    print("─" * 60)
    print(f"  Tests:        {total_tests}")
    if overall_cov:
        print(f"  Coverage:     {overall_cov:.1f}%")
    print(f"  Avg time:     {avg_time:.2f}s")
    if previous:
        prev_avg = previous.get("avg_time_s", 0)
        dt = avg_time - prev_avg
        dt_str = f"{dt:+.2f}s" if abs(dt) > 0.01 else "same"
        print(f"  vs baseline:  {dt_str}  (baseline avg={prev_avg:.2f}s)")

    aggregate = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_tests": total_tests,
        "avg_time_s": round(avg_time, 2),
        "coverage_pct": overall_cov,
        "runs": results,
    }
    history.append(aggregate)
    _save_history(history)

    print(f"\n  History saved to {BENCH_FILE} ({len(history)} entries)")
    print("═" * 60)
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
