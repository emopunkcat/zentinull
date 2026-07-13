"""Subprocess runner with line-by-line streaming to terminal and rotating log file.

Usage:
    from zentinull.cli.streaming import run_streaming
    returncode, lines = run_streaming(["python", "scripts/run_ingest.py"], tag="ingest")
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from ..logging_config import get_logger

log = get_logger("cli.streaming")


def _get_pipeline_log() -> logging.Logger:
    """Return standard logger for streaming commands."""
    return log


def run_streaming(
    cmd: list[str],
    tag: str,
    *,
    timeout: int = 120,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Run *cmd* in a subprocess, streaming each output line to stderr and pipeline.log.

    Every line produced by the subprocess (both stdout and stderr, merged) is:
      * printed to ``sys.stderr`` as ``[tag] <line>`` for terminal visibility,
      * appended to ``data/pipeline.log`` (rotated at 10 MiB, 5 backups).

    The timeout is enforced during output reading — if the subprocess produces no
    output or runs longer than *timeout* seconds, it is killed and ``RuntimeError``
    is raised.

    Args:
        cmd: The command to execute, as a list of strings (e.g. ``["python", "script.py"]``).
        tag: Short source label used to prefix every output line (e.g. ``"ingest"``).
        timeout: Maximum wall-clock seconds for the entire subprocess run.
        cwd: Working directory for the subprocess (default: current directory).
        env: Optional environment variables to merge with the current environment.

    Returns:
        A ``(returncode, lines)`` tuple where *lines* is the complete output as a list
        of strings (trailing newlines stripped).

    Raises:
        RuntimeError: If the process exits with a non-zero return code or times out.
    """
    import threading as _threading

    pipeline_log = _get_pipeline_log()
    output_lines: list[str] = []
    cwd_str = str(cwd) if cwd else None

    popen_env: dict[str, str] | None = None
    if env is not None:
        import os as _os

        popen_env = dict(_os.environ)
        popen_env.update(env)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd_str,
        env=popen_env,
    )

    assert process.stdout is not None

    def _read_output() -> None:
        """Read subprocess output line by line in a background thread."""
        out = process.stdout
        assert out is not None
        for raw_line in out:
            line = raw_line.rstrip("\n\r")
            pipeline_log.info(f"[{tag}] {line}")
            _emit_line(line, tag)
            output_lines.append(line)

    try:
        reader = _threading.Thread(target=_read_output, daemon=True)
        reader.start()

        returncode = process.wait(timeout=timeout)
        reader.join()

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        msg = f"[{tag}] timed out after {timeout}s"
        _emit_error(msg)
        pipeline_log.info(msg)
        raise RuntimeError(msg) from None

    except BaseException:
        process.kill()
        process.wait()
        raise

    if returncode != 0:
        msg = f"[{tag}] exited with code {returncode}"
        _emit_error(msg)
        pipeline_log.info(msg)
        raise RuntimeError(msg)

    return returncode, output_lines


def _emit_line(line: str, tag: str) -> None:
    """Print a subprocess output line to stderr, using brutalist rendering if enabled."""
    from .render import is_brutalist_enabled, render_line

    if is_brutalist_enabled():
        render_line(f"[{tag}] {line}", with_tag=True)
    else:
        print(f"[{tag}] {line}", file=sys.stderr, flush=True)


def _emit_error(msg: str) -> None:
    """Print an error/status message to stderr, using brutalist rendering if enabled."""
    from .render import is_brutalist_enabled, render_line

    if is_brutalist_enabled():
        render_line(msg, with_tag=True)
    else:
        print(msg, file=sys.stderr, flush=True)


def stream_command(
    tag: str,
    *args: str,
    timeout: int = 120,
    cwd: str | Path | None = None,
) -> tuple[int, list[str]]:
    """Convenience wrapper: pass command args as separate strings.

    Example::

        stream_command("ingest", "python", "scripts/run_ingest.py", timeout=300)

    Equivalent to ``run_streaming(["python", "scripts/run_ingest.py"], "ingest", timeout=300)``.
    """
    return run_streaming(list(args), tag, timeout=timeout, cwd=cwd)
