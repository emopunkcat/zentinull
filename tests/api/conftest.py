"""Shared fixtures for api tests — seeded DuckDB mesh, mocks, and TestClient."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest
from fastapi.testclient import TestClient

from zentinull.api.db import MeshDB
from zentinull.api.schema import EVENTS_SQL, INDEXES_SQL, METRICS_SQL
from zentinull.api.server import app

# ═══════════════════════════════════════════════════════════════════════════════
# Seeded DuckDB Mesh — covers every code path in MeshDB
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def seeded_meshdb(tmp_path: Path) -> MeshDB:
    """Create a temporary DuckDB mesh with pre-seeded data across all four tables.

    Devices: 4 rows covering multi-source (c1), medium (c2), unnamed (c3), single (c4).
    Source records: 7 rows (3 for c1, 2 for c2, 1 for c3, 1 for c4).
    Metrics: 5 rows for c1 — cpu_pct from zbx+me, disk_pct from zbx+me, memory_pct from me.
    Events: 3 rows — info, warning, critical.
    """
    db_path = tmp_path / "mesh.duckdb"
    conn = duckdb.connect(str(db_path))

    now = datetime.now(UTC)

    # ── source_records ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE source_records (
            cluster_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            name_clean TEXT DEFAULT '',
            serial_number TEXT DEFAULT '',
            mac_address TEXT DEFAULT '',
            mac_clean TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            model TEXT DEFAULT '',
            os TEXT DEFAULT '',
            assigned_user TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            imei TEXT DEFAULT ''
        )
    """)

    source_records_data: list[tuple] = [
        # c1: ws28 — 3 sources (sp, me, fg)
        (
            "c1",
            "sp",
            "sp_42",
            "WS28",
            "ws28",
            "SN001",
            "aa:bb:cc:dd:ee:ff",
            "aabbccddeeff",
            "Dell",
            "OptiPlex 7080",
            "Windows 10",
            "jdoe",
            "192.168.1.100",
            "",
        ),
        (
            "c1",
            "me",
            "me_101",
            "WS28",
            "ws28",
            "SN001",
            "aa:bb:cc:dd:ee:ff",
            "aabbccddeeff",
            "Dell",
            "OptiPlex 7080",
            "Windows 10",
            "jdoe",
            "",
            "",
        ),
        ("c1", "fg", "fg_7", "ws28", "ws28", "", "", "", "", "", "Windows 10", "", "192.168.1.100", ""),
        # c2: dc01 — 2 sources (ad, zbx)
        (
            "c2",
            "ad",
            "ad_12",
            "DC01",
            "dc01",
            "SN002",
            "11:22:33:44:55:66",
            "112233445566",
            "",
            "",
            "Server 2022",
            "",
            "10.0.0.1",
            "",
        ),
        ("c2", "zbx", "zbx_3", "dc01", "dc01", "SN002", "", "", "", "", "", "", "10.0.0.1", ""),
        # c3: unnamed — 1 source (sp)
        ("c3", "sp", "sp_99", "", "", "", "", "", "", "", "", "", "", ""),
        # c4: phone01 — 1 source (me_mdm)
        (
            "c4",
            "me_mdm",
            "mdm_55",
            "phone01",
            "phone01",
            "SN003",
            "",
            "",
            "Apple",
            "iPhone 15",
            "iOS 17",
            "jsmith",
            "",
            "356789012345678",
        ),
    ]
    for row in source_records_data:
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # ── devices (built from source_records aggregation) ────────────────────
    conn.execute("""
        CREATE TABLE devices (
            cluster_id TEXT NOT NULL,
            device_name TEXT DEFAULT '',
            source_count BIGINT NOT NULL DEFAULT 0,
            sources TEXT[] NOT NULL DEFAULT [],
            serial_number TEXT DEFAULT '',
            mac_address TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            model TEXT DEFAULT '',
            os TEXT DEFAULT '',
            assigned_user TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            imei TEXT DEFAULT '',
            record_count BIGINT NOT NULL DEFAULT 0
        )
    """)

    devices_data: list[tuple] = [
        (
            "c1",
            "ws28",
            3,
            ["sp", "me", "fg"],
            "SN001",
            "aabbccddeeff",
            "Dell",
            "OptiPlex 7080",
            "Windows 10",
            "jdoe",
            "192.168.1.100",
            "",
            3,
        ),
        ("c2", "dc01", 2, ["ad", "zbx"], "SN002", "112233445566", "", "", "Server 2022", "", "10.0.0.1", "", 2),
        ("c3", "(unnamed)", 1, ["sp"], "", "", "", "", "", "", "", "", 1),
        (
            "c4",
            "phone01",
            1,
            ["me_mdm"],
            "SN003",
            "",
            "Apple",
            "iPhone 15",
            "iOS 17",
            "jsmith",
            "",
            "356789012345678",
            1,
        ),
    ]
    for row in devices_data:
        conn.execute(
            "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    # ── metrics ────────────────────────────────────────────────────────────
    conn.execute(METRICS_SQL)

    metrics_data = [
        ("c1", "zbx", "cpu_pct", 45.2, None, [], now, now),
        ("c1", "me", "cpu_pct", 42.8, None, [], now, now),
        ("c1", "zbx", "disk_pct", 67.1, None, [], now, now),
        ("c1", "me", "disk_pct", 65.0, None, [], now, now),
        ("c1", "me", "memory_pct", 58.3, None, [], now, now),
    ]
    for row in metrics_data:
        conn.execute(
            "INSERT INTO metrics (cluster_id, source, metric_name, value, text_value, tags, recorded_at, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # ── events ─────────────────────────────────────────────────────────────
    conn.execute(EVENTS_SQL)
    events_data: list[tuple] = [
        ("c1", "zbx", "alert", "CPU usage above threshold", "info", now, now),
        ("c1", "me", "warning", "Disk space low", "warning", now, now),
        ("c2", "zbx", "alert", "Host unreachable", "critical", now, now),
    ]
    for row in events_data:
        conn.execute(
            "INSERT INTO events (cluster_id, source, event_type, detail, severity, recorded_at, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # ── indexes ────────────────────────────────────────────────────────────
    conn.execute(INDEXES_SQL)

    conn.execute("CHECKPOINT")
    conn.close()

    return MeshDB(db_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Mock + TestClient for independent router tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_meshdb() -> MagicMock:
    """Strictly-spec'd mock of MeshDB — router-level tests control return values."""
    return MagicMock(spec=MeshDB)


@pytest.fixture
def client_with_db(mock_meshdb: MagicMock) -> TestClient:
    """FastAPI TestClient wired with mock_meshdb as app.state.db."""
    app.state.db = mock_meshdb
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient with app.state.db = None (503 path)."""
    app.state.db = None
    return TestClient(app)
