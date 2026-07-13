"""Tests for ingestors.base — SQLite helpers."""

from __future__ import annotations


def test_create_table_creates_schema(inmemory_db):
    from zentinull.ingestors.base import create_table

    create_table(inmemory_db, "test_devices", ["name", "serial_number TEXT"])
    cursor = inmemory_db.execute("PRAGMA table_info(test_devices)")
    cols = {row[1]: row[2] for row in cursor.fetchall()}
    assert cols["id"] == "INTEGER"
    # Column types are stripped by create_table (everything stored as inserted type)
    assert cols["name"] == ""
    assert cols["raw_json"] == "TEXT"
    assert cols["ingested_at"] == "TEXT"


def test_insert_returns_count(inmemory_db):
    from zentinull.ingestors.base import create_table, insert

    create_table(inmemory_db, "t", ["name"])
    n = insert(inmemory_db, "t", [{"name": "a"}, {"name": "b"}])
    assert n == 2
    assert inmemory_db.execute("SELECT count(*) FROM t").fetchone()[0] == 2


def test_insert_raw_adds_extra_cols(inmemory_db):
    from zentinull.ingestors.base import create_table, insert_raw

    create_table(inmemory_db, "t", ["name", "source"])
    n = insert_raw(inmemory_db, "t", [{"name": "a", "raw_json": "{}"}], extra={"source": "test"})
    assert n == 1
    row = inmemory_db.execute("SELECT * FROM t").fetchone()
    assert row["source"] == "test"


def test_insert_empty_returns_zero(inmemory_db):
    from zentinull.ingestors.base import insert

    n = insert(inmemory_db, "t", [])
    assert n == 0


def test_insert_raw_empty_returns_zero(inmemory_db):
    from zentinull.ingestors.base import insert_raw

    n = insert_raw(inmemory_db, "t", [])
    assert n == 0


def test_create_table_atomic_swap_clears_data_and_no_tmp_leak(inmemory_db):
    """create_table uses temp-table swap: old data cleared, no _tmp table left."""
    from zentinull.ingestors.base import create_table, insert

    create_table(inmemory_db, "t", ["name"])
    insert(inmemory_db, "t", [{"name": "a"}, {"name": "b"}])
    assert inmemory_db.execute("SELECT count(*) FROM t").fetchone()[0] == 2

    # Recreate — triggers temp-table + atomic rename
    create_table(inmemory_db, "t", ["name"])
    assert inmemory_db.execute("SELECT count(*) FROM t").fetchone()[0] == 0

    # Verify the _tmp table was cleaned up (no leak)
    tables = inmemory_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='t_tmp'").fetchall()
    assert len(tables) == 0
