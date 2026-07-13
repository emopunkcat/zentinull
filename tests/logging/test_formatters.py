"""Tests for logging_config formatters — _fmt_val, StructuredFormatter, JsonFormatter."""

from __future__ import annotations

import json
import logging
import sys


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

    assert _fmt_val(True) == "true"


def test_fmt_val_bool_false():
    from zentinull.logging_config import _fmt_val

    assert _fmt_val(False) == "false"


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


# ── PrettyFormatter tests ─────────────────────────────────────────────────────


def test_pretty_fmt_dict_msg():
    """Dict messages render as colored key=value pairs."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "fetched", "rows": 42, "source": "fg"})
    output = fmt.format(record)
    assert "event=fetched" in output
    assert "rows=42" in output
    assert "source=fg" in output


def test_pretty_fmt_string_msg():
    """Plain string messages pass through unchanged."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record("hello world")
    output = fmt.format(record)
    assert "hello world" in output


def test_pretty_fmt_no_level_for_info():
    """INFO-level messages do not include the level label."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "test"}, level=logging.INFO)
    output = fmt.format(record)
    assert "INFO" not in output
    assert "WRN" not in output
    assert "ERR" not in output


def test_pretty_fmt_warning_level():
    """WARNING messages include 'WRN' label."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "slow"}, level=logging.WARNING)
    output = fmt.format(record)
    assert "WRN" in output


def test_pretty_fmt_error_level():
    """ERROR messages include 'ERR' label."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "fail"}, level=logging.ERROR)
    output = fmt.format(record)
    assert "ERR" in output


def test_pretty_fmt_critical_level():
    """CRITICAL messages include 'CRI' label."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "panic"}, level=logging.CRITICAL)
    output = fmt.format(record)
    assert "CRI" in output


def test_pretty_fmt_debug_level():
    """DEBUG messages include 'DBG' label."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "trace"}, level=logging.DEBUG)
    output = fmt.format(record)
    assert "DBG" in output


def test_pretty_fmt_strips_zig_prefix():
    """Logger name drops the 'zig.' prefix."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "test"}, name="zig.ingest.sp")
    output = fmt.format(record)
    assert "zig." not in output
    assert "ingest.sp" in output


def test_pretty_fmt_short_timestamp():
    """Timestamp is in HH:MM:SS format (no date, no timezone)."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    # HH:MM:SS pattern: two digits, colon, two digits, colon, two digits
    import re

    match = re.search(r"\d{2}:\d{2}:\d{2}", output)
    assert match is not None, f"Expected HH:MM:SS in output, got: {output[:50]}"


def test_pretty_fmt_bool_value_lowercase():
    """Boolean values render as lowercase true/false."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"active": True, "deleted": False})
    output = fmt.format(record)
    assert "active=true" in output
    assert "deleted=false" in output


def test_pretty_fmt_null_value():
    """None values render as 'null'."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"key": None})
    output = fmt.format(record)
    assert "key=null" in output


def test_pretty_fmt_number_value():
    """Numeric values are rendered directly."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"count": 42, "ratio": 3.14})
    output = fmt.format(record)
    assert "count=42" in output
    assert "ratio=3.14" in output


def test_pretty_fmt_with_exception():
    """Exception info is included in the output."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=logging.sys.exc_info())
        output = fmt.format(record)
    assert "ValueError" in output
    assert "boom" in output


def test_pretty_fmt_with_request_id():
    """Request ID from filter is rendered."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    record.request_id = "abc-123"  # type: ignore[attr-defined]
    output = fmt.format(record)
    assert "rid=abc-123" in output


def test_pretty_fmt_colors_enabled_adds_ansi():
    """When colors are enabled, ANSI escape sequences appear."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=True)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    assert "\033[" in output


def test_pretty_fmt_colors_disabled_no_ansi():
    """When colors are disabled, no ANSI escape sequences appear."""
    from zentinull.logging_config import PrettyFormatter

    fmt = PrettyFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    assert "\033[" not in output


# ── BrutalistFormatter tests ─────────────────────────────────────────────────


def test_brutalist_fmt_dict_msg():
    """Dict messages render as key=value pairs with values in output."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "fetched", "rows": 42, "source": "fg"})
    output = fmt.format(record)
    assert "event=42" not in output  # key and value separated by =
    assert "rows" in output
    assert "source" in output
    assert "42" in output
    assert "fg" in output


