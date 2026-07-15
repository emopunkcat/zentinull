"""Tests for export-time normalizer: strip_sentinels and normalize_serial."""

from __future__ import annotations


class TestStripSentinels:
    def test_none_returns_empty(self) -> None:
        """Given None, strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Given empty string, strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("") == ""

    def test_null_literal_returns_empty(self) -> None:
        """Given "null", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("null") == ""

    def test_none_capitalized_returns_empty(self) -> None:
        """Given "None", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("None") == ""

    def test_na_returns_empty(self) -> None:
        """Given "n/a", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("n/a") == ""

    def test_double_dash_returns_empty(self) -> None:
        """Given "--", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("--") == ""

    def test_single_dash_returns_empty(self) -> None:
        """Given "-", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("-") == ""

    def test_valid_string_passes_through(self) -> None:
        """Given a real value, strip_sentinels returns the stripped string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("ABC123") == "ABC123"

    def test_whitespace_only_returns_empty(self) -> None:
        """Given whitespace, strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("   ") == ""

    def test_na_uppercase_returns_empty(self) -> None:
        """Given "N/A", strip_sentinels returns empty string."""
        from zentinull.normalizer import strip_sentinels

        assert strip_sentinels("N/A") == ""


class TestNormalizeSerial:
    def test_none_returns_empty(self) -> None:
        """Given None, normalize_serial returns empty string."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial(None) == ""

    def test_empty_returns_empty(self) -> None:
        """Given empty string, normalize_serial returns empty string."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("") == ""

    def test_me_fake_serial_returns_empty(self) -> None:
        """Given a ManageEngine fake serial (16 groups of digits), returns empty."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("0000-1111-2222-3333-4444-5555-66") == ""

    def test_sn_prefix_stripped(self) -> None:
        """Given "SN-12345", strips the prefix and returns "12345"."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("SN-12345") == "12345"

    def test_sn_colon_prefix_stripped(self) -> None:
        """Given "SN:12345", strips the prefix and returns "12345"."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("SN:12345") == "12345"

    def test_slash_n_prefix_stripped(self) -> None:
        """Given "S/N:12345", strips the prefix and returns "12345"."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("S/N:12345") == "12345"

    def test_pc_prefix_stripped(self) -> None:
        """Given "PC-ABC123", strips the prefix and returns "ABC123"."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("PC-ABC123") == "ABC123"

    def test_cnd_prefix_stripped(self) -> None:
        """Given "CND-XYZ789", strips the prefix and returns "XYZ789"."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("CND-XYZ789") == "XYZ789"

    def test_no_prefix_passes_through(self) -> None:
        """Given serial without prefix, returns unchanged."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("ABC123XYZ") == "ABC123XYZ"

    def test_whitespace_stripped(self) -> None:
        """Given serial with surrounding whitespace, strips it."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("  ABC123  ") == "ABC123"

    def test_sn_prefix_case_insensitive(self) -> None:
        """Given "sn-12345" (lowercase), strips the prefix."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("sn-12345") == "12345"

    def test_sentinel_value_returns_empty(self) -> None:
        """Given "--" (sentinel), normalize_serial returns empty string."""
        from zentinull.normalizer import normalize_serial

        assert normalize_serial("--") == ""
