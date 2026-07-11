"""Tests for logging_config — setup(), get_logger(), and StepTimer."""

from __future__ import annotations

import logging

import zentinull.logging_config as lc


def _reset_state() -> None:
    """Reset module-level state and clean up handlers between tests."""
    lc._initialized = False
    lc._loggers.clear()
    root = logging.getLogger("zig")
    root.handlers.clear()
    root.propagate = True


# ── setup() tests ──────────────────────────────────────────────────────────────


def test_setup_creates_handler() -> None:
    _reset_state()
    lc.setup()
    root = logging.getLogger("zig")
    assert len(root.handlers) >= 1
    _reset_state()


def test_setup_json_mode() -> None:
    _reset_state()
    lc.setup(json_output=True)
    root = logging.getLogger("zig")
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, lc.JsonFormatter)]
    assert len(json_handlers) >= 1
    _reset_state()


def test_setup_file_handler(tmp_path) -> None:
    _reset_state()
    log_path = tmp_path / "test.log"
    lc.setup(log_file=log_path)
    root = logging.getLogger("zig")
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1
    assert log_path.exists()
    _reset_state()


def test_setup_custom_level() -> None:
    _reset_state()
    lc.setup(level="DEBUG")
    root = logging.getLogger("zig")
    assert root.level == logging.DEBUG
    _reset_state()


# ── get_logger() tests ─────────────────────────────────────────────────────────


def test_get_logger_auto_setup() -> None:
    _reset_state()
    lc.get_logger("test")
    root = logging.getLogger("zig")
    assert len(root.handlers) >= 1
    assert lc._initialized is True
    _reset_state()


def test_get_logger_caching() -> None:
    _reset_state()
    logger1 = lc.get_logger("x")
    logger2 = lc.get_logger("x")
    assert logger1 is logger2
    _reset_state()


def test_get_logger_name_prefix() -> None:
    _reset_state()
    lc._initialized = True  # skip auto-setup
    logger = lc.get_logger("foo")
    assert logger.name == "zig.foo"
    _reset_state()


# ── StepTimer tests ────────────────────────────────────────────────────────────


def test_step_timer_logs_start_and_done(caplog) -> None:
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test_step_timer")
    logger.propagate = True
    logger.setLevel(logging.INFO)

    with lc.StepTimer(logger, "test_step"):
        pass

    records = [r for r in caplog.records if r.name == "test_step_timer"]
    assert len(records) == 2
    assert records[0].msg["step"] == "test_step"
    assert records[0].msg["status"] == "started"
    assert records[1].msg["step"] == "test_step"
    assert records[1].msg["status"] == "done"
    assert "elapsed_ms" in records[1].msg


def test_step_timer_logs_error_on_exception(caplog) -> None:
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test_step_timer_err")
    logger.propagate = True
    logger.setLevel(logging.INFO)

    try:
        with lc.StepTimer(logger, "test_step_err"):
            raise ValueError("boom")
    except ValueError:
        pass

    records = [r for r in caplog.records if r.name == "test_step_timer_err"]
    assert len(records) == 2
    assert records[0].msg["status"] == "started"
    error_records = [r for r in records if r.msg.get("status") == "error"]
    assert len(error_records) == 1
    assert error_records[0].msg["step"] == "test_step_err"
    assert "elapsed_ms" in error_records[0].msg
