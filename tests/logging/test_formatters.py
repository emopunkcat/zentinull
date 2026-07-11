"""Tests for logging_config formatters — _fmt_val, StructuredFormatter, JsonFormatter."""

from __future__ import annotations

import json
import logging


def _make_record(
    msg: object,
    level: int = logging.INFO,
    name: str = "test",
    exc_info: object = None,
) -> logging.LogRecord:
    return logging.LogRecord(name, level, "", 0, msg, (), exc_info)


# ── _fmt_val tests ────────────────────────────────────────────────────────────


def test_fmt_val_none():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(None) == "null"


def test_fmt_val_int():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(42) == "42"


def test_fmt_val_float():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(3.14) == "3.14"


def test_fmt_val_bool_true():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(True) == "True"


def test_fmt_val_bool_false():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(False) == "False"


def test_fmt_val_string():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val("hello") == "hello"


def test_fmt_val_string_with_spaces():
    from zentinull.logging_config import _fmt_val

    result = _fmt_val("hello world")
    assert result == json.dumps("hello world")


def test_fmt_val_string_with_equals():
    from zentinull.logging_config import _fmt_val

    result = _fmt_val("a=b")
    assert result == json.dumps("a=b")


def test_fmt_val_empty_string():
    from zentinull.logging_config import _fmt_val

    result = _fmt_val("")
    assert result == json.dumps("")


# ── StructuredFormatter tests ─────────────────────────────────────────────────


def test_structured_fmt_dict_msg():
    from zentinull.logging_config import StructuredFormatter

    fmt = StructuredFormatter()
    record = _make_record({"event": "test", "rows": 5})
    output = fmt.format(record)
    assert "event=test" in output
    assert "rows=5" in output


def test_structured_fmt_string_msg():
    from zentinull.logging_config import StructuredFormatter

    fmt = StructuredFormatter()
    record = _make_record("hello")
    output = fmt.format(record)
    assert "hello" in output


def test_structured_fmt_with_exception():
    from zentinull.logging_config import StructuredFormatter

    fmt = StructuredFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=logging.sys.exc_info())
        output = fmt.format(record)
    assert "ValueError" in output
    assert "boom" in output


# ── JsonFormatter tests ───────────────────────────────────────────────────────


def test_json_fmt_dict_msg():
    from zentinull.logging_config import JsonFormatter

    fmt = JsonFormatter()
    record = _make_record({"event": "test", "rows": 5})
    output = fmt.format(record)
    obj = json.loads(output)
    assert obj["event"] == "test"
    assert obj["rows"] == 5
    assert "ts" in obj
    assert obj["logger"] == "test"
    assert obj["level"] == "INFO"


def test_json_fmt_string_msg():
    from zentinull.logging_config import JsonFormatter

    fmt = JsonFormatter()
    record = _make_record("hello")
    output = fmt.format(record)
    obj = json.loads(output)
    assert obj["msg"] == "hello"
    assert "ts" in obj
    assert obj["logger"] == "test"
    assert obj["level"] == "INFO"


def test_json_fmt_with_exception():
    from zentinull.logging_config import JsonFormatter

    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=logging.sys.exc_info())
        output = fmt.format(record)
    obj = json.loads(output)
    assert "error" in obj
    assert "boom" in obj["error"]


def test_json_fmt_parseable():
    from zentinull.logging_config import JsonFormatter

    fmt = JsonFormatter()
    record = _make_record("hello")
    output = fmt.format(record)
    obj = json.loads(output)
    assert isinstance(obj, dict)
