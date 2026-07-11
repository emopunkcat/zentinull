"""Extended tests for ingestors.base — db(), complex types, edge cases."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch


def test_db_creates_file(tmp_path):
    """db() creates a WAL-mode SQLite file under the configured DATA_DIR."""
    from zentinull.ingestors.base import db

    with patch("zentinull.ingestors.base.DATA_DIR", tmp_path):
        conn = db("test_source")

    assert isinstance(conn, sqlite3.Connection)
    # Verify WAL mode is enabled
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.upper() == "WAL"
    # Verify the file was created at the expected path
    expected_path = tmp_path / "test_source.sqlite"
    assert expected_path.exists()
    conn.close()


def test_insert_complex_types(inmemory_db):
    """insert() serialises dict and list values to JSON strings."""
    from zentinull.ingestors.base import create_table, insert

    create_table(inmemory_db, "t", ["name", "tags", "config"])
    records = [
        {"name": "device1", "tags": ["a", "b"], "config": ""},
        {"name": "device2", "tags": "", "config": {"key": "val"}},
    ]
    n = insert(inmemory_db, "t", records)
    assert n == 2

    rows = inmemory_db.execute("SELECT * FROM t ORDER BY id").fetchall()
    # Row 0: tags should be a JSON-encoded list
    assert json.loads(rows[0]["tags"]) == ["a", "b"]
    # Row 1: config should be a JSON-encoded dict
    assert json.loads(rows[1]["config"]) == {"key": "val"}


def test_insert_raw_extra_column(inmemory_db):
    """insert_raw() injects extra constant values into matching table columns."""
    from zentinull.ingestors.base import create_table, insert_raw

    create_table(inmemory_db, "t", ["name", "status"])
    n = insert_raw(
        inmemory_db,
        "t",
        [{"name": "a", "raw_json": "{}"}],
        extra={"status": "active"},
    )
    assert n == 1
    row = inmemory_db.execute("SELECT status FROM t").fetchone()
    assert row["status"] == "active"


def test_create_table_drops_existing(inmemory_db):
    """create_table drops and recreates the table, clearing prior rows."""
    from zentinull.ingestors.base import create_table, insert

    create_table(inmemory_db, "t", ["name"])
    insert(inmemory_db, "t", [{"name": "a"}, {"name": "b"}])
    assert inmemory_db.execute("SELECT count(*) FROM t").fetchone()[0] == 2

    # Second create_table call drops the old table and recreates it
    create_table(inmemory_db, "t", ["name"])
    assert inmemory_db.execute("SELECT count(*) FROM t").fetchone()[0] == 0


def test_create_table_raw_false(inmemory_db):
    """create_table(raw=False) omits the raw_json column from the schema."""
    from zentinull.ingestors.base import create_table

    create_table(inmemory_db, "t", ["name"], raw=False)
    cursor = inmemory_db.execute("PRAGMA table_info(t)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "raw_json" not in cols
    assert "id" in cols
    assert "name" in cols
    assert "ingested_at" in cols