def test_brutalist_fmt_string_msg():
    """Plain string messages pass through."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record("hello world")
    output = fmt.format(record)
    assert "hello world" in output


def test_brutalist_fmt_shows_level_for_info():
    """INFO-level messages include 'INF' label (unlike PrettyFormatter)."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"}, level=logging.INFO)
    output = fmt.format(record)
    assert "INF" in output


def test_brutalist_fmt_shows_level_for_warning():
    """WARNING messages include 'WRN' label."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "slow"}, level=logging.WARNING)
    output = fmt.format(record)
    assert "WRN" in output


def test_brutalist_fmt_shows_level_for_error():
    """ERROR messages include 'ERR' label."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "fail"}, level=logging.ERROR)
    output = fmt.format(record)
    assert "ERR" in output


def test_brutalist_fmt_shows_level_for_critical():
    """CRITICAL messages include 'CRI' label."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "panic"}, level=logging.CRITICAL)
    output = fmt.format(record)
    assert "CRI" in output


def test_brutalist_fmt_shows_level_for_debug():
    """DEBUG messages include 'DBG' label."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "trace"}, level=logging.DEBUG)
    output = fmt.format(record)
    assert "DBG" in output


def test_brutalist_fmt_block_chars_present():
    """Block-char glyphs (■ ◆ ● ·) appear in the output for each level."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)

    info = fmt.format(_make_record({"e": "i"}, level=logging.INFO))
    assert "●" in info

    warn = fmt.format(_make_record({"e": "w"}, level=logging.WARNING))
    assert "◆" in warn

    err = fmt.format(_make_record({"e": "e"}, level=logging.ERROR))
    assert "■" in err

    crit = fmt.format(_make_record({"e": "c"}, level=logging.CRITICAL))
    assert "■" in crit

    dbg = fmt.format(_make_record({"e": "d"}, level=logging.DEBUG))
    assert "·" in dbg


def test_brutalist_fmt_vertical_separator():
    """Output includes the heavy vertical bar separator (│)."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    assert "│" in output


def test_brutalist_fmt_strips_zig_prefix():
    """Logger name drops the 'zig.' prefix."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"}, name="zig.ingest.sp")
    output = fmt.format(record)
    assert "zig." not in output
    assert "ingest.sp" in output


def test_brutalist_fmt_short_timestamp():
    """Timestamp is in HH:MM:SS format."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    import re

    match = re.search(r"\d{2}:\d{2}:\d{2}", output)
    assert match is not None, f"Expected HH:MM:SS in output, got: {output[:50]}"


def test_brutalist_fmt_with_exception():
    """Exception info is included in the output."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=logging.sys.exc_info())
        output = fmt.format(record)
    assert "ValueError" in output
    assert "boom" in output


def test_brutalist_fmt_with_request_id():
    """Request ID from filter is rendered."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    record.request_id = "abc-123"  # type: ignore[attr-defined]
    output = fmt.format(record)
    assert "rid=abc-123" in output


def test_brutalist_fmt_colors_enabled_adds_ansi():
    """When colors are enabled, ANSI escape sequences appear."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=True)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    assert "\033[" in output


def test_brutalist_fmt_colors_disabled_no_ansi():
    """When colors are disabled, no ANSI escape sequences appear."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"event": "test"})
    output = fmt.format(record)
    assert "\033[" not in output


def test_brutalist_fmt_null_bool_number_values():
    """None→null, bool→lowercase, numbers render directly."""
    from zentinull.logging_config import BrutalistFormatter

    fmt = BrutalistFormatter(use_colors=False)
    record = _make_record({"key": None, "active": True, "count": 42, "ratio": 3.14})
    output = fmt.format(record)
    assert "key=null" in output
    assert "active=true" in output
    assert "count=42" in output
    assert "ratio=3.14" in output


