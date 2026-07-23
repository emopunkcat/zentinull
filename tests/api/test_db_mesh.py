"""Tests for MeshDB metric and event methods — device_metrics, device_metric_names,
device_metric_summary, device_timeline, device_stats."""

from __future__ import annotations

from pathlib import Path

from zentinull.api.db import MeshDB


class TestDeviceMetrics:
    """device_metrics() — time-series metrics filtered by metric, source, time range."""

    def test_device_metrics_all(self, seeded_meshdb: MeshDB) -> None:
        """All 5 metrics for c1 are returned."""
        results = seeded_meshdb.device_metrics("c1")
        assert len(results) == 5
        sources = {r["source"] for r in results}
        assert sources == {"zbx", "me"}

    def test_device_metrics_filter_by_name(self, seeded_meshdb: MeshDB) -> None:
        """Filtering by metric_name=cpu_pct returns only cpu_pct rows (2 from zbx+me)."""
        results = seeded_meshdb.device_metrics("c1", metric="cpu_pct")
        assert len(results) == 2
        assert all(r["metric_name"] == "cpu_pct" for r in results)

    def test_device_metrics_filter_by_source(self, seeded_meshdb: MeshDB) -> None:
        """Filtering by source=zbx returns only Zabbix metrics (2: cpu_pct, disk_pct)."""
        results = seeded_meshdb.device_metrics("c1", source="zbx")
        assert len(results) == 2
        assert all(r["source"] == "zbx" for r in results)

    def test_device_metrics_hours_filter(self, seeded_meshdb: MeshDB) -> None:
        """hours=0 disables the time filter, returning all metrics. hours=1 keeps results
        since the seeded data was inserted with recorded_at=now."""
        results = seeded_meshdb.device_metrics("c1", hours=1)
        assert len(results) == 5


class TestDeviceMetricNames:
    """device_metric_names() — distinct metric names for a device."""

    def test_device_metric_names(self, seeded_meshdb: MeshDB) -> None:
        """Returns sorted distinct metric names for c1: cpu_pct, disk_pct, memory_pct."""
        names = seeded_meshdb.device_metric_names("c1")
        assert names == ["cpu_pct", "disk_pct", "memory_pct"]


class TestDeviceMetricSummary:
    """device_metric_summary() — aggregated stats per metric."""

    def test_device_metric_summary(self, seeded_meshdb: MeshDB) -> None:
        """Returns a dict keyed by metric_name with avg/max/min/latest/count sub-keys."""
        summary = seeded_meshdb.device_metric_summary("c1")
        assert set(summary.keys()) == {"cpu_pct", "disk_pct", "memory_pct"}
        for _metric_name, stats in summary.items():
            assert set(stats.keys()) == {"count", "avg", "max", "min", "latest"}
            assert stats["count"] > 0


class TestDeviceTimeline:
    """device_timeline() — recent events for a device."""

    def test_device_timeline(self, seeded_meshdb: MeshDB) -> None:
        """c1 has 2 events: info+warning. Returns list of event dicts ordered by time desc."""
        events = seeded_meshdb.device_timeline("c1")
        assert len(events) == 2
        severities = {e["severity"] for e in events}
        assert severities == {"info", "warning"}


