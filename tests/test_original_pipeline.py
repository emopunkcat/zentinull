"""Tests for zentinull.pipeline — the original subprocess-based orchestrator.

Covers: _run_step, _run_splink, _load_to_duckdb, run(), _main().
Coverage target: 0 % -> ~95 %.
"""

from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# _run_step
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunStep:
    def test_successful_step(self) -> None:
        """When subprocess returns 0, _run_step returns None."""
        with patch("zentinull.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")
            from zentinull.pipeline import _run_step

            _run_step("test_step", ["arg1"], timeout=30)

    def test_failed_step_raises(self) -> None:
        """When subprocess returns non-zero, RuntimeError is raised."""
        with patch("zentinull.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="error msg")
            from zentinull.pipeline import _run_step

            with pytest.raises(RuntimeError, match="test_step failed with code 1"):
                _run_step("test_step", ["arg1"], timeout=30)

    def test_passes_python_executable(self) -> None:
        """The subprocess command starts with the configured PYTHON."""
        with (
            patch("zentinull.pipeline.subprocess.run") as mock_run,
            patch("zentinull.pipeline.PYTHON", "/usr/bin/python3"),
        ):
            mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")
            from zentinull.pipeline import _run_step

            _run_step("step", ["script.py"])
            args = mock_run.call_args[0][0]
            assert args[0] == "/usr/bin/python3"
            assert "script.py" in args

    def test_timeout_raised_as_subprocess_error(self) -> None:
        """subprocess.TimeoutExpired passes through."""
        with patch("zentinull.pipeline.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)
            from zentinull.pipeline import _run_step

            with pytest.raises(subprocess.TimeoutExpired):
                _run_step("step", ["script.py"], timeout=30)


# ═══════════════════════════════════════════════════════════════════════════════
# _run_splink
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunSplink:
    def test_successful_splink(self) -> None:
        """When splink subprocess returns 0, no error."""
        with patch("zentinull.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            from zentinull.pipeline import _run_splink

            _run_splink()

    def test_failed_splink_raises(self) -> None:
        """When splink returns non-zero, RuntimeError is raised."""
        with patch("zentinull.pipeline.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="splink error")
            from zentinull.pipeline import _run_splink

            with pytest.raises(RuntimeError, match="Splink failed"):
                _run_splink()


# ═══════════════════════════════════════════════════════════════════════════════
# _load_to_duckdb
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadToDuckDB:
    def test_delegates_to_cli_run_load(self) -> None:
        """_load_to_duckdb delegates to cli.pipeline.run_load."""
        with patch("zentinull.cli.pipeline.run_load") as mock_load:
            mock_load.return_value = 42
            from zentinull.pipeline import _load_to_duckdb

            _load_to_duckdb()
            mock_load.assert_called_once_with()


# ═══════════════════════════════════════════════════════════════════════════════
# run()
# ═══════════════════════════════════════════════════════════════════════════════


class TestRun:
    def test_dry_run_does_not_execute(self) -> None:
        """dry_run=True logs steps without executing anything."""
        with (
            patch("zentinull.pipeline._run_step") as mock_step,
            patch("zentinull.pipeline._run_splink") as mock_splink,
            patch("zentinull.pipeline._load_to_duckdb") as mock_load,
        ):
            from zentinull.pipeline import run

            run(dry_run=True)
            mock_step.assert_not_called()
            mock_splink.assert_not_called()
            mock_load.assert_not_called()

    def test_full_pipeline_executes_all_steps(self) -> None:
        """Full run calls _run_step (ingest + export), splink, then load."""
        with (
            patch("zentinull.pipeline._run_step") as mock_step,
            patch("zentinull.pipeline._run_splink") as mock_splink,
            patch("zentinull.pipeline._load_to_duckdb") as mock_load,
        ):
            from zentinull.pipeline import run

            run()
            assert mock_step.call_count == 2  # ingest + export
            mock_splink.assert_called_once_with()
            mock_load.assert_called_once_with()

    def test_skip_ingest_only_runs_export(self) -> None:
        """skip_ingest=True runs export, splink, and load but not ingest."""
        with (
            patch("zentinull.pipeline._run_step") as mock_step,
            patch("zentinull.pipeline._run_splink") as mock_splink,
            patch("zentinull.pipeline._load_to_duckdb") as mock_load,
        ):
            from zentinull.pipeline import run

            run(skip_ingest=True)
            assert mock_step.call_count == 1
            assert mock_step.call_args[0][0] == "export"
            mock_splink.assert_called_once_with()
            mock_load.assert_called_once_with()

    def test_correct_timeouts_per_step(self) -> None:
        """Ingest gets 300s timeout, export gets 60s."""
        with (
            patch("zentinull.pipeline._run_step") as mock_step,
            patch("zentinull.pipeline._run_splink"),
            patch("zentinull.pipeline._load_to_duckdb"),
        ):
            from zentinull.pipeline import run

            run()
            assert mock_step.call_args_list[0].kwargs.get("timeout") == 300
            assert mock_step.call_args_list[1].kwargs.get("timeout") == 60


# ═══════════════════════════════════════════════════════════════════════════════
# _main() -- CLI entry point
# ═══════════════════════════════════════════════════════════════════════════════


class TestMain:
    def test_main_calls_run_with_defaults(self) -> None:
        """_main() calls run() with skip_ingest=False, dry_run=False."""
        with (
            patch("zentinull.pipeline.run") as mock_run,
            patch("zentinull.pipeline.sys.argv", ["pipeline.py"]),
        ):
            from zentinull.pipeline import _main

            _main()
            mock_run.assert_called_once_with(skip_ingest=False, dry_run=False)

    def test_main_skip_ingest_flag(self) -> None:
        """--skip-ingest flag is passed to run()."""
        with (
            patch("zentinull.pipeline.run") as mock_run,
            patch("zentinull.pipeline.sys.argv", ["pipeline.py", "--skip-ingest"]),
        ):
            from zentinull.pipeline import _main

            _main()
            mock_run.assert_called_once_with(skip_ingest=True, dry_run=False)

    def test_main_dry_run_flag(self) -> None:
        """--dry-run flag is passed to run()."""
        with (
            patch("zentinull.pipeline.run") as mock_run,
            patch("zentinull.pipeline.sys.argv", ["pipeline.py", "--dry-run"]),
        ):
            from zentinull.pipeline import _main

            _main()
            mock_run.assert_called_once_with(skip_ingest=False, dry_run=True)

    def test_main_both_flags(self) -> None:
        """Both --skip-ingest and --dry-run flags are passed."""
        with (
            patch("zentinull.pipeline.run") as mock_run,
            patch(
                "zentinull.pipeline.sys.argv",
                ["pipeline.py", "--skip-ingest", "--dry-run"],
            ),
        ):
            from zentinull.pipeline import _main

            _main()
            mock_run.assert_called_once_with(skip_ingest=True, dry_run=True)

    def test_main_exception_calls_sys_exit(self) -> None:
        """When run() raises, _main() calls sys.exit(1)."""
        with (
            patch("zentinull.pipeline.run", side_effect=ValueError("oops")),
            patch("zentinull.pipeline.sys.exit") as mock_exit,
        ):
            from zentinull.pipeline import _main

            _main()
            mock_exit.assert_called_once_with(1)