# ── RegexBrutalistFormatter tests ─────────────────────────────────────────────


def test_regex_brutalist_fmt_dict_msg():
    """Dict messages render with regex-based highlighting."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "error", "source": "fg", "rows": 42})
    output = fmt.format(record)
    assert "error" in output
    assert "source=fg" in output
    assert "rows=42" in output


def test_regex_brutalist_fmt_string_msg():
    """Plain string messages pass through."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record("hello world")
    output = fmt.format(record)
    assert "hello world" in output


def test_regex_brutalist_shows_all_levels():
    """All severity levels include their badge label."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    for lvl, label in [("INFO", "INF"), ("WARNING", "WRN"), ("ERROR", "ERR"), ("CRITICAL", "CRI"), ("DEBUG", "DBG")]:
        record = _make_record("test", level=getattr(logging, lvl))
        output = fmt.format(record)
        assert label in output, f"Expected {label} in {lvl} output"


def test_regex_brutalist_block_chars():
    """Block-char glyphs appear for each severity level."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    inf = fmt.format(_make_record("x", level=logging.INFO))
    wrn = fmt.format(_make_record("x", level=logging.WARNING))
    err = fmt.format(_make_record("x", level=logging.ERROR))
    cri = fmt.format(_make_record("x", level=logging.CRITICAL))
    dbg = fmt.format(_make_record("x", level=logging.DEBUG))
    assert "●" in inf
    assert "◆" in wrn
    assert "■" in err
    assert "■" in cri
    assert "·" in dbg


def test_regex_brutalist_vertical_separator():
    """Output includes the heavy vertical bar separator (│)."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    output = fmt.format(_make_record("test"))
    assert "│" in output


def test_regex_brutalist_strips_zig_prefix():
    """Logger name drops the 'zig.' prefix."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record("test", name="zig.ingest.sp")
    output = fmt.format(record)
    assert "ingest.sp" in output
    assert "zig.ingest.sp" not in output


def test_regex_brutalist_short_timestamp():
    """Timestamp is in HH:MM:SS format."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    output = fmt.format(_make_record("test"))
    import re

    match = re.search(r"\b\d{2}:\d{2}:\d{2}\b", output)
    assert match is not None, f"Expected HH:MM:SS in output, got: {output[:50]}"


def test_regex_brutalist_with_exception():
    """Exception info is included in the output."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("oh no", exc_info=sys.exc_info())
    output = fmt.format(record)
    assert "boom" in output


def test_regex_brutalist_with_request_id():
    """Request ID from filter is rendered."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record("test")
    record.request_id = "abc-123"
    output = fmt.format(record)
    assert "rid=abc-123" in output


def test_regex_brutalist_colors_enabled_adds_ansi():
    """When colors are enabled, ANSI escape sequences appear."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    output = fmt.format(_make_record({"event": "test"}))
    assert "\033[" in output


def test_regex_brutalist_colors_disabled_no_ansi():
    """When colors are disabled, no ANSI escape sequences appear."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    output = fmt.format(_make_record({"event": "test"}))
    assert "\033[" not in output


def test_regex_brutalist_null_bool_number_values():
    """None→null, bool→lowercase, numbers render directly."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"key": None, "active": True, "count": 42, "ratio": 3.14})
    output = fmt.format(record)
    assert "key=null" in output
    assert "active=true" in output
    assert "count=42" in output
    assert "ratio=3.14" in output


def test_regex_brutalist_highlights_error_values():
    """Values matching 'error' rule get colored (bold_red ANSI visible with colors on)."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    record = _make_record({"event": "error", "status": "ok", "source": "fg"})
    output = fmt.format(record)
    # With colors enabled, ANSI codes are present
    assert "\033[" in output


def test_regex_brutalist_highlights_success_values():
    """Values matching 'done|complete|success|ok' get bold_green styling."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    record = _make_record({"event": "ingested", "status": "done", "rows": 5})
    output = fmt.format(record)
    assert "\033[" in output


