"""Phase 6 acceptance gate — Audit-mapping classifier and CLI tests.

Verifies:
- classify_value correctly identifies MAC, IP, IMEI, email, serial, and null sentinels
- classify_key_value returns confidence tuples
- cmd_audit_mapping --propose outputs FieldSpec suggestions for unmapped keys
- cmd_audit_mapping --strict exits non-zero on drift
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from zentinull.resolve.classifier import classify_key_value, classify_value


def _make_paths(tmp_path: Path):
    """Build a ProjectPaths pointing at tmp_path for test isolation."""
    from zentinull.config import ProjectPaths

    data_dir = tmp_path / "data"
    export_dir = tmp_path / "export"
    data_dir.mkdir(parents=True, exist_ok=True)
    return ProjectPaths(
        project="test",
        data_dir=data_dir,
        export_dir=export_dir,
        mesh_path=data_dir / "mesh.duckdb",
        status_file=data_dir / "status.json",
        log_file=data_dir / "pipeline.log",
        csv_dir=export_dir / "csv",
        splink_output_dir=export_dir / "splink_output",
        benchmarks_dir=tmp_path / ".benchmarks",
    )


# ── classify_value tests ──────────────────────────────────────────────────────


def test_classify_value_mac() -> None:
    """MAC address with colons -> 'mac_address'."""
    assert classify_value("AA:BB:CC:DD:EE:FF") == "mac_address"


def test_classify_value_ip() -> None:
    """IPv4 address -> 'ip_address'."""
    assert classify_value("192.168.1.100") == "ip_address"


def test_classify_value_imei() -> None:
    """15-digit IMEI -> 'imei'."""
    assert classify_value("354666655016848") == "imei"


def test_classify_value_email() -> None:
    """Email address -> 'email'."""
    assert classify_value("user@domain.com") == "email"


def test_classify_value_serial() -> None:
    """Alphanumeric serial with letters+digits, 6-20 chars -> 'serial_number'."""
    assert classify_value("SN001234") == "serial_number"


def test_classify_value_empty() -> None:
    """Empty string -> None."""
    assert classify_value("") is None


def test_classify_value_null_sentinel() -> None:
    """N/A (in NULL_SENTINELS) -> None."""
    assert classify_value("N/A") is None


# ── classify_key_value tests ──────────────────────────────────────────────────


def test_classify_key_value_returns_confidence() -> None:
    """classify_key_value returns [(target_field, type_name, 1.0)] for MAC."""
    result = classify_key_value("AA:BB:CC:DD:EE:FF")
    assert result == [("mac_address", "mac_address", 1.0)]


def test_classify_key_value_no_match() -> None:
    """classify_key_value returns [] for unclassifiable values."""
    assert classify_key_value("random text") == []


# ── cmd_audit_mapping --propose tests ─────────────────────────────────────────


def _make_args(**kwargs: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace with the given attributes."""
    return argparse.Namespace(**kwargs)


def _create_sqlite_with_raw(data_dir: Path, db_name: str, table_name: str, raw_json_records: list[dict]) -> None:
    """Create a SQLite DB with a raw store table and insert raw_json records."""
    db_path = data_dir / db_name
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY,
            source_id TEXT,
            raw_json TEXT,
            raw_hash TEXT,
            remote_updated_at TEXT,
            fetched_at TEXT
        )
        """
    )
    for i, raw in enumerate(raw_json_records):
        conn.execute(
            f"INSERT INTO {table_name} (id, source_id, raw_json, raw_hash) VALUES (?, ?, ?, ?)",
            (i + 1, f"src_{i + 1}", json.dumps(raw), f"hash_{i + 1}"),
        )
    conn.commit()
    conn.close()


def test_audit_mapping_propose_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--propose outputs FieldSpec suggestions for unmapped MAC-shaped keys."""
    from serve import cmd_audit_mapping

    # Set up a temp data dir with a SQLite DB containing an unmapped MAC-shaped key
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _create_sqlite_with_raw(
        data_dir,
        "sdp.sqlite",
        "requests",
        [{"id": "1", "description": "fix laptop", "device_mac": "AA:BB:CC:DD:EE:FF"}],
    )

    # Monkeypatch DATA_DIR in serve's import scope (cmd_audit_mapping imports it locally)
    _paths = _make_paths(tmp_path)
    with patch("zentinull.config.PATHS", _paths):
        args = _make_args(propose="sdp_requests", strict=False)
        with contextlib.suppress(SystemExit):
            cmd_audit_mapping(args)

    captured = capsys.readouterr()
    assert "FieldSpec" in captured.out, f"Expected 'FieldSpec' in output: {captured.out}"
    assert "device_mac" in captured.out, f"Expected raw key name in output: {captured.out}"


def test_audit_mapping_strict_exits_nonzero_on_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--strict exits with code 1 when a high-fill-rate unmapped MAC-shaped key is found."""
    from serve import cmd_audit_mapping

    # Set up a temp data dir with drift: a MAC-shaped key at high fill rate
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    records = [{"id": str(i), "device_mac": "AA:BB:CC:DD:EE:FF"} for i in range(10)]
    _create_sqlite_with_raw(data_dir, "sdp.sqlite", "requests", records)

    _paths = _make_paths(tmp_path)
    with patch("zentinull.config.PATHS", _paths):
        args = _make_args(propose=None, strict=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_audit_mapping(args)

    assert exc_info.value.code == 1, f"Expected exit code 1, got {exc_info.value.code}"
    captured = capsys.readouterr()
    assert "DRIFT:" in captured.out, f"Expected 'DRIFT:' in output: {captured.out}"
