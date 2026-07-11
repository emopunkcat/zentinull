"""Tests for cli/backup.py _fmt_bytes and cli/db_mgmt.py _fmt_size."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# _fmt_bytes (from cli/backup.py)
# ---------------------------------------------------------------------------


def test_fmt_bytes_bytes():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(500) == "500.0 B"


def test_fmt_bytes_kb():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(2048) == "2.0 KB"


def test_fmt_bytes_mb():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(5_242_880) == "5.0 MB"


def test_fmt_bytes_gb():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(1_073_741_824) == "1.0 GB"


def test_fmt_bytes_tb():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(2_199_023_255_552) == "2.0 TB"


def test_fmt_bytes_zero():
    from zentinull.cli.backup import _fmt_bytes

    assert _fmt_bytes(0) == "0.0 B"


# ---------------------------------------------------------------------------
# _fmt_size (from cli/db_mgmt.py)
# ---------------------------------------------------------------------------


def test_fmt_size_bytes():
    from zentinull.cli.db_mgmt import _fmt_size

    assert _fmt_size(500) == "500 B"


def test_fmt_size_kb():
    from zentinull.cli.db_mgmt import _fmt_size

    assert _fmt_size(2000) == "2.0 KB"


def test_fmt_size_mb():
    from zentinull.cli.db_mgmt import _fmt_size

    assert _fmt_size(3_145_728) == "3.0 MB"


def test_fmt_size_gb():
    from zentinull.cli.db_mgmt import _fmt_size

    assert _fmt_size(2_147_483_648) == "2.00 GB"


def test_fmt_size_zero():
    from zentinull.cli.db_mgmt import _fmt_size

    assert _fmt_size(0) == "0 B"