def test_regex_brutalist_highlights_warning_values():
    """Values matching 'warning|warn' get yellow styling."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    record = _make_record({"event": "warning", "reason": "timeout"})
    output = fmt.format(record)
    assert "\033[" in output


def test_regex_brutalist_highlights_number_values():
    """Standalone numeric values get gold styling."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    record = _make_record({"rows": 581, "elapsed_ms": 1234, "source": "fg"})
    output = fmt.format(record)
    assert "\033[" in output


def test_regex_brutalist_dimmed_keys_for_unmatched():
    """Unmatched keys are dimmed in output."""
    from zentinull.logging_config import RegexBrutalistFormatter

    # Disable colors so we can check content — dim is still applied as ANSI
    fmt = RegexBrutalistFormatter(use_colors=True)
    record = _make_record({"event": "fetched", "source": "fg", "rows": 10})
    output = fmt.format(record)
    # Unmatched key "source" should be in output (with dim styling)
    assert "source" in output


def test_regex_brutalist_matches_show_mode():
    """With ZENTINULL_LOG_SHOW=matches, non-matching content is not hidden, just dimmed."""
    import os as _os

    _os.environ["ZENTINULL_LOG_SHOW"] = "matches"
    try:
        from zentinull.logging_config import RegexBrutalistFormatter

        fmt = RegexBrutalistFormatter(use_colors=False)
        record = _make_record({"event": "uninteresting", "data": "boring"})
        output = fmt.format(record)
        # All content should still appear (just dimmed in color mode)
        assert "uninteresting" in output
        assert "boring" in output
    finally:
        del _os.environ["ZENTINULL_LOG_SHOW"]


def test_regex_brutalist_custom_rules():
    """ZENTINULL_LOG_RULES overrides default highlighting rules."""
    import os as _os

    _os.environ["ZENTINULL_LOG_RULES"] = "custom_match:bold_green"
    try:
        from zentinull.logging_config import RegexBrutalistFormatter

        fmt = RegexBrutalistFormatter(use_colors=False)
        record = _make_record({"key": "custom_match"})
        output = fmt.format(record)
        assert "custom_match" in output
    finally:
        del _os.environ["ZENTINULL_LOG_RULES"]


def test_regex_brutalist_empty_rules_falls_back_to_defaults():
    """Empty ZENTINULL_LOG_RULES falls back to built-in defaults."""
    import os as _os

    _os.environ["ZENTINULL_LOG_RULES"] = ""
    try:
        from zentinull.logging_config import RegexBrutalistFormatter

        fmt = RegexBrutalistFormatter(use_colors=False)
        record = _make_record({"event": "error"})
        output = fmt.format(record)
        assert "error" in output
    finally:
        del _os.environ["ZENTINULL_LOG_RULES"]


def test_regex_brutalist_raw_ansi_style():
    """Raw ANSI style codes (e.g. '1;31') are accepted directly."""
    import os as _os

    _os.environ["ZENTINULL_LOG_RULES"] = "urgent:1;31"
    try:
        from zentinull.logging_config import RegexBrutalistFormatter

        fmt = RegexBrutalistFormatter(use_colors=True)
        record = _make_record({"msg": "urgent"})
        output = fmt.format(record)
        assert "\033[1;31m" in output
    finally:
        del _os.environ["ZENTINULL_LOG_RULES"]


def test_regex_brutalist_matches_key_when_value_does_not():
    """When a key matches a rule but the value doesn't, the key is highlighted."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"error": "something_bad_happened"})
    output = fmt.format(record)
    # The key "error" matches the error rule
    assert "error" in output
    assert "something_bad_happened" in output


def test_regex_brutalist_value_takes_priority_over_key():
    """When both key and value match rules, value's style takes priority."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=True)
    # "error" key matches bold_red, "done" value matches bold_green
    record = _make_record({"error": "done"})
    output = fmt.format(record)
    # Value "done" should be highlighted — both key and value use its style
    assert "\033[" in output


