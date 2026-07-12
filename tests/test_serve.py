"""Tests for serve.py CLI entry points — argument parsing and command dispatch."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_args(**kwargs: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace with the given attributes."""
    return argparse.Namespace(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_status — delegates to print_status
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdStatus:
    """cmd_status calls print_status() from cli.status."""

    def test_cmd_status_calls_print_status(self) -> None:
        from serve import cmd_status

        with patch("zentinull.cli.status.print_status") as mock_print:
            args = _make_args()
            cmd_status(args)
            mock_print.assert_called_once_with()


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_logs — reads pipeline.log and prints last N lines
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdLogs:
    """cmd_logs reads data/pipeline.log and prints tail lines."""

    def test_logs_no_file_exists(self, tmp_path: Path, capsys) -> None:
        """When log file doesn't exist, a message is printed."""
        from serve import cmd_logs

        with patch("serve._HERE", tmp_path):
            args = _make_args(follow=False, lines=10)
            cmd_logs(args)

        captured = capsys.readouterr()
        assert "No pipeline log found" in captured.out

    def test_logs_prints_tail_lines(self, tmp_path: Path, capsys) -> None:
        """When log exists, the last N lines are printed."""
        from serve import cmd_logs

        log_dir = tmp_path / "data"
        log_dir.mkdir()
        log_file = log_dir / "pipeline.log"
        lines = [f"line {i}" for i in range(20)]
        log_file.write_text("\n".join(lines) + "\n")

        with patch("serve._HERE", tmp_path):
            args = _make_args(follow=False, lines=5)
            cmd_logs(args)

        captured = capsys.readouterr()
        output = captured.out.strip().splitlines()
        assert len(output) == 5
        assert output[0] == "line 15"
        assert output[-1] == "line 19"

    def test_logs_uses_default_lines(self, tmp_path: Path, capsys) -> None:
        """Default lines=50 when not specified."""
        from serve import cmd_logs

        log_dir = tmp_path / "data"
        log_dir.mkdir()
        log_file = log_dir / "pipeline.log"
        log_file.write_text("\n".join(f"line {i}" for i in range(60)) + "\n")

        with patch("serve._HERE", tmp_path):
            args = _make_args(follow=False, lines=50)
            cmd_logs(args)

        captured = capsys.readouterr()
        output = captured.out.strip().splitlines()
        assert len(output) == 50
        assert output[0] == "line 10"

    def test_logs_empty_file(self, tmp_path: Path, capsys) -> None:
        """Empty log file prints nothing."""
        from serve import cmd_logs

        log_dir = tmp_path / "data"
        log_dir.mkdir()
        (log_dir / "pipeline.log").write_text("")

        with patch("serve._HERE", tmp_path):
            args = _make_args(follow=False, lines=10)
            cmd_logs(args)

        captured = capsys.readouterr()
        assert captured.out.strip() == ""


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_db — dispatches to list_dbs / vacuum_dbs / check_dbs
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdDb:
    """cmd_db dispatches db_action to the correct helper."""

    def test_db_list_calls_list_dbs(self) -> None:
        from serve import cmd_db

        with patch("zentinull.cli.db_mgmt.list_dbs") as mock_list:
            args = _make_args(db_action="list")
            cmd_db(args)
            mock_list.assert_called_once_with()

    def test_db_vacuum_calls_vacuum_dbs(self) -> None:
        from serve import cmd_db

        with patch("zentinull.cli.db_mgmt.vacuum_dbs") as mock_vacuum:
            args = _make_args(db_action="vacuum")
            cmd_db(args)
            mock_vacuum.assert_called_once_with()

    def test_db_check_calls_check_dbs(self) -> None:
        from serve import cmd_db

        with patch("zentinull.cli.db_mgmt.check_dbs") as mock_check:
            args = _make_args(db_action="check")
            cmd_db(args)
            mock_check.assert_called_once_with()

    def test_db_unknown_action_does_nothing(self) -> None:
        """An unrecognized db_action silently does nothing."""
        from serve import cmd_db

        with (
            patch("zentinull.cli.db_mgmt.list_dbs") as mock_list,
            patch("zentinull.cli.db_mgmt.vacuum_dbs") as mock_vacuum,
            patch("zentinull.cli.db_mgmt.check_dbs") as mock_check,
        ):
            args = _make_args(db_action="unknown")
            cmd_db(args)
            mock_list.assert_not_called()
            mock_vacuum.assert_not_called()
            mock_check.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_export — delegates to run_export, prints result
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdExport:
    """cmd_export calls run_export() and prints the count."""

    def test_cmd_export_calls_run_export(self, capsys) -> None:
        from serve import cmd_export

        with patch("zentinull.cli.pipeline.run_export", return_value=42):
            args = _make_args()
            cmd_export(args)

        captured = capsys.readouterr()
        assert "42" in captured.out
        assert "Export complete" in captured.out

    def test_cmd_export_zero(self, capsys) -> None:
        """Export with 0 records still prints a message."""
        from serve import cmd_export

        with patch("zentinull.cli.pipeline.run_export", return_value=0):
            args = _make_args()
            cmd_export(args)

        captured = capsys.readouterr()
        assert "0 records" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_load — delegates to run_load, prints result
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdLoad:
    """cmd_load calls run_load() and prints the count."""

    def test_cmd_load_calls_run_load(self, capsys) -> None:
        from serve import cmd_load

        with patch("zentinull.cli.pipeline.run_load", return_value=123):
            args = _make_args()
            cmd_load(args)

        captured = capsys.readouterr()
        assert "123" in captured.out
        assert "Load complete" in captured.out

    def test_cmd_load_zero(self, capsys) -> None:
        from serve import cmd_load

        with patch("zentinull.cli.pipeline.run_load", return_value=0):
            args = _make_args()
            cmd_load(args)

        captured = capsys.readouterr()
        assert "0 devices" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_ingest — delegates to run_ingest, handles success and failure
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdIngest:
    """cmd_ingest delegates to run_ingest and prints results."""

    def test_cmd_ingest_all_success(self, capsys) -> None:
        from serve import cmd_ingest

        with patch("zentinull.cli.pipeline.run_ingest", return_value={"sp": 10, "fg": 5}):
            args = _make_args(source=None, skip=None)
            cmd_ingest(args)

        captured = capsys.readouterr()
        assert "15 records" in captured.out
        assert "2 sources" in captured.out

    def test_cmd_ingest_some_failed(self, capsys) -> None:
        from serve import cmd_ingest

        with patch("zentinull.cli.pipeline.run_ingest", return_value={"sp": 10, "zbx": -1}):
            args = _make_args(source=None, skip=None)
            cmd_ingest(args)

        captured = capsys.readouterr()
        assert "10 records" in captured.out
        assert "Failed" in captured.out
        assert "zbx" in captured.out

    def test_cmd_ingest_parses_sources(self, capsys) -> None:
        from serve import cmd_ingest

        with patch("zentinull.cli.pipeline.run_ingest", return_value={"fg": 5}) as mock_ingest:
            args = _make_args(source="fg, sp", skip=None)
            cmd_ingest(args)
            mock_ingest.assert_called_once_with(sources=["fg", "sp"], skip_sources=None)

    def test_cmd_ingest_parses_skip(self, capsys) -> None:
        from serve import cmd_ingest

        with patch("zentinull.cli.pipeline.run_ingest", return_value={"sp": 10}) as mock_ingest:
            args = _make_args(source=None, skip="zbx, ad")
            cmd_ingest(args)
            mock_ingest.assert_called_once_with(sources=None, skip_sources=["zbx", "ad"])


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_splink — delegates to run_splink with parsed arguments
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdSplink:
    """cmd_splink delegates to run_splink, passing threshold and skip_training."""

    def test_cmd_splink_defaults(self) -> None:
        from serve import cmd_splink

        with patch("zentinull.cli.pipeline.run_splink") as mock_splink:
            args = _make_args(threshold=None, skip_training=False)
            cmd_splink(args)
            mock_splink.assert_called_once_with(skip_training=False, threshold=None)

    def test_cmd_splink_with_threshold(self) -> None:
        from serve import cmd_splink

        with patch("zentinull.cli.pipeline.run_splink") as mock_splink:
            args = _make_args(threshold=-5, skip_training=False)
            cmd_splink(args)
            mock_splink.assert_called_once_with(skip_training=False, threshold=-5)

    def test_cmd_splink_skip_training(self) -> None:
        from serve import cmd_splink

        with patch("zentinull.cli.pipeline.run_splink") as mock_splink:
            args = _make_args(threshold=None, skip_training=True)
            cmd_splink(args)
            mock_splink.assert_called_once_with(skip_training=True, threshold=None)


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_backup — delegates to create_backup, parses output dir
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdBackup:
    """cmd_backup delegates to create_backup with optional output dir."""

    def test_cmd_backup_default(self, capsys) -> None:
        from serve import cmd_backup

        with patch("zentinull.cli.backup.create_backup", return_value=Path("/tmp/backup/test")) as mock_backup:
            args = _make_args(output=None)
            cmd_backup(args)
            mock_backup.assert_called_once_with(output_dir=None)

    def test_cmd_backup_with_output(self, capsys) -> None:
        from serve import cmd_backup

        with patch("zentinull.cli.backup.create_backup", return_value=Path("/tmp/custom_backup")) as mock_backup:
            args = _make_args(output="/tmp/custom_backup")
            cmd_backup(args)
            mock_backup.assert_called_once()
            output_dir_arg = mock_backup.call_args[1]["output_dir"]
            assert str(output_dir_arg) == "/tmp/custom_backup"

        captured = capsys.readouterr()
        assert "Backup complete" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_pipeline — delegates to run_pipeline with parsed args
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdPipeline:
    """cmd_pipeline delegates to run_pipeline with parsed sources/skips."""

    def test_cmd_pipeline_defaults(self) -> None:
        from serve import cmd_pipeline

        with patch("zentinull.cli.pipeline.run_pipeline") as mock_pipe:
            args = _make_args(source=None, skip=None, skip_ingest=False)
            cmd_pipeline(args)
            mock_pipe.assert_called_once_with(skip_ingest=False, sources=None, skip_sources=None)

    def test_cmd_pipeline_skip_ingest(self) -> None:
        from serve import cmd_pipeline

        with patch("zentinull.cli.pipeline.run_pipeline") as mock_pipe:
            args = _make_args(source=None, skip=None, skip_ingest=True)
            cmd_pipeline(args)
            mock_pipe.assert_called_once_with(skip_ingest=True, sources=None, skip_sources=None)

    def test_cmd_pipeline_with_sources(self) -> None:
        from serve import cmd_pipeline

        with patch("zentinull.cli.pipeline.run_pipeline") as mock_pipe:
            args = _make_args(source="sp, fg", skip=None, skip_ingest=False)
            cmd_pipeline(args)
            mock_pipe.assert_called_once_with(skip_ingest=False, sources=["sp", "fg"], skip_sources=None)

    def test_cmd_pipeline_with_skip(self) -> None:
        from serve import cmd_pipeline

        with patch("zentinull.cli.pipeline.run_pipeline") as mock_pipe:
            args = _make_args(source=None, skip="zbx, ad", skip_ingest=False)
            cmd_pipeline(args)
            mock_pipe.assert_called_once_with(skip_ingest=False, sources=None, skip_sources=["zbx", "ad"])


# ═══════════════════════════════════════════════════════════════════════════════
# cmd_start — delegates to uvicorn (we only verify argument wiring)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdStart:
    """cmd_start wires arguments and calls uvicorn.run."""

    def test_cmd_start_passes_args(self) -> None:
        from serve import cmd_start

        with (
            patch("uvicorn.run") as mock_uvicorn,
            patch("serve._setup_logging"),
        ):
            args = _make_args(port=9000, reload=True, log_json=False)
            cmd_start(args)
            mock_uvicorn.assert_called_once_with(
                "zentinull.api.server:app",
                host="0.0.0.0",
                port=9000,
                reload=True,
            )

    def test_cmd_start_default_port(self) -> None:
        from serve import cmd_start

        with (
            patch("uvicorn.run") as mock_uvicorn,
            patch("serve._setup_logging"),
        ):
            args = _make_args(port=8001, reload=False, log_json=False)
            cmd_start(args)
            mock_uvicorn.assert_called_once_with(
                "zentinull.api.server:app",
                host="0.0.0.0",
                port=8001,
                reload=False,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# main() — argument parsing and dispatch
# ═══════════════════════════════════════════════════════════════════════════════


class TestMain:
    """Main argument parsing and dispatch."""

    def test_main_parses_start(self) -> None:
        """argv = ["start", "--port", "9000"] dispatches to cmd_start with port=9000."""
        from serve import main

        with patch("serve.cmd_start") as mock_start:
            with patch("sys.argv", ["serve.py", "start", "--port", "9000"]):
                main()
            mock_start.assert_called_once()
            assert mock_start.call_args[0][0].port == 9000

    def test_main_parses_status(self) -> None:
        from serve import main

        with patch("serve.cmd_status") as mock_status:
            with patch("sys.argv", ["serve.py", "status"]):
                main()
            mock_status.assert_called_once()

    def test_main_parses_logs_default(self) -> None:
        from serve import main

        with patch("serve.cmd_logs") as mock_logs:
            with patch("sys.argv", ["serve.py", "logs"]):
                main()
            mock_logs.assert_called_once()
            args = mock_logs.call_args[0][0]
            assert args.lines == 50
            assert args.follow is False

    def test_main_parses_logs_follow(self) -> None:
        from serve import main

        with patch("serve.cmd_logs") as mock_logs:
            with patch("sys.argv", ["serve.py", "logs", "--follow"]):
                main()
            mock_logs.assert_called_once()
            assert mock_logs.call_args[0][0].follow is True

    def test_main_parses_ingest_with_skip(self) -> None:
        from serve import main

        with patch("serve.cmd_ingest") as mock_ingest:
            with patch("sys.argv", ["serve.py", "ingest", "--skip", "ad,zbx"]):
                main()
            mock_ingest.assert_called_once()
            assert mock_ingest.call_args[0][0].skip == "ad,zbx"

    def test_main_parses_splink_threshold(self) -> None:
        from serve import main

        with patch("serve.cmd_splink") as mock_splink:
            with patch("sys.argv", ["serve.py", "splink", "--threshold", "-5"]):
                main()
            mock_splink.assert_called_once()
            assert mock_splink.call_args[0][0].threshold == -5

    def test_main_parses_export(self) -> None:
        from serve import main

        with patch("serve.cmd_export") as mock_export:
            with patch("sys.argv", ["serve.py", "export"]):
                main()
            mock_export.assert_called_once()

    def test_main_parses_load(self) -> None:
        from serve import main

        with patch("serve.cmd_load") as mock_load:
            with patch("sys.argv", ["serve.py", "load"]):
                main()
            mock_load.assert_called_once()

    def test_main_parses_backup(self) -> None:
        from serve import main

        with patch("serve.cmd_backup") as mock_backup:
            with patch("sys.argv", ["serve.py", "backup", "--output", "/tmp/backup"]):
                main()
            mock_backup.assert_called_once()
            assert mock_backup.call_args[0][0].output == "/tmp/backup"

    def test_main_parses_db_list(self) -> None:
        from serve import main

        with patch("serve.cmd_db") as mock_db:
            with patch("sys.argv", ["serve.py", "db", "list"]):
                main()
            mock_db.assert_called_once()
            assert mock_db.call_args[0][0].db_action == "list"

    def test_main_parses_db_vacuum(self) -> None:
        from serve import main

        with patch("serve.cmd_db") as mock_db:
            with patch("sys.argv", ["serve.py", "db", "vacuum"]):
                main()
            mock_db.assert_called_once()
            assert mock_db.call_args[0][0].db_action == "vacuum"

    def test_main_parses_db_check(self) -> None:
        from serve import main

        with patch("serve.cmd_db") as mock_db:
            with patch("sys.argv", ["serve.py", "db", "check"]):
                main()
            mock_db.assert_called_once()
            assert mock_db.call_args[0][0].db_action == "check"

    def test_main_no_command_prints_help(self) -> None:
        """Running serve.py with no command prints help and exits 0."""
        from serve import main

        with (
            patch("sys.argv", ["serve.py"]),
            patch("sys.exit", side_effect=SystemExit) as mock_exit,
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit):
                main()
            mock_exit.assert_called_once_with(0)

    def test_main_pipeline_with_skip_ingest(self) -> None:
        from serve import main

        with patch("serve.cmd_pipeline") as mock_pipe:
            with patch("sys.argv", ["serve.py", "pipeline", "--skip-ingest"]):
                main()
            mock_pipe.assert_called_once()
            assert mock_pipe.call_args[0][0].skip_ingest is True

    def test_main_pipeline_with_sources(self) -> None:
        from serve import main

        with patch("serve.cmd_pipeline") as mock_pipe:
            with patch("sys.argv", ["serve.py", "pipeline", "--source", "sp,fg"]):
                main()
            mock_pipe.assert_called_once()
            assert mock_pipe.call_args[0][0].source == "sp,fg"


class TestCmdSeed:
    """cmd_seed delegates to scripts.seed_demo_data.seed_demo_data."""

    def test_seed_calls_seed_demo_data(self) -> None:
        from serve import cmd_seed

        with patch("scripts.seed_demo_data.seed_demo_data") as mock_seed:
            mock_seed.return_value = 42
            args = _make_args(rows=100, force=True)
            cmd_seed(args)
            mock_seed.assert_called_once_with(row_count=100, force=True)

    def test_seed_default_args(self) -> None:
        from serve import cmd_seed

        with patch("scripts.seed_demo_data.seed_demo_data") as mock_seed:
            mock_seed.return_value = 80
            args = _make_args(rows=80, force=False)
            cmd_seed(args)
            mock_seed.assert_called_once_with(row_count=80, force=False)

    def test_main_dispatches_seed(self) -> None:
        from serve import main

        with patch("serve.cmd_seed") as mock_cmd:
            with patch("sys.argv", ["serve.py", "seed", "--rows", "50", "-f"]):
                main()
            mock_cmd.assert_called_once()
            assert mock_cmd.call_args[0][0].rows == 50
            assert mock_cmd.call_args[0][0].force is True


class TestCmdBench:
    """cmd_bench delegates to scripts.bench.main."""

    def test_bench_calls_bench_main(self) -> None:
        from serve import cmd_bench

        with patch("scripts.bench.main") as mock_bench:
            args = _make_args()
            with pytest.raises(SystemExit):
                cmd_bench(args)
            mock_bench.assert_called_once_with()

    def test_main_dispatches_bench(self) -> None:
        from serve import main

        with patch("serve.cmd_bench") as mock_cmd:
            with patch("sys.argv", ["serve.py", "bench"]):
                main()
            mock_cmd.assert_called_once()


class TestCmdBenchApi:
    """cmd_bench_api delegates to scripts.bench_api.main."""

    def test_bench_api_plain(self) -> None:
        from serve import cmd_bench_api

        with patch("scripts.bench_api.main") as mock_bench:
            args = _make_args(ci=False)
            with pytest.raises(SystemExit):
                cmd_bench_api(args)
            mock_bench.assert_called_once_with(None)

    def test_bench_api_ci_mode(self) -> None:
        from serve import cmd_bench_api

        with patch("scripts.bench_api.main") as mock_bench:
            args = _make_args(ci=True)
            with pytest.raises(SystemExit):
                cmd_bench_api(args)
            mock_bench.assert_called_once_with(["--ci"])

    def test_main_dispatches_bench_api(self) -> None:
        from serve import main

        with patch("serve.cmd_bench_api") as mock_cmd:
            with patch("sys.argv", ["serve.py", "bench-api", "--ci"]):
                main()
            mock_cmd.assert_called_once()
            assert mock_cmd.call_args[0][0].ci is True
