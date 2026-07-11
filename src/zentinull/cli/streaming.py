"""Subprocess runner with line-by-line streaming to terminal and rotating log file.

Usage:
    from zentinull.cli.streaming import run_streaming
    returncode, lines = run_streaming(["python", "scripts/run_ingest.py"], tag="ingest")
"""

from __future__ import annotations

import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOG_PATH = ROOT / "data" / "pipeline.log"

_pipeline_log: logging.Logger | None = None


def _stream_handler() -> RotatingFileHandler:
    """Return a rotating file handler writing to pipeline.log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(LOG_PATH),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _get_pipeline_log() -> logging.Logger:
    """Lazily create a dedicated logger for the pipeline log file."""
    global _pipeline_log
    if _pipeline_log is None:
        _pipeline_log = logging.getLogger("zig.cli.streaming")
        _pipeline_log.setLevel(logging.DEBUG)
        _pipeline_log.propagate = False
        _pipeline_log.addHandler(_stream_handler())
    return _pipeline_log


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
        RuntimeError: If the process exits with a non-zero return code.
    """
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

    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n\r")
            tagged = f"[{tag}] {line}"
            print(tagged, file=sys.stderr, flush=True)
            pipeline_log.info(tagged)
            output_lines.append(line)

        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            msg = f"[{tag}] timed out after {timeout}s"
            print(msg, file=sys.stderr, flush=True)
            pipeline_log.info(msg)
            raise RuntimeError(msg) from None

    except BaseException:
        process.kill()
        process.wait()
        raise

    if returncode != 0:
        msg = f"[{tag}] exited with code {returncode}"
        print(msg, file=sys.stderr, flush=True)
        pipeline_log.info(msg)
        raise RuntimeError(msg)

    return returncode, output_lines


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
