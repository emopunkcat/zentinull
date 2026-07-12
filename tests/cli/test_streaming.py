"""Tests for zentinull.cli.streaming — run_streaming, stream_command, and helpers."""

from __future__ import annotations

import sys

import pytest


class TestRunStreaming:
    """run_streaming() — subprocess runner with streaming output."""

    def test_simple_command(self) -> None:
        """Running a simple echo command collects the output line."""
        from zentinull.cli.streaming import run_streaming

        returncode, lines = run_streaming(
            [sys.executable, "-c", "print('hello world')"],
            "test",
            timeout=10,
        )

        assert returncode == 0
        assert len(lines) >= 1
        assert "hello world" in lines[0]

    def test_multiline_output(self) -> None:
        """Multiple lines from a subprocess are all collected."""
        from zentinull.cli.streaming import run_streaming

        returncode, lines = run_streaming(
            [sys.executable, "-c", "for i in range(3): print(f'line{i}')"],
            "test",
            timeout=10,
        )

        assert returncode == 0
        assert lines[:3] == ["line0", "line1", "line2"]

    def test_non_zero_exit_raises_error(self) -> None:
        """A subprocess that exits with non-zero code raises RuntimeError."""
        from zentinull.cli.streaming import run_streaming

        with pytest.raises(RuntimeError, match="exited with code"):
            run_streaming(
                [sys.executable, "-c", "exit(42)"],
                "test",
                timeout=10,
            )

    def test_no_output(self) -> None:
        """A subprocess that produces no output returns an empty list."""
        from zentinull.cli.streaming import run_streaming

        returncode, lines = run_streaming(
            [sys.executable, "-c", ""],
            "test",
            timeout=10,
        )

        assert returncode == 0
        assert lines == []

    def test_environment_merge(self) -> None:
        """Custom env vars are visible inside the subprocess."""
        from zentinull.cli.streaming import run_streaming

        returncode, lines = run_streaming(
            [sys.executable, "-c", "import os; print(os.environ.get('MY_VAR', 'missing'))"],
            "test",
            timeout=10,
            env={"MY_VAR": "custom_value"},
        )

        assert returncode == 0
        assert "custom_value" in lines[0]


class TestStreamCommand:
    """stream_command() — convenience wrapper that splits args."""

    def test_stream_command_delegates(self) -> None:
        """stream_command correctly delegates to run_streaming."""
        from zentinull.cli.streaming import stream_command

        returncode, lines = stream_command(
            "test",
            sys.executable,
            "-c",
            "print('from stream_command')",
            timeout=10,
        )

        assert returncode == 0
        assert "from stream_command" in lines[0]

    def test_stream_command_raises_on_failure(self) -> None:
        """stream_command propagates RuntimeError from non-zero exit."""
        from zentinull.cli.streaming import stream_command

        with pytest.raises(RuntimeError, match="exited with code"):
            stream_command("test", sys.executable, "-c", "exit(1)", timeout=10)


class TestRunStreamingErrors:
    """run_streaming() — error and timeout paths."""

    def test_timeout_raises_runtime_error(self) -> None:
        """A subprocess that exceeds the timeout raises RuntimeError."""
        from zentinull.cli.streaming import run_streaming

        with pytest.raises(RuntimeError, match="timed out"):
            run_streaming(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "timeout_test",
                timeout=1,
            )

    def test_timeout_message_contains_tag(self) -> None:
        """The timeout error message includes the tag for traceability."""
        from zentinull.cli.streaming import run_streaming

        with pytest.raises(RuntimeError) as exc_info:
            run_streaming(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "my_tag",
                timeout=1,
            )
        assert "my_tag" in str(exc_info.value)
        assert "timed out" in str(exc_info.value)

    def test_process_killed_on_broad_exception(self) -> None:
        """A subprocess that raises a broad exception is killed gracefully."""
        from zentinull.cli.streaming import run_streaming

        with pytest.raises(RuntimeError):
            run_streaming(
                [sys.executable, "-c", "import sys; sys.stdout.write('ok'); sys.stdout.flush(); raise SystemExit(1)"],
                "crash_test",
                timeout=10,
            )
