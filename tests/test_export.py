"""Tests for export_for_splink — CSV export from SQLite source DBs via manifest walker."""

from __future__ import annotations

import csv
import json
import sqlite3
from unittest.mock import MagicMock


def _make_paths(tmp_path):
    """Build a ProjectPaths pointing at tmp_path for test isolation."""
    from zentinull.config import ProjectPaths

    data_dir = tmp_path / "data"
    export_dir = tmp_path / "export"
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
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


def _setup_me_db(
    data_dir,
    /,
    *,
    resource_id="res_001",
    name="WS28.domain.com",
    mac_address="AA:BB:CC:DD:EE:FF",
    serial_number="SN001",
    manufacturer="Dell",
    model="OptiPlex",
    os_name="Windows 10",
    os_version="22H2",
    assigned_user="jdoe",
    last_seen="2026-01-01",
    ip_address="192.168.1.100",
    raw_json=None,
    row_id=1,
):
    """Create a minimal me.sqlite with a computers table (matching manifest me_ec feed)."""
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.execute("""
        CREATE TABLE computers (
            id INTEGER PRIMARY KEY,
            resource_id TEXT, serial_number TEXT, mac_address TEXT,
            name TEXT, manufacturer TEXT, model TEXT, os_name TEXT,
            os_version TEXT, assigned_user TEXT, last_seen TEXT,
            domain_name TEXT, ip_address TEXT, source_type TEXT,
            raw_json TEXT, ingested_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO computers (id, resource_id, serial_number, mac_address, name, "
        "manufacturer, model, os_name, os_version, assigned_user, last_seen, "
        "domain_name, ip_address, source_type, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            resource_id,
            serial_number,
            mac_address,
            name,
            manufacturer,
            model,
            os_name,
            os_version,
            assigned_user,
            last_seen,
            "",
            ip_address,
            "ec",
            raw_json,
        ),
    )
    conn.commit()
    conn.close()


def _setup_me_mdm_db(
    data_dir,
    /,
    *,
    device_id="456",
    name="MBiPad-500",
    serial_number="K2317C63VG",
    mac_address="00:1A:2B:3C:4D:5E",
    manufacturer="Apple Inc.",
    model="iPad Air",
    os_version="18.5",
    platform="ios",
    user_email="user@moonlite.local",
    imei="352656100123450",
    raw_json=None,
    row_id=1,
):
    """Create a minimal me.sqlite with an mdm_devices table (matching manifest me_mdm feed)."""
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.execute("""
        CREATE TABLE mdm_devices (
            id INTEGER PRIMARY KEY,
            device_id TEXT, serial_number TEXT, imei TEXT, udid TEXT,
            name TEXT, model TEXT, os_version TEXT, mac_address TEXT,
            manufacturer TEXT, user_email TEXT, platform TEXT,
            enrolled_at TEXT, last_seen TEXT, source_type TEXT,
            raw_json TEXT, ingested_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO mdm_devices (id, device_id, serial_number, imei, udid, name, model, "
        "os_version, mac_address, manufacturer, user_email, platform, "
        "enrolled_at, last_seen, source_type, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            device_id,
            serial_number,
            imei,
            "",
            name,
            model,
            os_version,
            mac_address,
            manufacturer,
            user_email,
            platform,
            "",
            "",
            "mdm",
            raw_json,
        ),
    )
    conn.commit()
    conn.close()


def _setup_fg_clients_db(data_dir, /):
    """Create fg.sqlite with a clients table (matching manifest fg_clients feed)."""
    conn = sqlite3.connect(str(data_dir / "fg.sqlite"))
    conn.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY,
            mac TEXT, ipv4_address TEXT, hostname TEXT, ssid TEXT,
            vlan TEXT, user_name TEXT, os TEXT, manufacturer TEXT,
            model TEXT, signal TEXT, ap_name TEXT, interface TEXT,
            fg_host TEXT, raw_json TEXT, ingested_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO clients (mac, ipv4_address, hostname, user_name, os, "
        "manufacturer, model, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("aa:bb:cc:dd:ee:ff", "10.0.0.1", "fw01", "admin", "FortiOS", "Fortinet", "FortiGate", None),
    )
    conn.commit()
    conn.close()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_export_creates_csv(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir)

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    csv_path = csv_dir / "devices.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) >= 1
    assert "source" in rows[0]
    assert rows[0]["source"] == "me_ec"


def test_export_name_clean_normalized(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(
        data_dir,
        name="WS28.domain.com",
        raw_json=json.dumps({"resource_id": "res_001", "fqdn_name": "WS28.domain.com"}),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["name"] == "WS28.domain.com"
    assert row["name_clean"] == "ws28"


def test_export_mac_clean_normalized(tmp_path, monkeypatch):
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(
        data_dir,
        mac_address="AA:BB:CC:DD:EE:FF",
        raw_json=json.dumps({"resource_id": "res_001", "mac_address": "AA:BB:CC:DD:EE:FF"}),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert row["mac_clean"] == "aabbccddeeff"


def test_export_missing_db_skipped(tmp_path, monkeypatch):
    """Missing DB files are skipped; existing sources still export."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    # Only create me.sqlite — sp, fg, zbx, ad, sdp DBs are missing
    _setup_me_db(data_dir)

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    csv_path = csv_dir / "devices.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1  # Only me_ec row; other sources' DBs missing → skipped


def test_export_missing_table_skipped(tmp_path, monkeypatch):
    """Missing tables in existing DBs are skipped; other sources still export."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    # Create me.sqlite with no meaningful tables (empty DB)
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.close()
    # Create fg.sqlite with a clients table
    _setup_fg_clients_db(data_dir)

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    csv_path = csv_dir / "devices.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1  # Only fg row; me_ec/me_mdm tables missing → skipped


def test_export_field_mapping(tmp_path, monkeypatch):
    """All me_ec spec field paths are correctly mapped to output CSV columns."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(
        data_dir,
        resource_id="res_001",
        serial_number="SN001",
        manufacturer="Dell",
        model="OptiPlex",
        os_name="Windows 10",
        os_version="22H2",
        assigned_user="jdoe",
        ip_address="192.168.1.100",
        raw_json=json.dumps(
            {
                "resource_id": "res_001",
                "servicetag": "SN001",
                "hardware_vendor": "Dell",
                "model": "OptiPlex",
                "os_name": "Windows 10",
                "os_version": "22H2",
                "agent_logged_on_users": "jdoe",
                "ip_address": "192.168.1.100",
                "fqdn_name": "WS28.domain.com",
                "mac_address": "AA:BB:CC:DD:EE:FF",
            }
        ),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["source_id"] == "res_001"  # resource_id → source_id
    # Verify field-level mappings from me_ec spec (via raw_json extraction)
    assert row["manufacturer"] == "dell"  # manufacturer → manufacturer (export lowers via post-processing)
    assert row["model"] == "OptiPlex"  # model → model
    assert row["os"] == "Windows 10"  # os_name → os
    assert row["os_version"] == "22H2"  # os_version → os_version
    assert row["assigned_user"] == "jdoe"  # assigned_user → assigned_user
    assert row["ip_address"] == "192.168.1.100"  # ip_address → ip_address


def test_export_mac_clean_too_short(tmp_path, monkeypatch):
    """Short/invalid MAC produces empty mac_clean."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir, mac_address="AA:BB:CC")

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["mac_clean"] == ""


def test_export_db_error_skipped(tmp_path, monkeypatch):
    """When a source DB SELECT fails, that source is skipped with a warning."""
    import sqlite3 as sqlite3_mod

    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"

    # Create a real DB on disk so db_path.exists() passes, then the mock intercepts queries
    conn = sqlite3.connect(str(data_dir / "me.sqlite"))
    conn.execute("CREATE TABLE computers (id INTEGER PRIMARY KEY, name TEXT, serial_number TEXT)")
    conn.execute("INSERT INTO computers VALUES (1, 'machine-1', 'SN001')")
    conn.commit()
    conn.close()

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    table_check_result = [("computers",)]
    pragma_result = [("id",), ("resource_id",), ("name",), ("serial_number",)]

    def mock_execute(sql: str, *args: object, **kwargs: object) -> MagicMock:
        if "sqlite_master" in sql:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = table_check_result
            return mock_cursor
        if "pragma_table_info" in sql:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = pragma_result
            return mock_cursor
        msg = "simulated query error during SELECT"
        raise sqlite3_mod.Error(msg)

    mock_conn = MagicMock(spec=sqlite3_mod.Connection)
    mock_conn.execute = mock_execute  # type: ignore[method-assign]
    mock_conn.row_factory = sqlite3_mod.Row
    mock_conn.close = MagicMock()

    monkeypatch.setattr(export_mod.sqlite3, "connect", lambda *a, **kw: mock_conn)

    export_mod.export()

    # CSV should NOT exist (all sources skipped due to errors, export returns early)
    csv_path = csv_dir / "devices.csv"
    assert not csv_path.exists()


def test_export_main_block(tmp_path, monkeypatch):
    """When export_for_splink is run as __main__, export() is called."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_db(data_dir)

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    # __main__ just calls export(), so call it directly
    export_mod.export()

    csv_path = csv_dir / "devices.csv"
    assert csv_path.exists()


def test_export_me_mdm_field_mapping(tmp_path, monkeypatch):
    """me_mdm records get all Splink fields populated via raw_json extraction."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"
    _setup_me_mdm_db(
        data_dir,
        raw_json=json.dumps(
            {
                "device_id": "456",
                "device_name": "MBiPad-500",
                "serial_number": "K2317C63VG",
                "wifi_mac": "00:1A:2B:3C:4D:5E",
                "product_name": "Apple Inc.",
                "model": "iPad Air",
                "platform_type": "ios",
                "os_version": "18.5",
                "user": {"user_email": "user@moonlite.local"},
                "imei": "352656100123450",
            }
        ),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()

    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row["source"] == "me_mdm"
    assert row["source_id"] == "456"
    assert row["name"] == "MBiPad-500"
    assert row["name_clean"] == "mbipad-500"
    assert row["serial_number"] == "K2317C63VG"
    assert row["mac_address"] == "00:1A:2B:3C:4D:5E"
    assert row["mac_clean"] == "001a2b3c4d5e"
    assert row["manufacturer"] == "apple inc."  # normalized to lowercase by export
    assert row["model"] == "iPad Air"
    assert row["os"] == "ios"
    assert row["os_version"] == "18.5"
    assert row["assigned_user"] == "user@moonlite.local"
    assert row["imei"] == "352656100123450"


def test_export_raw_json_extraction(tmp_path, monkeypatch):
    """When raw_json is present, its values are extracted into CSV columns."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"

    # Set typed column values to WRONG data, but provide correct values in raw_json
    raw = {
        "resource_id": "res_correct",
        "fqdn_name": "real-hostname.domain.com",
        "servicetag": "SN-REAL001",
        "mac_address": "11:22:33:44:55:66",
        "hardware_vendor": "HP",
        "model": "EliteBook",
        "os_name": "Windows 11",
        "os_version": "23H2",
        "agent_logged_on_users": "real_user",
        "ip_address": "10.0.0.42",
    }
    _setup_me_db(
        data_dir,
        resource_id="",
        name="",
        mac_address="",
        serial_number="",
        manufacturer="",
        model="",
        os_name="",
        os_version="",
        assigned_user="",
        ip_address="",
        raw_json=json.dumps(raw),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()
    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    # Raw_json keys take priority over typed column values
    assert row["source_id"] == "res_correct"
    assert row["name"] == "real-hostname.domain.com"
    assert row["serial_number"] == "REAL001"
    assert row["mac_address"] == "11:22:33:44:55:66"
    assert row["manufacturer"] == "hp"  # lowered by export post-processing
    assert row["model"] == "EliteBook"
    assert row["os"] == "Windows 11"
    assert row["os_version"] == "23H2"
    # assigned_user uses raw_json key "agent_logged_on_users"
    assert row["assigned_user"] == "real_user"
    assert row["ip_address"] == "10.0.0.42"


def test_export_imei_list_artifact_returns_bare_digit(tmp_path, monkeypatch):
    """IMEI stored as JSON list in raw_json → CSV contains bare first digit, not str(list)."""
    import zentinull.export_for_splink as export_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    csv_dir = tmp_path / "export" / "csv"

    raw = {
        "device_id": "dev_001",
        "device_name": "iPhone14",
        "serial_number": "SN001",
        "wifi_mac": "aa:bb:cc:dd:ee:ff",
        "product_name": "Apple",
        "model": "iPhone 14",
        "platform_type": "iOS",
        "os_version": "17.0",
        "user": {"user_email": "joe@example.com"},
        "imei": ["354666655016848", "123456789012345"],
    }
    _setup_me_mdm_db(
        data_dir,
        name="",
        serial_number="",
        mac_address="",
        manufacturer="",
        model="",
        user_email="",
        platform="",
        raw_json=json.dumps(raw),
    )

    paths = _make_paths(tmp_path)
    monkeypatch.setattr(export_mod, "get_paths", lambda: paths)

    export_mod.export()
    with open(csv_dir / "devices.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    imei_val = row["imei"]
    # Must be a bare 15-digit IMEI, no brackets, quotes, or spaces
    assert imei_val == "354666655016848", f"IMEI list artifact not resolved: {imei_val!r}"
    # Structural checks — no Python repr artifacts
    assert "[" not in imei_val, f"Bracket leaked: {imei_val!r}"
    assert "'" not in imei_val, f"Quote leaked: {imei_val!r}"
