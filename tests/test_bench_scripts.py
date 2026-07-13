"""Tests for scripts/bench.py and scripts/bench_api.py benchmark runners."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# ═══════════════════════════════════════════════════════════════════════════════
# bench.py  — pytest timing / coverage benchmark
# ═══════════════════════════════════════════════════════════════════════════════


class TestBenchCapture:
    """_capture() parses subprocess output correctly."""

    def test_parses_test_count_from_stdout(self) -> None:
        from scripts.bench import _capture

        with patch("scripts.bench.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "410 passed in 14.82s\n"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            result = _capture("test", ["pytest", "tests/"], "/tmp")

        assert result["exit_code"] == 0
        assert result["test_count"] == 410
        assert result["coverage_pct"] == 0.0

    def test_parses_coverage_from_stderr(self) -> None:
        from scripts.bench import _capture

        with patch("scripts.bench.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = "TOTAL  1681  141  92%\npytest: 410 passed in 16.47s\n"
            mock_run.return_value = mock_proc

            result = _capture("test-cov", ["pytest", "--cov"], "/tmp")

        assert result["test_count"] == 410
        assert result["coverage_pct"] == 92.0

    def test_handles_no_tests(self) -> None:
        from scripts.bench import _capture

        with patch("scripts.bench.subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "no tests ran\n"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            result = _capture("empty", ["pytest"], "/tmp")

        assert result["test_count"] == 0
        assert result["coverage_pct"] == 0.0


class TestBenchHistory:
    """History persistence for bench.py."""

    def test_load_empty_when_no_file(self, tmp_path: Path) -> None:
        from scripts.bench import _load_history

        with patch("scripts.bench.BENCH_FILE", tmp_path / "noexist.json"):
            assert _load_history() == []

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        from scripts.bench import _load_history, _save_history

        fake = tmp_path / "results.json"
        with patch("scripts.bench.BENCH_FILE", fake):
            _save_history([{"label": "test", "elapsed_s": 1.5}])
            loaded = _load_history()
        assert len(loaded) == 1
        assert loaded[0]["label"] == "test"

    def test_load_handles_corrupt_json(self, tmp_path: Path) -> None:
        from scripts.bench import _load_history

        fake = tmp_path / "results.json"
        fake.write_text("{bad}")
        with patch("scripts.bench.BENCH_FILE", fake):
            assert _load_history() == []

    def test_load_wraps_single_dict_in_list(self, tmp_path: Path) -> None:
        from scripts.bench import _load_history

        fake = tmp_path / "results.json"
        fake.write_text('{"label": "test"}')
        with patch("scripts.bench.BENCH_FILE", fake):
            loaded = _load_history()
        assert isinstance(loaded, list)
        assert loaded[0]["label"] == "test"


class TestBenchPrintRun:
    """_print_run formatting."""

    def test_prints_pass_with_coverage(self, capsys) -> None:
        from scripts.bench import _print_run

        _print_run({"label": "test", "exit_code": 0, "elapsed_s": 14.5, "test_count": 410, "coverage_pct": 92.0})
        captured = capsys.readouterr()
        assert "PASS" in captured.out
        assert "14.50s" in captured.out
        assert "410" in captured.out
        assert "92.0%" in captured.out

    def test_prints_fail_without_coverage(self, capsys) -> None:
        from scripts.bench import _print_run

        _print_run({"label": "broken", "exit_code": 1, "elapsed_s": 0.5, "test_count": 0, "coverage_pct": 0.0})
        captured = capsys.readouterr()
        assert "FAIL" in captured.out


class TestBenchMain:
    """bench.py main() orchestrates runs and saves history."""

    def test_main_runs_and_saves(self, tmp_path: Path) -> None:
        from scripts.bench import main

        bench_dir = tmp_path / ".benchmarks"
        with (
            patch("scripts.bench.BENCH_DIR", bench_dir),
            patch("scripts.bench.BENCH_FILE", bench_dir / "results.json"),
            patch("scripts.bench._capture") as mock_capture,
        ):
            mock_capture.side_effect = [
                {"label": "test (pytest)", "exit_code": 0, "elapsed_s": 10.0, "test_count": 410, "coverage_pct": 0.0},
                {"label": "test-cov", "exit_code": 0, "elapsed_s": 12.0, "test_count": 410, "coverage_pct": 92.0},
            ]
            rc = main()

        assert rc == 0
        history_file = bench_dir / "results.json"
        assert history_file.exists()
        history = json.loads(history_file.read_text())
        assert len(history) == 1
        assert history[0]["total_tests"] == 410
        assert history[0]["coverage_pct"] == 92.0

    def test_main_returns_1_on_failure(self, tmp_path: Path) -> None:
        from scripts.bench import main

        bench_dir = tmp_path / ".benchmarks"
        with (
            patch("scripts.bench.BENCH_DIR", bench_dir),
            patch("scripts.bench.BENCH_FILE", bench_dir / "results.json"),
            patch("scripts.bench._capture") as mock_capture,
        ):
            mock_capture.side_effect = [
                {"label": "test (pytest)", "exit_code": 1, "elapsed_s": 0.5, "test_count": 0, "coverage_pct": 0.0},
                {"label": "test-cov", "exit_code": 0, "elapsed_s": 1.0, "test_count": 0, "coverage_pct": 0.0},
            ]
            rc = main()

        assert rc == 1


# ═══════════════════════════════════════════════════════════════════════════════
# bench_api.py — API endpoint performance benchmark
# ═══════════════════════════════════════════════════════════════════════════════


class TestBenchAPICreateDB:
    """_create_seeded_db creates a valid DuckDB mesh."""

    def test_creates_tables_and_data(self, tmp_path: Path) -> None:
        import duckdb

        from scripts.bench_api import _create_seeded_db

        db_path = tmp_path / "test_mesh.duckdb"
        _create_seeded_db(db_path)

        assert db_path.exists()
        conn = duckdb.connect(str(db_path))

        tables_duck = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        ]
        assert "source_records" in tables_duck
        assert "devices" in tables_duck
        assert "metrics" in tables_duck
        assert "events" in tables_duck

        # Verify data
        device_count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert device_count == 4

        record_count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        assert record_count == 7

        conn.close()


class TestBenchAPIBenchmarkEndpoint:
    """_benchmark_endpoint measures timing correctly."""

    def test_returns_timing_stats(self) -> None:
        from scripts.bench_api import _benchmark_endpoint

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_client.get.return_value = mock_response

        stats = _benchmark_endpoint(mock_client, "GET", "/health", None, iterations=5, warmup=2)

        # Should have run 7 total calls (2 warmup + 5 measured)
        assert mock_client.get.call_count == 7
        assert all(k in stats for k in ("min_ms", "max_ms", "avg_ms", "p50_ms", "p95_ms", "stdev_ms"))
        assert stats["min_ms"] <= stats["avg_ms"] <= stats["max_ms"]

    def test_raises_on_5xx(self) -> None:
        from scripts.bench_api import _benchmark_endpoint

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_client.get.return_value = mock_response

        import pytest

        with pytest.raises(RuntimeError, match="503"):
            _benchmark_endpoint(mock_client, "GET", "/broken", None, iterations=3, warmup=1)

    def test_works_with_post(self) -> None:
        from scripts.bench_api import _benchmark_endpoint

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_client.post.return_value = mock_response

        stats = _benchmark_endpoint(mock_client, "POST", "/batch", {"queries": ["a"]}, iterations=3, warmup=1)
        assert stats["avg_ms"] >= 0
        assert mock_client.post.call_count == 4


class TestBenchAPISummarize:
    """_summarize aggregates results."""

    def test_summarizes_successful_results(self) -> None:
        from scripts.bench_api import _summarize

        results = [
            {"method": "GET", "endpoint": "/health", "avg_ms": 1.5},
            {"method": "GET", "endpoint": "/dashboard", "avg_ms": 30.0},
        ]
        summary = _summarize(results)
        assert summary["endpoints_ok"] == 2
        assert summary["avg_of_avgs"] == 15.75
        assert summary["fastest_avg"] == 1.5
        assert summary["slowest_avg"] == 30.0

    def test_skips_errors(self) -> None:
        from scripts.bench_api import _summarize

        results = [
            {"method": "GET", "endpoint": "/health", "avg_ms": 1.5},
            {"method": "GET", "endpoint": "/broken", "error": "503"},
        ]
        summary = _summarize(results)
        assert summary["endpoints_ok"] == 1
        assert summary["endpoints_total"] == 2

    def test_empty_results(self) -> None:
        from scripts.bench_api import _summarize

        assert _summarize([]) == {}
        assert _summarize([{"endpoint": "/bad", "error": "err"}]) == {}


class TestBenchAPIPrintResults:
    """_print_results and _print_trend formatting."""

    def test_prints_all_endpoints(self, capsys) -> None:
        from scripts.bench_api import _print_results

        results = [
            {
                "method": "GET",
                "endpoint": "/health",
                "avg_ms": 1.5,
                "p50_ms": 1.4,
                "p95_ms": 1.8,
                "min_ms": 1.2,
                "max_ms": 2.0,
            },
            {"method": "GET", "endpoint": "/broken", "error": "timeout"},
        ]
        _print_results(results)
        captured = capsys.readouterr()
        assert "health" in captured.out
        assert "1.5ms" in captured.out
        assert "ERROR" in captured.out
        assert "timeout" in captured.out

    def test_print_trend_shows_nothing_with_one_entry(self, capsys) -> None:
        from scripts.bench_api import _print_trend

        history = [{"summary": {"avg_of_avgs": 20.0}}]
        _print_trend(history)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_print_trend_shows_regression(self, capsys) -> None:
        from scripts.bench_api import _print_trend

        history = [
            {"summary": {"avg_of_avgs": 15.0}},
            {"summary": {"avg_of_avgs": 30.0}},
        ]
        _print_trend(history)
        captured = capsys.readouterr()
        assert "slower" in captured.out or "+15.0ms" in captured.out


class TestBenchAPIHistory:
    """History persistence for bench_api.py."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        from scripts.bench_api import _load_history, _save_history

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
        ):
            _save_history([{"timestamp": "now", "summary": {"avg_of_avgs": 20.0}}])
            loaded = _load_history()

        assert len(loaded) == 1
        assert loaded[0]["summary"]["avg_of_avgs"] == 20.0

    def test_trim_to_20_entries(self, tmp_path: Path) -> None:
        from scripts.bench_api import _load_history, _save_history

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"
        many_entries = [{"i": i} for i in range(25)]
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
        ):
            _save_history(many_entries)
            loaded = _load_history()

        assert len(loaded) == 20
        assert loaded[0]["i"] == 5  # trimmed oldest 5


