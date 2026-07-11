"""Tests for export_for_splink — CSV export from SQLite source DBs."""

from __future__ import annotations

import csv
import sqlite3


def _setup_me_db(data_dir, /, *, resource_id="res_001", name="WS28.domain.com", mac_address="AA:BB:CC:DD:EE:FF",
                 serial_number="SN001", manufacturer="Dell", model="OptiPlex", os_name="Windows 10",
                 os_version="22H2", assigned_user="jdoe", last_seen="2026-01-01", ip_address="192.168.1.100",
                 row_id=1):
    """Create a minimal me.sqlite with a computers table and one row."""
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.execute("""
        CREATE TABLE computers (
            id INTEGER, resource_id TEXT, serial_number TEXT, mac_address TEXT,
            name TEXT, manufacturer TEXT, model TEXT, os_name TEXT, os_version TEXT,
            assigned_user TEXT, last_seen TEXT, ip_address TEXT
        )
    """)
    conn.execute(
        "INSERT INTO computers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row_id, resource_id, serial_number, mac_address, name, manufacturer, model,
         os_name, os_version, assigned_user, last_seen, ip_address),
    )
    conn.commit()
    conn.close()


def test_export_creates_csv(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir)

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    csv_path = out_dir / "devices.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) >= 1
    assert "source" in rows[0]
    assert rows[0]["source"] == "me_ec"


def test_export_name_clean_normalized(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir, name="WS28.domain.com")

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    with open(out_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["name"] == "WS28.domain.com"
    assert row["name_clean"] == "ws28"


def test_export_mac_clean_normalized(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir, mac_address="AA:BB:CC:DD:EE:FF")

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    with open(out_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert row["mac_clean"] == "aabbccddeeff"


def test_export_missing_db_skipped(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    # Create me.sqlite so me_ec provides rows (avoids ZeroDivisionError in coverage stats).
    # sp, fg, zbx, ad, sdp DBs are missing — they should be skipped.
    _setup_me_db(data_dir)

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()  # Must not raise — missing DBs skip, me_ec succeeds

    csv_path = out_dir / "devices.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1  # Only me_ec row; sp/fg/zbx/ad/sdp DBs missing → skipped


def test_export_missing_table_skipped(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    # Create me.sqlite empty (no tables) → me_ec and me_mdm skipped due to missing tables.
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.close()
    # Also create fg.sqlite with a "clients" table so fg provides ≥1 row
    # (avoids ZeroDivisionError in coverage stats).
    conn = sqlite3.connect(str(data_dir / "fg.sqlite"))
    conn.execute("""
        CREATE TABLE clients (
            mac TEXT, ip TEXT, hostname TEXT, user_name TEXT,
            os TEXT, manufacturer TEXT, model TEXT
        )
    """)
    conn.execute(
        "INSERT INTO clients VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("aa:bb:cc:dd:ee:ff", "10.0.0.1", "fw01", "admin", "FortiOS", "Fortinet", "FortiGate"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()  # Must not raise — me_ec/mdm skip (table missing), fg succeeds

    csv_path = out_dir / "devices.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1  # Only fg row; me_ec/mdm tables missing → skipped


def test_export_field_mapping(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir, resource_id="res_001", serial_number="SN001", manufacturer="Dell",
                 model="OptiPlex", os_name="Windows 10", os_version="22H2", assigned_user="jdoe",
                 ip_address="192.168.1.100")

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    with open(out_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    # Verify field-level mappings from me_ec FIELD_MAP
    assert row["source_id"] == "res_001"      # resource_id → source_id
    assert row["serial_number"] == "SN001"     # serial_number → serial_number
    assert row["manufacturer"] == "Dell"       # manufacturer → manufacturer
    assert row["model"] == "OptiPlex"          # model → model
    assert row["os"] == "Windows 10"           # os_name → os
    assert row["os_version"] == "22H2"         # os_version → os_version
    assert row["assigned_user"] == "jdoe"      # assigned_user → assigned_user
    assert row["ip_address"] == "192.168.1.100"  # ip_address → ip_address


def test_export_source_id_fallback(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    # resource_id is NULL → source_id falls back to row "id" column
    _setup_me_db(data_dir, resource_id=None, row_id=42)

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    with open(out_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["source_id"] == "42"


def test_export_mac_clean_too_short(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir, mac_address="AA:BB:CC")

    monkeypatch.setattr(export_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(export_mod, "OUT_DIR", out_dir)

    from zentinull.export_for_splink import export
    export()

    with open(out_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["mac_clean"] == ""
