"""Phase 5 acceptance gate — Attach resolver behavioral tests.

Verifies the three attachment strategies (exact, normalized, extract_fuzzy)
and the orchestrator's table-not-found handling.

Key invariants:
- Ambiguous tokens with link.multi=False -> exactly 1 result (no NULL row)
- link.multi=True with distinct tokens -> N results with N distinct cluster_ids
- Attachments are N rows per link, never a merge
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import duckdb

from zentinull.manifest.types import Feed, Link, Role
from zentinull.resolve.attach import (
    build_keyspace,
    resolve_exact,
    resolve_extract_fuzzy,
    resolve_feed_attachments,
    resolve_normalized,
)


def _seed_mesh(mesh_path: str) -> None:
    """Create a minimal source_records table in the mesh DB for keyspace building."""
    conn = duckdb.connect(mesh_path)
    try:
        conn.execute(
            """
            CREATE TABLE source_records (
                cluster_id TEXT,
                source TEXT,
                source_id TEXT,
                name_clean TEXT,
                mac_clean TEXT,
                serial_number TEXT,
                asset_tag TEXT,
                assigned_user TEXT DEFAULT '',
                manufacturer TEXT DEFAULT '',
                os TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO source_records VALUES
            ('cid_a', 'sp', 'sp_1', 'mbipad-072', 'aabbccddeeff', 'SN001', 'TAG001', 'jdoe@example.com', 'Apple', 'ios'),
            ('cid_b', 'fg', 'fg_1', 'ws28', '112233445566', 'SN002', 'TAG002', 'jsmith@example.com', 'Dell', 'Windows 10'),
            ('cid_c', 'zbx', 'zbx_1', 'sr01', 'aabbcc112233', 'SN003', 'TAG003', '', 'HP', 'Linux')
            """
        )
    finally:
        conn.close()


def _make_link(**kwargs: object) -> Link:
    """Build a Link with sensible defaults for testing."""
    defaults: dict[str, object] = {
        "field": "description",
        "to": "device",
        "on": "name_clean",
        "strategy": "exact",
        "transform": None,
        "multi": False,
        "scope": (),
    }
    defaults.update(kwargs)
    return Link(**defaults)  # type: ignore[arg-type]


def test_exact_strategy_match() -> None:
    """resolve_exact with field value matching a keyspace entry -> [AttachResult(confidence=1.0)]."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="serial_number", strategy="exact")
        raw_record = {"serial_number": "SN001"}
        results = resolve_exact(link, raw_record, keyspace)

        assert len(results) == 1
        assert results[0].cluster_id == "cid_a"
        assert results[0].confidence == 1.0


def test_exact_strategy_no_match() -> None:
    """resolve_exact with value not in keyspace -> []."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="serial_number", strategy="exact")
        raw_record = {"serial_number": "NOTFOUND"}
        results = resolve_exact(link, raw_record, keyspace)

        assert results == []


def test_normalized_strategy_match() -> None:
    """resolve_normalized applies transform then looks up -> match at confidence 1.0."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        # The keyspace lowercases values; mac transform normalizes separators
        link = _make_link(field="mac", strategy="normalized", transform="mac")
        # normalize_mac("AA-BB-CC-DD-EE-FF") -> "aa:bb:cc:dd:ee:ff"
        raw_record = {"mac": "AA-BB-CC-DD-EE-FF"}
        results = resolve_normalized(link, raw_record, keyspace)

        assert len(results) == 1
        assert results[0].cluster_id == "cid_a"
        assert results[0].confidence == 1.0


def test_normalized_strategy_no_match() -> None:
    """resolve_normalized with unresolvable value -> []."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="mac", strategy="normalized", transform="mac")
        raw_record = {"mac": "ZZ:ZZ:ZZ:ZZ:ZZ:ZZ"}
        results = resolve_normalized(link, raw_record, keyspace)

        assert results == []


def test_exact_strategy_match_assigned_user() -> None:
    """build_keyspace includes assigned_user; resolve_exact matches email."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="email", strategy="exact", on="assigned_user")
        raw_record = {"email": "jdoe@example.com"}
        results = resolve_exact(link, raw_record, keyspace)

        assert len(results) == 1
        assert results[0].cluster_id == "cid_a"
        assert results[0].confidence == 1.0


def test_ambiguous_token_multi_false_returns_one() -> None:
    """link.multi=False, token matching multiple clusters -> exactly 1 result (highest confidence/longest token)."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        # Text containing tokens that match multiple clusters
        link = _make_link(field="description", strategy="extract_fuzzy", multi=False)
        raw_record = {"description": "ws28 sr01"}
        results = resolve_extract_fuzzy(link, raw_record, keyspace, set(), set())

        assert len(results) == 1
        # No NULL row — empty list means no attachment; a single result is not a NULL row
        assert results[0].cluster_id in {"cid_b", "cid_c"}


def test_ambiguous_token_no_match_returns_empty() -> None:
    """resolve_extract_fuzzy with a token matching 0 keyspace entries -> []."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="description", strategy="extract_fuzzy", multi=False)
        raw_record = {"description": "zzzzzz nonexistent"}
        results = resolve_extract_fuzzy(link, raw_record, keyspace, set(), set())

        assert results == []


def test_multi_ref_never_merges() -> None:
    """link.multi=True, 3 distinct tokens each matching a different cluster_id -> 3 distinct cluster_ids."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        keyspace = build_keyspace(mesh_path)

        link = _make_link(field="description", strategy="extract_fuzzy", multi=True)
        # Three tokens that match the three clusters by name_clean
        raw_record = {"description": "mbipad-072 ws28 sr01"}
        results = resolve_extract_fuzzy(link, raw_record, keyspace, set(), set())

        cluster_ids = {r.cluster_id for r in results}
        assert len(results) == 3, f"Expected 3 results, got {len(results)}: {results}"
        assert cluster_ids == {"cid_a", "cid_b", "cid_c"}, f"Got {cluster_ids}"
        # Each result has a distinct cluster_id — attachments are N rows, never a merge
        assert len(cluster_ids) == len(results)


def test_resolve_feed_attachments_table_not_found() -> None:
    """resolve_feed_attachments with feed.store not in SQLite tables -> [], logs warning."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)
        sqlite_path = str(Path(tmp) / "nonexistent.sqlite")

        # Create empty SQLite (no tables)
        conn = sqlite3.connect(sqlite_path)
        conn.close()

        feed = Feed(
            system="sdp",
            endpoint={},
            role=Role.ATTACHMENT,
            store="nonexistent_table",
            id_path="id",
            links=(_make_link(field="description", strategy="extract_fuzzy", multi=True),),
        )

        results = resolve_feed_attachments(feed, "sdp_requests", mesh_path, sqlite_path)
        assert results == []


def test_resolve_feed_attachments_extracts_and_resolves() -> None:
    """resolve_feed_attachments with real data -> produces attachment row dicts with correct fields."""
    with tempfile.TemporaryDirectory() as tmp:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        _seed_mesh(mesh_path)

        sqlite_path = str(Path(tmp) / "sdp.sqlite")
        conn = sqlite3.connect(sqlite_path)
        conn.execute(
            """
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY,
                source_id TEXT,
                raw_json TEXT,
                raw_hash TEXT,
                remote_updated_at TEXT,
                fetched_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO requests (id, source_id, raw_json, raw_hash) VALUES (?, ?, ?, ?)",
            (1, "req_001", json.dumps({"description": "ws28 sr01"}), "hash1"),
        )
        conn.commit()
        conn.close()

        feed = Feed(
            system="sdp",
            endpoint={},
            role=Role.ATTACHMENT,
            store="requests",
            id_path="id",
            links=(_make_link(field="description", strategy="extract_fuzzy", multi=True),),
        )

        results = resolve_feed_attachments(feed, "sdp_requests", mesh_path, sqlite_path)
        assert len(results) == 2, f"Expected 2 attachment rows, got {len(results)}"
        cids = {r["cluster_id"] for r in results}
        assert cids == {"cid_b", "cid_c"}, f"Got {cids}"
        for r in results:
            assert r["feed_key"] == "sdp_requests"
            assert r["source_id"] == "req_001"
            assert "confidence" in r
            assert "payload" in r


def test_resolve_feed_attachments_sharepoint_lookup_by_id() -> None:
    """sp_devicenotes LookupToDevicesLookupId -> sp_devices source_id exact match."""
    tmp = tempfile.mkdtemp()
    try:
        mesh_path = str(Path(tmp) / "mesh.duckdb")
        conn = duckdb.connect(mesh_path)
        conn.execute(
            """
            CREATE TABLE source_records (
                cluster_id TEXT, source TEXT, source_id TEXT,
                name_clean TEXT, mac_clean TEXT, serial_number TEXT,
                asset_tag TEXT, assigned_user TEXT DEFAULT '',
                manufacturer TEXT DEFAULT '', os TEXT DEFAULT ''
            )
            """
        )
        # sp_devices record with source_id matching the SharePoint item ID
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("cid_x", "sp", "42", "ws28", "", "", "", "", "", ""),
        )
        conn.close()

        sqlite_path = str(Path(tmp) / "sp.sqlite")
        with sqlite3.connect(sqlite_path) as sconn:
            sconn.execute(
                """
                CREATE TABLE sp_devicenotes (
                    id INTEGER PRIMARY KEY,
                    source_id TEXT,
                    raw_json TEXT,
                    raw_hash TEXT,
                    remote_updated_at TEXT,
                    fetched_at TEXT
                )
                """
            )
            sconn.execute(
                "INSERT INTO sp_devicenotes (id, source_id, raw_json, raw_hash) VALUES (?, ?, ?, ?)",
                (1, "note_001", json.dumps({"fields": {"LookupToDevicesLookupId": "42"}}), "hash1"),
            )

        feed = Feed(
            system="sp",
            endpoint={},
            role=Role.ATTACHMENT,
            store="sp_devicenotes",
            id_path="id",
            links=(
                Link(
                    field="fields.LookupToDevicesLookupId",
                    to="device",
                    on="source_id",
                    strategy="exact",
                    scope=("sp_devices",),
                ),
            ),
        )

        results = resolve_feed_attachments(feed, "sp_devicenotes", mesh_path, sqlite_path)
        assert len(results) == 1
        assert results[0]["cluster_id"] == "cid_x"
        assert results[0]["source_id"] == "note_001"
        assert results[0]["field"] == "fields.LookupToDevicesLookupId"
    finally:
        import gc

        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)