class TestBenchAPIRegression:
    """detect_regression() and _get_baseline() for CI regression gate."""

    def test_get_baseline_no_history(self) -> None:
        from scripts.bench_api import _get_baseline

        assert _get_baseline([]) is None
        assert _get_baseline([{"summary": {"endpoints_ok": 5}}]) is None  # only current

    def test_get_baseline_skips_current(self) -> None:
        from scripts.bench_api import _get_baseline

        history = [
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 10.0}},  # baseline
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 15.0}},  # current
        ]
        baseline = _get_baseline(history)
        assert baseline is not None
        assert baseline["avg_of_avgs"] == 10.0

    def test_get_baseline_empty_summary(self) -> None:
        from scripts.bench_api import _get_baseline

        history = [
            {"summary": {}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 15.0}},
        ]
        assert _get_baseline(history) is None

    def test_detect_no_baseline(self) -> None:
        from scripts.bench_api import detect_regression

        assert detect_regression({"avg_of_avgs": 15.0}, []) is None

    def test_detect_ok_within_threshold(self) -> None:
        from scripts.bench_api import detect_regression

        history = [
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 10.0}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 11.0}},  # 10% increase — under 20%
        ]
        assert detect_regression(history[-1]["summary"], history) is None

    def test_detect_crosses_threshold(self) -> None:
        from scripts.bench_api import detect_regression

        history = [
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 10.0}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 13.0}},  # 30% increase — over 20%
        ]
        reg = detect_regression(history[-1]["summary"], history)
        assert reg is not None
        assert reg["direction"] == "regression"
        assert reg["change_pct"] == 30.0
        assert reg["baseline_ms"] == 10.0
        assert reg["current_ms"] == 13.0

    def test_detect_custom_threshold(self) -> None:
        from scripts.bench_api import detect_regression

        history = [
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 10.0}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 12.0}},  # 20% — at threshold, not over
        ]
        assert detect_regression(history[-1]["summary"], history, threshold_pct=19.9) is not None
        assert detect_regression(history[-1]["summary"], history, threshold_pct=20.0) is None

    def test_detect_zero_baseline(self) -> None:
        from scripts.bench_api import detect_regression

        history = [
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 0.0}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 5.0}},
        ]
        assert detect_regression(history[-1]["summary"], history) is None

    def test_detect_no_endpoints_ok(self) -> None:
        from scripts.bench_api import detect_regression

        history = [
            {"summary": {"endpoints_ok": 0, "avg_of_avgs": 10.0}},
            {"summary": {"endpoints_ok": 5, "avg_of_avgs": 15.0}},
        ]
        assert detect_regression(history[-1]["summary"], history) is None