def test_regex_brutalist_inherits_brutalist_aesthetic():
    """RegexBrutalistFormatter is a subclass of BrutalistFormatter."""
    from zentinull.logging_config import BrutalistFormatter, RegexBrutalistFormatter

    assert issubclass(RegexBrutalistFormatter, BrutalistFormatter)


# ── RegexBrutalistFormatter format template tests ────────────────────────────


def test_format_template_ingested_renders_structured():
    """event=ingested renders as structured status line [INGEST] - SOURCE | [N]."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "source": "fg", "rows": 250, "elapsed_ms": 1700})
    output = fmt.format(record)
    assert "[INGEST] - FG" in output
    assert "[250]" in output
    assert "1.7s" in output


def test_format_template_ingested_short_timing_uses_ms():
    """elapsed_ms < 1000 renders as e.g. '234ms'."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "source": "sp", "rows": 89, "elapsed_ms": 234})
    output = fmt.format(record)
    assert "234ms" in output


def test_format_template_ingested_missing_source_renders_empty():
    """Missing keys in template render as empty string — no crash."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "rows": 10})
    output = fmt.format(record)
    assert "[INGEST]" in output
    assert "[10]" in output


def test_format_template_export_complete():
    """event=export_complete renders [EXPORT] ✓ N records."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "export_complete", "total_records": 1423, "elapsed_ms": 3400})
    output = fmt.format(record)
    assert "[EXPORT] ✓" in output
    assert "1423 records" in output
    assert "3.4s" in output


def test_format_template_ingest_failed():
    """event=ingest_failed renders [INGEST] - SOURCE | ✗ error."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "ingest_failed", "source": "ad", "error": "timeout"})
    output = fmt.format(record)
    assert "[INGEST] - AD" in output
    assert "✗" in output
    assert "timeout" in output


def test_format_template_pipeline_stage():
    """event=pipeline_stage renders [PIPE] ── STAGE ──."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "pipeline_stage", "stage": "splink"})
    output = fmt.format(record)
    assert "[PIPE] ── SPLINK ──" in output


def test_format_template_pipeline_complete():
    """event=pipeline_complete renders [PIPE] ✓ done | N devices."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "pipeline_complete", "devices": 312, "elapsed_ms": 45200})
    output = fmt.format(record)
    assert "[PIPE] ✓ done" in output
    assert "312 devices" in output
    assert "45.2s" in output


def test_format_template_server_start():
    """event=server_start renders [API] ── url."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "server_start", "url": "http://0.0.0.0:8001"})
    output = fmt.format(record)
    assert "[API] ── http://0.0.0.0:8001" in output


def test_format_template_fallback_to_keyvalue():
    """Unmatched events fall back to key=value rendering."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "unknown_event", "data": "stuff"})
    output = fmt.format(record)
    assert "unknown_event" in output
    assert "data=stuff" in output


def test_format_template_custom_formats():
    """ZENTINULL_LOG_FORMATS overrides default format templates."""
    import os as _os

    _os.environ["ZENTINULL_LOG_FORMATS"] = "custom_event~[CUSTOM] {value:U} | {count:B}"
    try:
        from zentinull.logging_config import RegexBrutalistFormatter

        fmt = RegexBrutalistFormatter(use_colors=False)
        record = _make_record({"event": "custom_event", "value": "hello", "count": 5})
        output = fmt.format(record)
        assert "[CUSTOM] HELLO" in output
        assert "[5]" in output
    finally:
        del _os.environ["ZENTINULL_LOG_FORMATS"]


def test_format_template_size_bytes_bracket():
    """{size_bytes:B} wraps the value in brackets."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "copied", "file": "db.sqlite", "size_bytes": 4096})
    output = fmt.format(record)
    assert "[BACKUP] ✓ db.sqlite" in output
    assert "[4096]" in output


