"""Tests for _safe() and _norm_mac() pure functions from api.db."""

from __future__ import annotations

# ── _safe ──────────────────────────────────────────────────────────────


def test_safe_returns_string():
    from zentinull.api.db import _safe

    assert _safe("hello") == "hello"


def test_safe_none_returns_default():
    from zentinull.api.db import _safe

    assert _safe(None) == ""


def test_safe_none_custom_default():
    from zentinull.api.db import _safe

    assert _safe(None, default="N/A") == "N/A"


def test_safe_empty_string():
    from zentinull.api.db import _safe

    assert _safe("") == ""


def test_safe_nan_string():
    from zentinull.api.db import _safe

    assert _safe("nan") == ""


def test_safe_nan_uppercase():
    from zentinull.api.db import _safe

    assert _safe("NaN") == ""
    assert _safe("NAN") == ""


def test_safe_whitespace_trim():
    from zentinull.api.db import _safe

    assert _safe("  hello  ") == "hello"


def test_safe_int_value():
    from zentinull.api.db import _safe

    assert _safe(0) == "0"


def test_safe_stripped_to_empty():
    """Whitespace-only input stripped to empty string returns default."""
    from zentinull.api.db import _safe

    assert _safe("   ") == ""


# ── _norm_mac ──────────────────────────────────────────────────────────


def test_norm_mac_valid_colons():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("aa:bb:cc:dd:ee:ff") == "aabbccddeeff"


def test_norm_mac_mixed_case():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("AA:BB:CC:DD:EE:FF") == "aabbccddeeff"


def test_norm_mac_dashes():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("AA-BB-CC-DD-EE-FF") == "aabbccddeeff"


def test_norm_mac_too_short():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("aa:bb:cc") == ""


def test_norm_mac_too_long():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("aa:bb:cc:dd:ee:ff:00") == ""


def test_norm_mac_empty():
    from zentinull.api.db import _norm_mac

    assert _norm_mac("") == ""


def test_norm_mac_special_chars_removed():
    """Non-hex chars like 'W' and 'i' in '(WiFi)' are stripped, leaving 13 hex chars → too long."""
    from zentinull.api.db import _norm_mac

    assert _norm_mac("aa:bb:cc:dd:ee:ff (WiFi)") == ""