class TestBenchAPIMainCI:
    """bench_api.py main() with --ci flag."""

    def test_ci_no_regression(self, tmp_path: Path) -> None:
        from scripts.bench_api import main

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"

        # First run — no baseline yet
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {
                    "method": "GET",
                    "endpoint": "/health",
                    "avg_ms": 1.5,
                    "p50_ms": 1.4,
                    "p95_ms": 1.8,
                    "min_ms": 1.2,
                    "max_ms": 2.0,
                },
            ]
            rc = main(["--ci"])
        assert rc == 0  # no baseline → OK

        # Second run — similar performance → OK
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {
                    "method": "GET",
                    "endpoint": "/health",
                    "avg_ms": 1.5,
                    "p50_ms": 1.4,
                    "p95_ms": 1.8,
                    "min_ms": 1.2,
                    "max_ms": 2.0,
                },
            ]
            rc = main(["--ci"])
        assert rc == 0

    def test_ci_triggers_on_regression(self, tmp_path: Path) -> None:
        from scripts.bench_api import main

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"

        # Seed baseline: 10ms
        fake_dir.mkdir(parents=True, exist_ok=True)
        fake_file.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-01-01T00:00:00",
                        "summary": {"endpoints_ok": 1, "avg_of_avgs": 10.0},
                        "endpoints": [{"method": "GET", "endpoint": "/health", "avg_ms": 10.0}],
                    },
                ]
            )
        )

        # Current run: 15ms → 50% increase, over 20% threshold
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {
                    "method": "GET",
                    "endpoint": "/health",
                    "avg_ms": 15.0,
                    "p50_ms": 14.0,
                    "p95_ms": 18.0,
                    "min_ms": 12.0,
                    "max_ms": 20.0,
                },
            ]
            rc = main(["--ci"])
        assert rc == 2  # regression detected


