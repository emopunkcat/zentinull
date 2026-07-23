"""Unit tests for src/zentinull/worker.py."""

from __future__ import annotations

from zentinull.worker import WorkerState, _get_interval, _get_splink_interval


def test_get_interval_default():
    interval = _get_interval("fg")
    assert isinstance(interval, int)
    assert interval > 0


def test_get_interval_override(monkeypatch):
    monkeypatch.setenv("ZENTINULL_SCHED_FG", "1234")
    assert _get_interval("fg") == 1234


def test_get_splink_interval_override(monkeypatch):
    monkeypatch.setenv("ZENTINULL_SCHED_SPLINK", "9999")
    assert _get_splink_interval() == 9999


def test_worker_state():
    state = WorkerState()
    assert state.running is False
    assert state.should_stop is False

    # Initially last_run is 0.0, so should_run evaluates to True
    assert state.should_run("fg") is True
    assert state.should_run_splink() is True