class TestDeviceStats:
    """device_stats() — latest metric values + event counts by severity."""

    def test_device_stats(self, seeded_meshdb: MeshDB) -> None:
        """Returns dict with 'metrics' and 'event_counts' keys."""
        stats = seeded_meshdb.device_stats("c1")
        assert set(stats.keys()) == {"metrics", "event_counts"}
        # 3 distinct metrics with latest values
        assert set(stats["metrics"].keys()) == {"cpu_pct", "disk_pct", "memory_pct"}
        # 2 events: info=1, warning=1
        assert stats["event_counts"] == {"info": 1, "warning": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# search()
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearch:
    """search() — lookup devices by field or full-text."""

    def test_by_device_name(self, seeded_meshdb: MeshDB) -> None:
        """search("ws", field="device_name") finds c1 (ws28)."""
        results = seeded_meshdb.search("ws", field="device_name")
        ids = [r.cluster_id for r in results]
        assert "c1" in ids

    def test_by_serial(self, seeded_meshdb: MeshDB) -> None:
        """search("SN00", field="serial_number") finds c1, c2, and c4."""
        results = seeded_meshdb.search("SN00", field="serial_number")
        ids = {r.cluster_id for r in results}
        assert ids == {"c1", "c2", "c4"}

    def test_by_mac_clean(self, seeded_meshdb: MeshDB) -> None:
        """search("aa:bb:cc:dd:ee:ff", field="mac_clean") — _norm_mac applied, finds c1."""
        results = seeded_meshdb.search("aa:bb:cc:dd:ee:ff", field="mac_clean")
        ids = [r.cluster_id for r in results]
        assert ids == ["c1"]

    def test_full_text_no_field(self, seeded_meshdb: MeshDB) -> None:
        """search("dell") without field finds c1 by manufacturer."""
        results = seeded_meshdb.search("dell")
        ids = [r.cluster_id for r in results]
        assert "c1" in ids

    def test_limit(self, seeded_meshdb: MeshDB) -> None:
        """search("", limit=1) returns at most 1 result."""
        results = seeded_meshdb.search("", limit=1)
        assert len(results) <= 1

    def test_no_results(self, seeded_meshdb: MeshDB) -> None:
        """search("zzz") returns empty list."""
        results = seeded_meshdb.search("zzz")
        assert results == []

    def test_by_assigned_user(self, seeded_meshdb: MeshDB) -> None:
        """search("jdoe", field="assigned_user") finds c1."""
        results = seeded_meshdb.search("jdoe", field="assigned_user")
        ids = [r.cluster_id for r in results]
        assert ids == ["c1"]

    def test_unknown_field_falls_back_to_full_text(self, seeded_meshdb: MeshDB) -> None:
        """An unknown field name is ignored (full-text), never interpolated raw into SQL."""
        # 'dell' matches c1 by manufacturer under full-text; a bogus field must not error.
        results = seeded_meshdb.search("dell", field="not_a_real_column")
        ids = [r.cluster_id for r in results]
        assert "c1" in ids

    def test_field_injection_is_neutralized(self, seeded_meshdb: MeshDB) -> None:
        """A SQL-injection payload in field neither errors nor leaks — treated as unknown."""
        payload = "device_name) LIKE '%' OR lower(serial_number"
        results = seeded_meshdb.search("zzz-no-match", field=payload)
        # Full-text fallback with a non-matching query yields nothing; no injection widened it.
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# dashboard()
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboard:
    """dashboard() — aggregation stats for the dashboard."""

    def test_clusters_count(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() reports 4 clusters."""
        d = seeded_meshdb.dashboard()
        assert d["clusters"] == 4

    def test_records_count(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() reports 7 source records."""
        d = seeded_meshdb.dashboard()
        assert d["records"] == 7

    def test_multi_source(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() reports 2 multi-source clusters (c1 has 3, c2 has 2)."""
        d = seeded_meshdb.dashboard()
        assert d["multi_source"] == 2

    def test_sources_dict(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() sources dict has correct per-source record counts."""
        d = seeded_meshdb.dashboard()
        sources = d["sources"]
        assert sources["sp"] == 2
        assert sources["me"] == 1
        assert sources["fg"] == 1
        assert sources["ad"] == 1
        assert sources["zbx"] == 1
        assert sources["me_mdm"] == 1

    def test_coverage_keys(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() coverage dict has serial, mac, name, assigned_user keys."""
        d = seeded_meshdb.dashboard()
        coverage = d["coverage"]
        for key in ("serial", "mac", "name", "assigned_user"):
            assert key in coverage

    def test_top_clusters(self, seeded_meshdb: MeshDB) -> None:
        """dashboard() top_clusters sorted by source_count desc — first is c1."""
        d = seeded_meshdb.dashboard()
        top = d["top_clusters"]
        assert len(top) > 0
        assert top[0]["cluster_id"] == "c1"


# ═══════════════════════════════════════════════════════════════════════════════
# mesh_stats()
# ═══════════════════════════════════════════════════════════════════════════════


class TestMeshStats:
    """mesh_stats() — cross-source cluster statistics."""

    def test_totals(self, seeded_meshdb: MeshDB) -> None:
        """mesh_stats() reports total_clusters=4, total_records=7."""
        s = seeded_meshdb.mesh_stats()
        assert s["total_clusters"] == 4
        assert s["total_records"] == 7

    def test_singletons(self, seeded_meshdb: MeshDB) -> None:
        """mesh_stats() reports 2 singletons (c3 and c4)."""
        s = seeded_meshdb.mesh_stats()
        assert s["singletons"] == 2

    def test_multi_source(self, seeded_meshdb: MeshDB) -> None:
        """mesh_stats() reports multi_source=2 (4 total - 2 singletons)."""
        s = seeded_meshdb.mesh_stats()
        assert s["multi_source"] == 2

    def test_records_per_source(self, seeded_meshdb: MeshDB) -> None:
        """mesh_stats() records_per_source dict has correct counts."""
        s = seeded_meshdb.mesh_stats()
        rps = s["records_per_source"]
        assert rps["sp"] == 2
        assert rps["me"] == 1
        assert rps["fg"] == 1
        assert rps["ad"] == 1
        assert rps["zbx"] == 1
        assert rps["me_mdm"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# list_clusters()
# ═══════════════════════════════════════════════════════════════════════════════


class TestListClusters:
    """list_clusters() — paginated cluster listing."""

    def test_all(self, seeded_meshdb: MeshDB) -> None:
        """list_clusters(min_sources=1) returns all 4 clusters."""
        total, results = seeded_meshdb.list_clusters()
        assert total == 4
        assert len(results) == 4

    def test_multi_source_only(self, seeded_meshdb: MeshDB) -> None:
        """list_clusters(min_sources=2) returns c1 and c2 only."""
        total, results = seeded_meshdb.list_clusters(min_sources=2)
        ids = {r.cluster_id for r in results}
        assert ids == {"c1", "c2"}
        assert total == 2

    def test_by_source(self, seeded_meshdb: MeshDB) -> None:
        """list_clusters(source="sp") returns c1 and c3."""
        total, results = seeded_meshdb.list_clusters(source="sp")
        ids = {r.cluster_id for r in results}
        assert ids == {"c1", "c3"}
        assert total == 2

    def test_pagination(self, seeded_meshdb: MeshDB) -> None:
        """list_clusters(limit=2, offset=0) returns first page."""
        total, page1 = seeded_meshdb.list_clusters(limit=2, offset=0)
        assert total == 4
        assert len(page1) == 2
        # Sorted by source_count DESC then device_name: c1 (3), c2 (2)
        assert page1[0].cluster_id == "c1"
        assert page1[1].cluster_id == "c2"

    def test_source_filter_is_parameterized(self, seeded_meshdb: MeshDB) -> None:
        """A source value containing a quote is bound as a parameter, not injected — no crash, no match."""
        total, results = seeded_meshdb.list_clusters(source="sp' OR '1'='1")
        assert total == 0
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# anomalies() — singletons, unnamed, no-serial
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnomalies:
    """anomalies() — singletons, unnamed devices, missing serials."""

    def test_singletons_count(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() reports 2 singletons (c3, c4)."""
        result = seeded_meshdb.anomalies()
        assert result["singletons"] == 2

    def test_no_name_count(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() reports 1 unnamed device (c3)."""
        result = seeded_meshdb.anomalies()
        assert result["no_name"] == 1

    def test_no_serial_count(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() reports 1 device without serial number (c3)."""
        result = seeded_meshdb.anomalies()
        assert result["no_serial"] == 1

    def test_singleton_list_contains_c3_c4(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() singleton_list cluster_ids are c3 and c4."""
        result = seeded_meshdb.anomalies()
        ids = {i["cluster_id"] for i in result["singleton_list"]}
        assert ids == {"c3", "c4"}

    def test_no_name_list_contains_only_c3(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() no_name_list contains only c3 (unnamed)."""
        result = seeded_meshdb.anomalies()
        ids = {i["cluster_id"] for i in result["no_name_list"]}
        assert ids == {"c3"}

    def test_no_serial_list_contains_c3(self, seeded_meshdb: MeshDB) -> None:
        """anomalies() no_serial_list contains c3 (empty serial)."""
        result = seeded_meshdb.anomalies()
        ids = {i["cluster_id"] for i in result["no_serial_list"]}
        assert ids == {"c3"}

    def test_list_models_have_expected_keys(self, seeded_meshdb: MeshDB) -> None:
        """Each item in anomaly lists has cluster_id and device_name."""
        result = seeded_meshdb.anomalies()
        for lst_key in ("singleton_list", "no_name_list", "no_serial_list"):
            for item in result[lst_key]:
                assert "cluster_id" in item
                assert "device_name" in item

    def test_zombies_is_nonnegative_int(self, seeded_meshdb: MeshDB) -> None:
        """anomalies()['zombies'] is an int >= 0."""
        result = seeded_meshdb.anomalies()
        assert isinstance(result["zombies"], int)
        assert result["zombies"] >= 0

    def test_hardware_drift_is_nonnegative_int(self, seeded_meshdb: MeshDB) -> None:
        """anomalies()['hardware_drift'] is an int >= 0."""
        result = seeded_meshdb.anomalies()
        assert isinstance(result["hardware_drift"], int)
        assert result["hardware_drift"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# _resolve_cluster — 7-step resolution cascade
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve(seeded_meshdb, query: str) -> str | None:
    """Convenience wrapper that opens/closes a connection for resolve tests."""
    conn = seeded_meshdb._conn()
    try:
        return seeded_meshdb._resolve_cluster(conn, query)
    finally:
        conn.close()


class TestResolveCluster:
    """Tests for the 7-step _resolve_cluster cascade plus edge cases."""

    def test_resolve_by_cluster_id(self, seeded_meshdb: MeshDB) -> None:
        """Step 1 — exact cluster_id match returns self."""
        result = _resolve(seeded_meshdb, "c1")
        assert result == "c1"

    def test_resolve_by_device_name(self, seeded_meshdb: MeshDB) -> None:
        """Step 2 — case-insensitive device_name exact match."""
        result = _resolve(seeded_meshdb, "ws28")
        assert result == "c1"

    def test_resolve_by_serial(self, seeded_meshdb: MeshDB) -> None:
        """Step 3 — exact serial_number match (case-insensitive)."""
        result = _resolve(seeded_meshdb, "SN001")
        assert result == "c1"

    def test_resolve_by_mac_devices(self, seeded_meshdb: MeshDB) -> None:
        """Step 4 — raw MAC normalized then matched in devices.mac_address."""
        result = _resolve(seeded_meshdb, "aa:bb:cc:dd:ee:ff")
        assert result == "c1"

    def test_resolve_by_mac_source_records(self, seeded_meshdb: MeshDB) -> None:
        """Step 4 fallback — MAC found in source_records.mac_clean."""
        # 11:22:33:44:55:66 normalizes to 112233445566 — c2 in both tables
        result = _resolve(seeded_meshdb, "11:22:33:44:55:66")
        assert result == "c2"

    def test_resolve_mac_only_in_source_records(self, seeded_meshdb: MeshDB, tmp_path: Path) -> None:
        """Step 4 fallback — MAC exists only in source_records.mac_clean, not in devices."""
        import duckdb

        db_path = seeded_meshdb._path
        wconn = duckdb.connect(str(db_path))
        try:
            wconn.execute(
                "INSERT INTO source_records (cluster_id, source, mac_clean) VALUES (?, ?, ?)",
                ["c2", "test_src", "a1b2c3d4e5f6"],
            )
        finally:
            wconn.close()
        try:
            result = _resolve(seeded_meshdb, "a1:b2:c3:d4:e5:f6")
            assert result == "c2"
        finally:
            wconn = duckdb.connect(str(db_path))
            wconn.execute("DELETE FROM source_records WHERE mac_clean = 'a1b2c3d4e5f6'")
            wconn.close()

    def test_resolve_by_ip(self, seeded_meshdb: MeshDB) -> None:
        """Step 5 — IP address LIKE substring match in source_records."""
        result = _resolve(seeded_meshdb, "192.168.1.100")
        assert result == "c1"

    def test_resolve_by_user_substring(self, seeded_meshdb: MeshDB) -> None:
        """Step 6 — assigned_user LIKE substring match."""
        result = _resolve(seeded_meshdb, "jdoe")
        assert result == "c1"

    def test_resolve_by_fulltext(self, seeded_meshdb: MeshDB) -> None:
        """Step 7 — full-text fallback across 9 source_records fields."""
        # "OptiPlex" appears in c1's model column in source_records
        result = _resolve(seeded_meshdb, "OptiPlex")
        assert result == "c1"

    def test_resolve_miss(self, seeded_meshdb: MeshDB) -> None:
        """No match across any step returns None."""
        result = _resolve(seeded_meshdb, "nonexistent_xyz")
        assert result is None

    def test_resolve_prioritizes_source_count(self, seeded_meshdb: MeshDB) -> None:
        """When multiple clusters match, highest source_count wins."""
        # "SN00" reaches step 7 (LIKE on serial_number) and matches both:
        #   c1 serial "SN001" contains "SN00", c2 serial "SN002" contains "SN00"
        # c1 (source_count=3) beats c2 (source_count=2)
        result = _resolve(seeded_meshdb, "SN00")
        assert result == "c1"


# ═══════════════════════════════════════════════════════════════════════════════
# _build_story — DeviceStory construction from cluster_id
# ═══════════════════════════════════════════════════════════════════════════════


def _build(seeded_meshdb, cluster_id: str, query: str = "test_query"):
    """Convenience wrapper for _build_story tests."""
    conn = seeded_meshdb._conn()
    try:
        return seeded_meshdb._build_story(conn, cluster_id, query)
    finally:
        conn.close()


class TestBuildStory:
    """Tests for _build_story — consolidated view, records, edge cases."""

    def test_multi_source(self, seeded_meshdb: MeshDB) -> None:
        """c1 has 3 source records across 3 distinct sources."""
        story = _build(seeded_meshdb, "c1")
        assert story.record_count == 3
        assert story.source_count == 3
        assert sorted(story.sources) == ["fg", "me", "sp"]
        assert story.device_name == "ws28"
        assert story.cluster_id == "c1"
        assert story.query == "test_query"

    def test_singleton(self, seeded_meshdb: MeshDB) -> None:
        """c3 has exactly 1 source record from 1 source."""
        story = _build(seeded_meshdb, "c3")
        assert story.record_count == 1
        assert story.source_count == 1
        assert story.sources == ["sp"]

    def test_unnamed(self, seeded_meshdb: MeshDB) -> None:
        """c3 device_name is the sentinel '(unnamed)' string."""
        story = _build(seeded_meshdb, "c3")
        assert story.device_name == "(unnamed)"

    def test_consolidated(self, seeded_meshdb: MeshDB) -> None:
        """Consolidated dict has correct fields with non-empty values for c1."""
        story = _build(seeded_meshdb, "c1")
        c = story.consolidated

        assert c["serial_number"] == ["SN001"]
        assert c["mac_address"] == ["aabbccddeeff"]
        assert c["manufacturer"] == ["Dell"]
        assert c["model"] == ["OptiPlex 7080"]
        assert c["os"] == ["Windows 10"]
        assert c["assigned_user"] == ["jdoe"]
        assert c["ip_address"] == ["192.168.1.100"]
        assert c["name_clean"] == ["ws28"]
        # imei is empty for c1 — should not appear in consolidated
        assert "imei" not in c

    def test_records_sorted(self, seeded_meshdb: MeshDB) -> None:
        """Source records are returned sorted alphabetically by source name."""
        story = _build(seeded_meshdb, "c1")
        sources = [r.source for r in story.records]
        assert sources == ["fg", "me", "sp"]
        # Verify content of the first record (FG)
        assert story.records[0].source == "fg"
        assert story.records[0].name == "ws28"
        assert story.records[0].ip_address == "192.168.1.100"

    def test_nonexistent_cluster(self, seeded_meshdb: MeshDB) -> None:
        """Requesting a cluster_id that does not exist raises ValueError."""
        import pytest as pt

        conn = seeded_meshdb._conn()
        try:
            with pt.raises(ValueError, match="not found"):
                seeded_meshdb._build_story(conn, "nonexistent", "query")
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# _row_to_cluster_info — edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestRowToClusterInfoEdgeCases:
    """_row_to_cluster_info — defensive edge cases."""

    def test_string_sources_parsed(self, tmp_path: Path) -> None:
        """When sources column is a string (TEXT not TEXT[]), it is parsed into a list."""
        import duckdb

        db_path = tmp_path / "string_sources.duckdb"
        conn = duckdb.connect(str(db_path))
        try:
            conn.execute("""
                CREATE TABLE devices (
                    cluster_id TEXT, device_name TEXT, source_count BIGINT,
                    sources TEXT, serial_number TEXT, mac_address TEXT,
                    manufacturer TEXT, model TEXT, os TEXT, os_version TEXT,
                    asset_tag TEXT, assigned_user TEXT,
                    ip_address TEXT, imei TEXT, record_count BIGINT
                )
            """)
            conn.execute("""
                INSERT INTO devices VALUES ('c99', 'test-device', 2,
                    '[sp, me]', 'SN999', '', 'Dell', '', '', '', '', '', '', '', 2)
            """)
        finally:
            conn.close()
        from zentinull.api.db import MeshDB

        mesh = MeshDB(db_path)
        results = mesh.search("test-device")
        assert len(results) == 1
        assert results[0].sources == ["sp", "me"]


# ═══════════════════════════════════════════════════════════════════════════════
# lookup — public single-query entry point
# ═══════════════════════════════════════════════════════════════════════════════


class TestLookup:
    """Tests for lookup() — resolve + build story in one call."""

    def test_by_device_name(self, seeded_meshdb: MeshDB) -> None:
        """lookup("ws28") returns a DeviceStory with correct cluster_id and SOT."""
        result = seeded_meshdb.lookup("ws28")
        assert result is not None
        assert result.cluster_id == "c1"
        assert result.device_name == "ws28"
        assert result.source_count == 3
        assert len(result.records) == 3
        # SOT resolution produces per-field priority
        assert "sot" in result.model_dump()
        assert "name" in result.sot
        assert isinstance(result.sot["name"]["priority"], str)

    def test_miss(self, seeded_meshdb: MeshDB) -> None:
        """lookup for nonexistent query returns None."""
        result = seeded_meshdb.lookup("nonexistent_xyz")
        assert result is None

    def test_by_mac(self, seeded_meshdb: MeshDB) -> None:
        """lookup by raw MAC address resolves via normalization."""
        result = seeded_meshdb.lookup("aa:bb:cc:dd:ee:ff")
        assert result is not None
        assert result.cluster_id == "c1"


# ═══════════════════════════════════════════════════════════════════════════════
# batch_lookup — multi-query resolution in a single connection
# ═══════════════════════════════════════════════════════════════════════════════


class TestBatchLookup:
    """Tests for batch_lookup() — returns list[dict | None]."""

    def test_all_hits(self, seeded_meshdb: MeshDB) -> None:
        """All queries resolve — returns list of dicts."""
        results = seeded_meshdb.batch_lookup(["ws28", "dc01"])
        assert len(results) == 2
        assert isinstance(results[0], dict)
        assert isinstance(results[1], dict)
        assert results[0]["cluster_id"] == "c1"
        assert results[1]["cluster_id"] == "c2"

    def test_some_misses(self, seeded_meshdb: MeshDB) -> None:
        """Mixed hits and misses — dict for hits, None for misses."""
        results = seeded_meshdb.batch_lookup(["ws28", "nonexistent_xyz"])
        assert len(results) == 2
        assert isinstance(results[0], dict)
        assert results[0]["cluster_id"] == "c1"
        assert results[1] is None

    def test_all_misses(self, seeded_meshdb: MeshDB) -> None:
        """No results — list of Nones."""
        results = seeded_meshdb.batch_lookup(["zzz1", "zzz2"])
        assert results == [None, None]

    def test_empty(self, seeded_meshdb: MeshDB) -> None:
        """Empty input returns empty list."""
        results = seeded_meshdb.batch_lookup([])
        assert results == []


class TestBatchLookupErrors:
    """batch_lookup() — error handling when queries fail."""

    def test_empty_db_returns_none(self, tmp_path: Path) -> None:
        """batch_lookup on a DB with no tables catches exceptions and returns None."""
        import duckdb

        db_path = tmp_path / "empty.duckdb"
        duckdb.connect(str(db_path)).close()
        from zentinull.api.db import MeshDB

        mesh = MeshDB(db_path)
        results = mesh.batch_lookup(["anything"])
        assert results == [None]
