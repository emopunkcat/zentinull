"""Tests for cli/status.py formatting helpers: _fmt_ts, _fmt_duration, _fmt_stat."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# _fmt_ts
# ---------------------------------------------------------------------------


def test_fmt_ts_valid_iso():
    from zentinull.cli.status import _fmt_ts

    result = _fmt_ts("2026-01-15T10:30:00")
    assert result == "2026-01-15 10:30:00"


def test_fmt_ts_invalid():
    from zentinull.cli.status import _fmt_ts

    result = _fmt_ts("not-a-date")
    assert result == "not-a-date"


def test_fmt_ts_none():
    from zentinull.cli.status import _fmt_ts

    result = _fmt_ts(None)
    assert result == "None"


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


def test_fmt_duration_zero():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(0) == "\u2014"


def test_fmt_duration_negative():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(-100) == "\u2014"


def test_fmt_duration_milliseconds():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(500) == "500ms"


def test_fmt_duration_subsecond():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(999) == "999ms"


def test_fmt_duration_exactly_one_second():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(1000) == "1.0s"


def test_fmt_duration_seconds():
    from zentinull.cli.status import _fmt_duration

    assert _fmt_duration(2500) == "2.5s"


# ---------------------------------------------------------------------------
# _fmt_stat
# ---------------------------------------------------------------------------


def test_fmt_stat_scalar():
    from zentinull.cli.status import _fmt_stat

    result = _fmt_stat("rows", 42)
    assert result == "rows:42"


def test_fmt_stat_dict():
    from zentinull.cli.status import _fmt_stat

    result = _fmt_stat("sources", {"sp": 1, "me": 2})
    assert result == "sp:1 me:2"


def test_fmt_stat_list():
    from zentinull.cli.status import _fmt_stat

    result = _fmt_stat("tags", ["a", "b"])
    assert result == "a, b"