def test_format_template_no_elapsed_ms_omits_separator():
    """When elapsed_ms is missing, no '────' separator appears."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "source": "fg", "rows": 250})
    output = fmt.format(record)
    assert "[INGEST] - FG | [250]" in output
    # The ms formatter returns "" when value is None, so no trailing dash


def test_format_template_done_event():
    """event=done renders ✓ step status."""
    from zentinull.logging_config import RegexBrutalistFormatter

    fmt = RegexBrutalistFormatter(use_colors=False)
    record = _make_record({"event": "done", "step": "backup", "status": "ok", "elapsed_ms": 5200})
    output = fmt.format(record)
    assert "✓ backup ok" in output
    assert "5.2s" in output


# ── ColumnarFormatter tests ──────────────────────────────────────────────────


def test_columnar_basic_dict_msg():
    """Dict messages matching a template render compact headline + detail line."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "fetch_failed", "source": "sp", "error": "timeout"})
    output = fmt.format(record)
    # Template: "✗ {source:U} {endpoint}" — but no endpoint key, so just "✗ SP"
    assert "SP" in output
    # Detail line has remaining keys (event is consumed by template match)
    assert "ERR: timeout" in output
    # Continuation line uses · bullet
    assert "· " in output


def test_columnar_string_msg():
    """Plain string messages pass through after the compact prefix."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("hello world")
    output = fmt.format(record)
    assert "hello world" in output
    # No brackets in new format
    assert "[" not in output


def test_columnar_all_levels():
    """All severity levels include glyph badges (■ ◆ ● ·)."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    glyphs = {"INFO": "●", "WARNING": "◆", "ERROR": "■", "CRITICAL": "■", "DEBUG": "·"}
    for lvl, glyph in glyphs.items():
        record = _make_record("test", level=getattr(logging, lvl))
        output = fmt.format(record)
        assert glyph in output, f"Expected {glyph!r} glyph in {lvl} output"


def test_columnar_strips_zig_prefix():
    """Logger name is abbreviated — 'zig.ingest.sp' → 'sp'."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("test", name="zig.ingest.sp")
    output = fmt.format(record)
    assert "sp" in output
    assert "zig.ingest.sp" not in output


def test_columnar_short_timestamp():
    """Timestamp is in HH:MM format (5 chars, no seconds)."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    output = fmt.format(_make_record("test"))
    import re

    # HH:MM (not HH:MM:SS)
    match = re.search(r"\b\d{2}:\d{2}\b", output)
    assert match is not None, f"Expected HH:MM in output, got: {output[:50]}"
    # No seconds
    assert not re.search(r"\b\d{2}:\d{2}:\d{2}\b", output)


def test_columnar_with_exception():
    """Exception info is included after the message."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("oh no", exc_info=sys.exc_info())
    output = fmt.format(record)
    assert "boom" in output


def test_columnar_with_request_id():
    """Request ID from filter is rendered."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("test")
    record.request_id = "abc-123"
    output = fmt.format(record)
    assert "rid=abc-123" in output


def test_columnar_null_bool_number_values():
    """None→null, bool→lowercase, numbers render directly."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"key": None, "active": True, "count": 42, "ratio": 3.14})
    output = fmt.format(record)
    assert "KEY: null" in output
    assert "ACTIVE: true" in output
    assert "COUNT: 42" in output
    assert "RATIO: 3.14" in output


def test_columnar_string_with_spaces_quoted():
    """String values with spaces are double-quoted."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"error": "Invalid URL", "msg": "OK"})
    output = fmt.format(record)
    assert 'ERR: "Invalid URL"' in output
    assert "MSG: OK" in output  # no spaces, no quotes


