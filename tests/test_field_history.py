"""Integration tests for capture_field_history via upsert_raw_rows.

Exercises the field-level diffing pipeline end-to-end against a temp
SQLite db using the real ensure_raw_store and upsert_raw_rows functions.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


_BASE_RECORD = {"source_id": "dev42", "name": "WS-Alpha", "os": "Windows 10"}


def _count_fh(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM field_history").fetchone()[0]


def _all_fh(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT source_id, source, field, old_value, new_value, batch_id FROM field_history ORDER BY rowid"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Test 1 — Same data re-upserted → zero history rows (hash unchanged)
# ---------------------------------------------------------------------------


def test_same_data_produces_no_history(db: sqlite3.Connection) -> None:
    from zentinull.ingestors.base import ensure_raw_store, upsert_raw_rows

    ensure_raw_store(db, "t1")
    row = dict(_BASE_RECORD)
    upsert_raw_rows(db, "t1", [row], "source_id", source="sp", batch_id="b1")
    # Second pass with identical data
    n = upsert_raw_rows(db, "t1", [row], "source_id", source="sp", batch_id="b2")
    assert n == 0, "expected zero writes for identical data"
    assert _count_fh(db) == 0


# ---------------------------------------------------------------------------
# Test 2 — Changed field → one field_history row with correct old/new
# ---------------------------------------------------------------------------


def test_changed_field_produces_history(db: sqlite3.Connection) -> None:
    from zentinull.ingestors.base import ensure_raw_store, upsert_raw_rows

    ensure_raw_store(db, "t2")
    row = dict(_BASE_RECORD)
    upsert_raw_rows(db, "t2", [row], "source_id", source="sp", batch_id="b1")
    updated = dict(row, name="WS-Beta")
    n = upsert_raw_rows(db, "t2", [updated], "source_id", source="sp", batch_id="b2")
    assert n == 1, "expected one write for the changed record"

    rows = _all_fh(db)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "dev42"
    assert rows[0]["source"] == "sp"
    assert rows[0]["field"] == "name"
    assert rows[0]["old_value"] == "WS-Alpha"
    assert rows[0]["new_value"] == "WS-Beta"
    assert rows[0]["batch_id"] == "b2"


# ---------------------------------------------------------------------------
# Test 3 — Changed field to sentinel value ("--") → one history row
# ---------------------------------------------------------------------------


def test_sentinel_value_produces_history(db: sqlite3.Connection) -> None:
    from zentinull.ingestors.base import ensure_raw_store, upsert_raw_rows

    ensure_raw_store(db, "t3")
    row = dict(_BASE_RECORD)
    upsert_raw_rows(db, "t3", [row], "source_id", source="sp", batch_id="b1")
    sentinel_row = dict(row, name="--")
    n = upsert_raw_rows(db, "t3", [sentinel_row], "source_id", source="sp", batch_id="b2")
    assert n == 1, "expected one write for the changed record"

    rows = _all_fh(db)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "dev42"
    assert rows[0]["source"] == "sp"
    assert rows[0]["field"] == "name"
    assert rows[0]["old_value"] == "WS-Alpha"
    # "--" is a sentinel → normalized to None
    assert rows[0]["new_value"] is None
    assert rows[0]["batch_id"] == "b2"


# ---------------------------------------------------------------------------
# Test 4 — Initial insert (no existing row) → zero history rows
# ---------------------------------------------------------------------------


def test_initial_insert_produces_no_history(db: sqlite3.Connection) -> None:
    from zentinull.ingestors.base import ensure_raw_store, upsert_raw_rows

    ensure_raw_store(db, "t4")
    row = dict(_BASE_RECORD)
    n = upsert_raw_rows(db, "t4", [row], "source_id", source="sp", batch_id="b1")
    assert n == 1, "expected one write for first-time insert"
    assert _count_fh(db) == 0