class TestBenchAPIMain:
    """bench_api.py main() entrypoint."""

    def test_main_runs_and_saves(self, tmp_path: Path) -> None:
        from scripts.bench_api import main

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"

        # Patch the heavy internals to avoid creating a real DuckDB + TestClient
        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {
                    "method": "GET",
                    "endpoint": "/health",
                    "avg_ms": 1.5,
                    "p50_ms": 1.4,
                    "p95_ms": 1.8,
                    "min_ms": 1.2,
                    "max_ms": 2.0,
                },
            ]
            rc = main([])

        assert rc == 0
        assert fake_file.exists()
        history = json.loads(fake_file.read_text())
        assert len(history) == 1
        assert history[0]["summary"]["avg_of_avgs"] == 1.5

    def test_main_json_output(self, tmp_path: Path) -> None:
        from scripts.bench_api import main

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"

        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {
                    "method": "GET",
                    "endpoint": "/health",
                    "avg_ms": 1.5,
                    "p50_ms": 1.4,
                    "p95_ms": 1.8,
                    "min_ms": 1.2,
                    "max_ms": 2.0,
                },
            ]
            rc = main(["--json"])

        assert rc == 0

    def test_main_returns_1_on_errors(self, tmp_path: Path) -> None:
        from scripts.bench_api import main

        fake_dir = tmp_path / ".benchmarks"
        fake_file = fake_dir / "api_results.json"

        with (
            patch("scripts.bench_api.BENCH_DIR", fake_dir),
            patch("scripts.bench_api.BENCH_FILE", fake_file),
            patch("scripts.bench_api.run_benchmarks") as mock_bench,
        ):
            mock_bench.return_value = [
                {"method": "GET", "endpoint": "/broken", "error": "timeout"},
            ]
            rc = main([])

        assert rc == 1


class TestBenchAPIRunBenchmarksIntegration:
    """Integration test — runs real bench_api flows with a tiny DB."""

    def test_creates_db_and_runs_endpoints(self, tmp_path: Path) -> None:
        """Run benchmarks with a real seeded DB and TestClient over mocked endpoints."""

        from fastapi.testclient import TestClient

        from scripts.bench_api import ENDPOINT_BENCHMARKS, _create_seeded_db
        from zentinull.api.db import MeshDB
        from zentinull.api.server import app

        db_path = tmp_path / "bench.duckdb"
        _create_seeded_db(db_path)
        app.state.db = MeshDB(db_path)
        client = TestClient(app)

        # Health endpoint — fastest, simplest verification
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["mesh_db"] == "connected"
        assert body["mesh_file"] == "present"

        # Dashboard — real KPIs
        resp = client.get("/dashboard")
        assert resp.status_code == 200

        # Search
        resp = client.get("/search?q=ws28")
        assert resp.status_code == 200
        results = resp.json()
        assert any(r.get("device_name") == "ws28" for r in results)

        # Device lookup
        resp = client.get("/device/ws28")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("device_name") == "ws28"

        # Verify we have the right endpoint declarations
        paths = [ep[1] for ep in ENDPOINT_BENCHMARKS]
        assert "/health" in paths
        assert "/dashboard" in paths
        assert "/search?q=ws28" in paths
        assert "/device/ws28" in paths
        assert len(paths) == 13