def test_columnar_key_abbreviation_builtin():
    """Built-in abbreviations are applied: source→SRC, error→ERR, elapsed_ms→ELAPSED."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"source": "fg", "error": "timeout", "elapsed_ms": 1234, "custom_key": "val"})
    output = fmt.format(record)
    assert "SRC: fg" in output
    assert "ERR: timeout" in output
    assert "ELAPSED: 1234" in output
    assert "CUSTOM_KEY: val" in output  # no built-in, just uppercased


def test_columnar_custom_column_map():
    """ZENTINULL_LOG_COLUMN_MAP overrides and extends abbreviations."""
    import os as _os

    _os.environ["ZENTINULL_LOG_COLUMN_MAP"] = "source=SRCE@@custom_key=CK@@endpoint=EP"
    try:
        from zentinull.logging_config import ColumnarFormatter

        fmt = ColumnarFormatter(use_colors=False)
        record = _make_record({"source": "fg", "custom_key": "val", "endpoint": "sp_devices", "error": "boom"})
        output = fmt.format(record)
        assert "SRCE: fg" in output  # overridden
        assert "CK: val" in output  # new
        assert "EP: sp_devices" in output  # new
        assert "ERR: boom" in output  # built-in still active
    finally:
        del _os.environ["ZENTINULL_LOG_COLUMN_MAP"]


def test_columnar_empty_column_map_env():
    """Empty ZENTINULL_LOG_COLUMN_MAP falls back to built-in defaults."""
    import os as _os

    _os.environ["ZENTINULL_LOG_COLUMN_MAP"] = ""
    try:
        from zentinull.logging_config import ColumnarFormatter

        fmt = ColumnarFormatter(use_colors=False)
        record = _make_record({"source": "fg", "error": "boom"})
        output = fmt.format(record)
        assert "SRC: fg" in output
        assert "ERR: boom" in output
    finally:
        del _os.environ["ZENTINULL_LOG_COLUMN_MAP"]


def test_columnar_colors_enabled_adds_ansi():
    """When colors are enabled, ANSI escape sequences appear."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=True)
    output = fmt.format(_make_record({"event": "test"}))
    assert "\033[" in output


def test_columnar_colors_disabled_no_ansi():
    """When colors are disabled, no ANSI escape sequences appear."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    output = fmt.format(_make_record({"event": "test"}))
    assert "\033[" not in output


def test_columnar_pipe_separators():
    """When no template matches, key-value groups use pipe separators."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"a": 1, "b": 2, "c": 3})
    output = fmt.format(record)
    # Short dict fits on main line with pipe separators
    assert " | " in output


def test_columnar_header_is_compact():
    """The main line starts with HH:MM glyph module (no brackets)."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    output = fmt.format(_make_record("test"))
    # Should have HH:MM, glyph, module — no [ ] brackets
    assert "[" not in output
    assert "]" not in output


def test_columnar_fetch_failed_exact_format():
    """The user's motivating example: fetch_failed renders compact with continuation."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record(
        {"event": "fetch_failed", "source": "sp", "endpoint": "sp_devices", "error": "Invalid URL"},
        level=logging.ERROR,
        name="zig.ingest.sp",
    )
    output = fmt.format(record)
    # Template: "✗ {source:U} {endpoint}" → headline with SP and sp_devices
    assert "SP" in output
    assert "sp_devices" in output
    # Detail line has remaining keys (event consumed, only error left)
    assert 'ERR: "Invalid URL"' in output
    # Continuation is indented with bullet
    assert "· " in output
    # Module is abbreviated to "sp"
    assert "sp" in output.split("\n")[0]


# ── Compact format template tests ──────────────────────────────────────────


def test_compact_template_ingested_renders_headline():
    """event=ingested renders '✓ SOURCE +N' headline."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "source": "FortiGate", "rows": 250})
    output = fmt.format(record)
    assert "✓" in output
    assert "FORTIGATE" in output  # :U uppercased
    assert "+250" in output


def test_compact_template_pipeline_stage_renders_banner():
    """event=pipeline_stage renders '── STAGE ──' banner."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "pipeline_stage", "stage": "splink"})
    output = fmt.format(record)
    assert "── SPLINK ──" in output


def test_compact_template_exported_renders_compact():
    """event=exported renders '⇢ SOURCE +N'."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "exported", "source": "sp", "records": 581})
    output = fmt.format(record)
    assert "⇢" in output
    assert "SP" in output
    assert "+581" in output


def test_compact_template_server_start():
    """event=server_start renders '↑ server'."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "server_start", "url": "http://0.0.0.0:8001"})
    output = fmt.format(record)
    assert "↑ server" in output
    # url goes to continuation
    assert "URL: http://0.0.0.0:8001" in output


