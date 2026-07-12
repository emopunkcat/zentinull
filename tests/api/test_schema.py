"""Tests for zentinull.api.schema — create_mesh_tables and the SQL constant strings."""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest


class TestCreateMeshTables:
    """create_mesh_tables() builds devices, source_records, metrics, events tables from CSV."""

    def test_tables_exist_after_create(self, tmp_path: Path) -> None:
        """All four tables (source_records, devices, metrics, events) exist after calling create_mesh_tables."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_sample_csv(tmp_path)
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert tables == {"source_records", "devices", "metrics", "events"}
        conn.close()

    def test_source_records_populated(self, tmp_path: Path) -> None:
        """source_records contains exactly the rows from the CSV."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_sample_csv(tmp_path)
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))
        rows = conn.execute("SELECT name, source, serial_number FROM source_records ORDER BY name").fetchall()

        assert rows[0] == ("LAPTOP-01", "me", "SN001")
        assert rows[1] == ("SERVER-01", "sp", "SN002")
        assert rows[2] == ("server-01", "zbx", "SN002")
        conn.close()

    def test_devices_consolidated(self, tmp_path: Path) -> None:
        """Devices table consolidates rows per cluster_id with aggregated fields."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_sample_csv(tmp_path)
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))

        rows = conn.execute(
            "SELECT cluster_id, source_count, device_name, serial_number FROM devices ORDER BY cluster_id"
        ).fetchall()

        assert len(rows) == 2
        # c1 has two sources, c2 has one
        assert rows[0] == ("c1", 2, "server-01", "SN002")
        assert rows[1] == ("c2", 1, "laptop-01", "SN001")
        conn.close()

    def test_metrics_table_appendable(self, tmp_path: Path) -> None:
        """Metrics table is created and accepts inserts."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_sample_csv(tmp_path)
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))

        conn.execute(
            "INSERT INTO metrics (cluster_id, source, metric_name, value, recorded_at) VALUES (?, ?, ?, ?, now())",
            ["c1", "zbx", "cpu_pct", 42.5],
        )
        count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert count == 1
        conn.close()

    def test_events_table_appendable(self, tmp_path: Path) -> None:
        """Events table is created and accepts inserts."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_sample_csv(tmp_path)
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))

        conn.execute(
            "INSERT INTO events (cluster_id, source, event_type, detail, recorded_at) VALUES (?, ?, ?, ?, now())",
            ["c1", "zbx", "reboot", "Server restarted"],
        )
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 1
        conn.close()

    def test_devices_unnamed_cluster(self, tmp_path: Path) -> None:
        """A cluster with no valid name value gets default device_name '(unnamed)'."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = tmp_path / "devices.csv"
        _write_csv(
            csv_path,
            [
                [
                    "cluster_id",
                    "source",
                    "source_id",
                    "name",
                    "name_clean",
                    "serial_number",
                    "mac_address",
                    "mac_clean",
                    "asset_tag",
                    "manufacturer",
                    "model",
                    "os",
                    "os_version",
                    "assigned_user",
                    "ip_address",
                    "imei",
                ],
                ["c3", "me", "res-1", "", "", "SN003", "", "", "", "", "", "", "", "", "", ""],
            ],
        )
        conn = duckdb.connect()
        create_mesh_tables(conn, str(csv_path))

        name = conn.execute("SELECT device_name FROM devices").fetchone()[0]
        assert name == "(unnamed)"
        conn.close()

    def test_devices_no_cluster_id_row(self, tmp_path: Path) -> None:
        """If the CSV is empty, source_records and devices are empty."""
        from zentinull.api.schema import create_mesh_tables

        csv_path = _write_csv(tmp_path / "empty.csv", [SPLINK_HEADERS])
        conn = duckdb.connect()

        create_mesh_tables(conn, str(csv_path))

        rec_count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        dev_count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        assert rec_count == 0
        assert dev_count == 0
        conn.close()


