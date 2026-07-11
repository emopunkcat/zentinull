from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def inmemory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def sample_device_record() -> dict:
    return {
        "source": "sp",
        "source_id": "42",
        "name": "WS28",
        "serial_number": "SN001",
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "manufacturer": "Dell",
        "model": "OptiPlex 7080",
        "os": "Windows 10",
        "assigned_user": "jdoe",
        "ip_address": "192.168.1.100",
    }