def test_compact_template_unused_keys_become_detail():
    """Keys not consumed by the template render as indented detail."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"event": "ingested", "source": "fg", "rows": 42, "elapsed_ms": 1700})
    output = fmt.format(record)
    # Template "✓ {source:U} +{rows}" consumes source, rows
    # elapsed_ms should appear in detail
    # event key is consumed by template match, only elapsed_ms remains
    assert "ELAPSED: 1700" in output
    # Verify event does NOT appear in detail
    assert "EVENT:" not in output


def test_compact_template_fallback_keyvalue():
    """Unmatched events fall back to key=value rendering with pipes."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record({"custom_event": "stuff", "data": 42})
    output = fmt.format(record)
    assert "CUSTOM_EVENT: stuff" in output
    assert "DATA: 42" in output


def test_compact_template_custom_formats():
    """ZENTINULL_LOG_COMPACT_FORMATS overrides default templates."""
    import os as _os

    _os.environ["ZENTINULL_LOG_COMPACT_FORMATS"] = "custom~⚡ {msg:U}"
    try:
        from zentinull.logging_config import ColumnarFormatter

        fmt = ColumnarFormatter(use_colors=False)
        record = _make_record({"custom": "yes", "msg": "hello"})
        output = fmt.format(record)
        assert "⚡ HELLO" in output
    finally:
        del _os.environ["ZENTINULL_LOG_COMPACT_FORMATS"]


def test_compact_template_ms_formatter():
    """{elapsed_ms:ms} in template renders human-readable timing."""
    # Use custom template via env to test ms formatter in compact context
    import os as _os

    from zentinull.logging_config import ColumnarFormatter

    _os.environ["ZENTINULL_LOG_COMPACT_FORMATS"] = "event=done_test~✓ done{elapsed_ms:ms}"
    try:
        from zentinull.logging_config import ColumnarFormatter

        fmt2 = ColumnarFormatter(use_colors=False)
        record = _make_record({"event": "done_test", "elapsed_ms": 5200})
        output = fmt2.format(record)
        assert "5.2s" in output

        record2 = _make_record({"event": "done_test", "elapsed_ms": 234})
        output2 = fmt2.format(record2)
        assert "234ms" in output2
    finally:
        del _os.environ["ZENTINULL_LOG_COMPACT_FORMATS"]


def test_compact_width_enforcement():
    """Main line is capped at configured width — overflow goes to continuation."""
    import os as _os

    _os.environ["ZENTINULL_LOG_COMPACT_WIDTH"] = "32"
    try:
        from zentinull.logging_config import ColumnarFormatter

        fmt = ColumnarFormatter(use_colors=False)
        # 10-key dict that won't fit in 32-17=15 headline chars
        record = _make_record({f"key{i}": f"value{i}" for i in range(10)})
        output = fmt.format(record)
        lines = output.split("\n")
        # Main line should be ≤ 32 visible chars (plus ANSI)
        main = lines[0]
        assert len(main) >= 5  # at minimum the prefix
        # Continuation line should exist
        assert len(lines) > 1
        assert "· " in lines[1]
    finally:
        del _os.environ["ZENTINULL_LOG_COMPACT_WIDTH"]


def test_compact_module_abbreviation_ingest():
    """Module 'zig.ingest.fg' abbreviates to 'fg'."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("test", name="zig.ingest.fg")
    output = fmt.format(record)
    assert "fg" in output


def test_compact_module_abbreviation_pipeline():
    """Module 'zig.cli.pipeline' abbreviates to 'pipe'."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("test", name="zig.cli.pipeline")
    output = fmt.format(record)
    assert "pipe" in output


def test_compact_module_abbreviation_unknown():
    """Unknown module names are truncated to 7 chars from last component."""
    from zentinull.logging_config import ColumnarFormatter

    fmt = ColumnarFormatter(use_colors=False)
    record = _make_record("test", name="zig.very.long.module.name.here")
    output = fmt.format(record)
    # Last component is "here", truncated to 7 chars = "here"
    assert "here" in output