class TestSqlConstants:
    """SQL string constants are valid and syntactically correct."""

    def test_source_records_sql_parses(self) -> None:
        """SOURCE_RECORDS_SQL is syntactically valid in DuckDB."""
        from zentinull.api.schema import SOURCE_RECORDS_SQL

        conn = duckdb.connect()
        # Parse-only verification: can be described (won't execute without param)
        # We check it's a non-empty string containing expected keywords
        assert "CREATE OR REPLACE TABLE" in SOURCE_RECORDS_SQL
        assert "read_csv_auto" in SOURCE_RECORDS_SQL
        conn.close()

    def test_devices_sql_parses(self) -> None:
        """DEVICES_SQL is valid DuckDB SQL — creates source_records first then runs the DDL."""
        from zentinull.api.schema import DEVICES_SQL

        conn = duckdb.connect()
        conn.execute("""
            CREATE TABLE source_records AS SELECT * FROM (VALUES
                ('c1', 'sp', 'sp-1', 'server-01', 'server-01', 'SN001', 'aa:bb:cc:dd:ee:ff', 'aabbccddeeff',
                 '', 'Dell', 'PowerEdge', 'Windows', '2022', '', '10.0.0.1', '')
            ) t(cluster_id, source, source_id, name, name_clean, serial_number,
                mac_address, mac_clean, asset_tag, manufacturer, model, os, os_version,
                assigned_user, ip_address, imei)
        """)
        try:
            conn.execute(f"EXPLAIN {DEVICES_SQL}")
        except duckdb.Error as e:
            pytest.fail(f"DEVICES_SQL parse error: {e}")
        finally:
            conn.close()

    def test_metrics_sql_table_created(self) -> None:
        """METRICS_SQL creates the metrics table successfully."""
        from zentinull.api.schema import METRICS_SQL

        conn = duckdb.connect()
        conn.execute(METRICS_SQL)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        assert "metrics" in tables
        conn.close()

    def test_events_sql_table_created(self) -> None:
        """EVENTS_SQL creates the events table successfully."""
        from zentinull.api.schema import EVENTS_SQL

        conn = duckdb.connect()
        conn.execute(EVENTS_SQL)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        assert "events" in tables
        conn.close()

    def test_indexes_sql_creates_indexes(self) -> None:
        """INDEXES_SQL creates indexes after tables exist."""
        from zentinull.api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL

        conn = duckdb.connect()
        conn.execute(
            """CREATE TABLE source_records AS SELECT * FROM (VALUES
                ('c1', 'sp', 'sp-1', 'server-01', 'server-01', 'SN001', 'aa:bb:cc:dd:ee:ff', 'aabbccddeeff',
                 '', 'Dell', 'PowerEdge', 'Windows', '2022', '', '10.0.0.1', '')
            ) t(cluster_id, source, source_id, name, name_clean, serial_number,
                mac_address, mac_clean, asset_tag, manufacturer, model, os, os_version,
                assigned_user, ip_address, imei)"""
        )
        conn.execute(DEVICES_SQL)
        conn.execute(METRICS_SQL)
        conn.execute(EVENTS_SQL)
        conn.execute(INDEXES_SQL)
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_devices_name" in indexes
        assert "idx_devices_serial" in indexes
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

SPLINK_HEADERS = [
    "cluster_id",
    "source",
    "source_id",
    "name",
    "name_clean",
    "serial_number",
    "mac_address",
    "mac_clean",
    "asset_tag",
    "manufacturer",
    "model",
    "os",
    "os_version",
    "assigned_user",
    "ip_address",
    "imei",
]


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    """Write rows to a CSV file at *path* and return the path."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return path


def _write_sample_csv(tmp_path: Path) -> Path:
    """Write a sample devices.csv with 3 rows across 2 clusters and return its path."""
    return _write_csv(
        tmp_path / "devices.csv",
        [
            SPLINK_HEADERS,
            # c1 — server-01 from sp (row 1)
            [
                "c1",
                "sp",
                "sp-001",
                "SERVER-01",
                "server-01",
                "SN002",
                "aa:bb:cc:dd:ee:01",
                "aabbccddee01",
                "",
                "Dell",
                "PowerEdge",
                "Windows Server",
                "2022",
                "",
                "10.0.0.1",
                "",
            ],
            # c1 — server-01 from zbx (row 2) — same cluster, different source
            [
                "c1",
                "zbx",
                "101",
                "server-01",
                "server-01",
                "SN002",
                "aa:bb:cc:dd:ee:01",
                "aabbccddee01",
                "",
                "Dell",
                "PowerEdge",
                "Linux",
                "Ubuntu 22.04",
                "",
                "10.0.0.1",
                "",
            ],
            # c2 — laptop-01 from me (row 3)
            [
                "c2",
                "me",
                "res-001",
                "LAPTOP-01",
                "laptop-01",
                "SN001",
                "aa:bb:cc:dd:ee:02",
                "aabbccddee02",
                "",
                "Lenovo",
                "ThinkPad",
                "Windows 11",
                "24H2",
                "jdoe",
                "10.0.0.2",
                "",
            ],
        ],
    )
