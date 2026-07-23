"""Tests for the strategy-driven runner raw-store write path.

Verifies that run_system() creates the §4 raw-store tables, inserts rows with
canonical JSON + SHA-256 hashing, and preserves existing tables on empty fetch.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from zentinull.ingest.runner import run_system
from zentinull.ingest.strategies import REGISTRY
from zentinull.manifest.types import Auth, Feed, Manifest, Role, System


def _sha256_of_canonical_json(data: dict) -> str:
    """Compute raw_hash as the runner does — sorted-keys JSON → SHA-256."""
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class TestRunnerRawStore:
    """Tests for the raw-store write path through the runner."""

    def test_run_system_creates_table_and_inserts_rows(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Given a manifest with a system and a feed, a fake strategy registered,
        and DATA_DIR pointing to tmp_path, when run_system is called,
        then the raw-store table is created with the §4 schema, a unique index
        exists on source_id, and the row data (source_id, raw_json, raw_hash,
        remote_updated_at, fetched_at) is correct.
        """
        fake_strategy_name = "test_raw_store_table"
        fake_records = [
            {"device_id": "d1", "name": "Device 1", "serial": "SN001"},
            {"device_id": "d2", "name": "Device 2", "serial": "SN002"},
        ]

        def _fake_fetch(endpoint: dict, auth: object) -> list[dict]:
            return fake_records

        REGISTRY[fake_strategy_name] = _fake_fetch
        try:
            auth = Auth(kind="none")
            system = System(
                auth=auth,
                strategy=fake_strategy_name,
                label="Fake",
            )
            feed = Feed(
                system="sys1",
                endpoint={},
                role=Role.ANCHOR,
                store="devices",
                id_path="device_id",
            )
            manifest = Manifest(
                project="test",
                systems={"sys1": system},
                feeds={"sys1_feed1": feed},
                profiles={},
            )

            # Override DATA_DIR so the runner creates the sqlite in tmp_path
            import zentinull.ingestors.base as base_mod
            from zentinull.config import ProjectPaths as _TestPaths

            _test_paths_obj = _TestPaths(
                project="test",
                data_dir=tmp_path,
                export_dir=tmp_path / "export",
                mesh_path=tmp_path / "mesh.duckdb",
                status_file=tmp_path / "status.json",
                log_file=tmp_path / "pipeline.log",
                csv_dir=tmp_path / "export" / "csv",
                splink_output_dir=tmp_path / "export" / "splink_output",
                benchmarks_dir=tmp_path / ".benchmarks",
            )
            monkeypatch.setattr(base_mod, "get_paths", lambda: _test_paths_obj)

            result = run_system("sys1", manifest, feed_keys=["sys1_feed1"])

            assert result == {"sys1_feed1": 2}

            # Verify the DB file exists
            db_path = tmp_path / "sys1.sqlite"
            assert db_path.exists()

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # Verify table schema — §4 columns
                cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
                assert cols["source_id"] == "TEXT"
                assert cols["raw_json"] == "TEXT"
                assert cols["raw_hash"] == "TEXT"
                assert cols["remote_updated_at"] == "TEXT"
                assert cols["fetched_at"] == "TEXT"
                assert cols["id"] == "INTEGER"

                # Verify unique index exists
                indexes = [row[1] for row in conn.execute("PRAGMA index_list(devices)").fetchall()]
                index_names = [conn.execute(f"PRAGMA index_info('{idx}')").fetchone()[2] for idx in indexes]
                assert "source_id" in index_names, f"No unique index on source_id (indexes: {indexes})"

                # Verify rows
                rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
                assert len(rows) == 2

                row1 = rows[0]
                assert row1["source_id"] == "d1"
                parsed = json.loads(row1["raw_json"])
                assert parsed["name"] == "Device 1"
                expected_hash = _sha256_of_canonical_json(fake_records[0])
                assert row1["raw_hash"] == expected_hash
                assert row1["remote_updated_at"] == ""

                row2 = rows[1]
                assert row2["source_id"] == "d2"
                parsed2 = json.loads(row2["raw_json"])
                assert parsed2["name"] == "Device 2"

                # fetched_at should be set to a timestamp
                assert row1["fetched_at"] is not None
                assert len(str(row1["fetched_at"])) > 5
            finally:
                conn.close()
        finally:
            REGISTRY.pop(fake_strategy_name, None)

    def test_empty_fetch_leaves_pre_existing_table_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Given a pre-existing raw-store table with existing data and a fake
        strategy that returns an empty list, when run_system is called,
        then the existing table is left intact (same schema, original rows preserved).
        """
        from zentinull.ingestors.base import create_raw_store, insert_raw_rows

        fake_strategy_name = "test_empty_fetch"

        def _empty_fetch(endpoint: dict, auth: object) -> list[dict]:
            return []

        REGISTRY[fake_strategy_name] = _empty_fetch
        try:
            # Pre-create the store with one row
            import zentinull.ingestors.base as base_mod
            from zentinull.config import ProjectPaths as _TestPaths

            _test_paths_obj = _TestPaths(
                project="test",
                data_dir=tmp_path,
                export_dir=tmp_path / "export",
                mesh_path=tmp_path / "mesh.duckdb",
                status_file=tmp_path / "status.json",
                log_file=tmp_path / "pipeline.log",
                csv_dir=tmp_path / "export" / "csv",
                splink_output_dir=tmp_path / "export" / "splink_output",
                benchmarks_dir=tmp_path / ".benchmarks",
            )
            monkeypatch.setattr(base_mod, "get_paths", lambda: _test_paths_obj)

            try:
                conn = sqlite3.connect(str(tmp_path / "sys1.sqlite"))
                try:
                    create_raw_store(conn, "devices")
                    insert_raw_rows(
                        conn,
                        "devices",
                        [{"device_id": "pre_existing", "name": "I was here first"}],
                        "device_id",
                    )
                    # Verify pre-existing data
                    count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
                    assert count == 1
                finally:
                    conn.close()

                # Now run the system — should fetch [] which is a no-op
                auth = Auth(kind="none")
                system = System(
                    auth=auth,
                    strategy=fake_strategy_name,
                    label="Fake",
                )
                feed = Feed(
                    system="sys1",
                    endpoint={},
                    role=Role.ANCHOR,
                    store="devices",
                    id_path="device_id",
                )
                manifest = Manifest(
                    project="test",
                    systems={"sys1": system},
                    feeds={"sys1_feed1": feed},
                    profiles={},
                )

                result = run_system("sys1", manifest, feed_keys=["sys1_feed1"])

                # Empty fetch → runner logs warning and returns 0
                assert result == {"sys1_feed1": 0}

                # Verify table still exists with same schema and original data
                conn2 = sqlite3.connect(str(tmp_path / "sys1.sqlite"))
                conn2.row_factory = sqlite3.Row
                try:
                    cols = {row[1] for row in conn2.execute("PRAGMA table_info(devices)").fetchall()}
                    assert "source_id" in cols
                    assert "raw_json" in cols
                    assert "raw_hash" in cols

                    rows = conn2.execute("SELECT * FROM devices").fetchall()
                    assert len(rows) == 1
                    assert rows[0]["source_id"] == "pre_existing"

                    # Index still exists
                    indexes = [row[2] for row in conn2.execute("PRAGMA index_list(devices)").fetchall()]
                    assert len(indexes) > 0
                finally:
                    conn2.close()
            finally:
                pass
        finally:
            REGISTRY.pop(fake_strategy_name, None)
