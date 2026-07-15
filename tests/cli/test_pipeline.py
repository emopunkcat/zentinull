"""Tests for zentinull.cli.pipeline — pipeline orchestrator functions.

Covers: run_ingest, run_export, run_splink, run_load, run_pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zentinull.config import ProjectPaths


def _make_paths(tmp_path: Path) -> ProjectPaths:
    """Create a ProjectPaths instance pointing at tmp_path subdirectories."""
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


def _paths_from_data_dir(data_dir: Path) -> ProjectPaths:
    """Create a ProjectPaths instance from an existing data_dir."""
    export_dir = data_dir.parent / "export"
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
        benchmarks_dir=data_dir.parent / ".benchmarks",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# run_ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunIngest:
    """run_ingest() calls each ingestor module and returns row counts.

    Covers lines 48-83 of cli/pipeline.py.
    """

    def _patch_ingestors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch the adapter's runner.run_system so ingest is network-free and deterministic.

        run_ingest() delegates to ingest_adapter.run_ingest → runner.run_system(system_key, manifest),
        which returns a per-feed dict. We stub per-system counts keyed by the system's anchor feed.
        """
        import zentinull.ingest_adapter as adapter_mod

        counts = {"sp": 42, "me": 100, "fg": 200, "zbx": 75, "ad": 50, "sdp": 30}

        def _fake_run_system(system_key: str, manifest: Any, **kwargs: Any) -> dict[str, int]:
            return {f"{system_key}_feed": counts.get(system_key, 0)}

        monkeypatch.setattr(adapter_mod, "run_system", _fake_run_system)

    def test_runs_all_sources(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """When sources=None, all 6 sources run and return counts."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import _SOURCE_MAP, run_ingest

        result = run_ingest()

        assert isinstance(result, dict)
        assert len(result) == len(_SOURCE_MAP)
        for name, count in result.items():
            assert count >= 0, f"{name} failed with count {count}"

    def test_respects_sources_param(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """Given specific sources, only those run."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import run_ingest

        result = run_ingest(sources=["sp", "fg"])

        assert set(result.keys()) == {"SharePoint", "FortiGate"}
        assert result["SharePoint"] == 42
        assert result["FortiGate"] == 200

    def test_skip_sources(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """Given skip_sources, matching sources are excluded."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import run_ingest

        result = run_ingest(skip_sources=["ad", "sdp"])

        assert "Active Directory" not in result
        assert "ServiceDesk Plus" not in result
        assert "SharePoint" in result
        assert "FortiGate" in result

    def test_ingestor_error_returns_negative_one(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """When an ingestor raises, its row count is -1 and other sources continue."""
        import zentinull.ingest_adapter as adapter_mod

        def _raise_for_sp(system_key: str, manifest: Any, **kwargs: Any) -> dict[str, int]:
            if system_key == "sp":
                raise ValueError("API down")
            return {f"{system_key}_feed": 200}

        monkeypatch.setattr(adapter_mod, "run_system", _raise_for_sp)
        from zentinull.cli.pipeline import run_ingest

        result = run_ingest()

        assert result["SharePoint"] == -1
        assert result["FortiGate"] >= 0

    def test_unknown_source_skipped(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """Unknown source keys are silently skipped without error."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import run_ingest

        result = run_ingest(sources=["sp", "nonexistent"])

        assert "SharePoint" in result
        assert "nonexistent" not in result

    def test_empty_skip_sources(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """skip_sources=None runs all sources (no skip)."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import _SOURCE_MAP, run_ingest

        result = run_ingest(skip_sources=None)

        assert len(result) == len(_SOURCE_MAP)


# ═══════════════════════════════════════════════════════════════════════════════
# run_export
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunExport:
    """run_export() runs the export function and counts CSV rows.

    Covers lines 94-106 of cli/pipeline.py.
    """

    def test_successful_export(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When export succeeds and CSV exists, returns row count (minus header)."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))
        monkeypatch.setattr(pipeline_mod, "_run_export_fn", lambda: None)

        csv_dir = tmp_path / "export" / "csv"
        csv_dir.mkdir(parents=True)
        csv_path = csv_dir / "devices.csv"
        csv_path.write_text("source,name,serial\nfg,ws28,SER001\nad,WS28,SER001\n")

        from zentinull.cli.pipeline import run_export

        result = run_export()
        assert result == 2

    def test_csv_not_found_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When CSV is not produced by export, FileNotFoundError is raised."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))
        monkeypatch.setattr(pipeline_mod, "_run_export_fn", lambda: None)

        from zentinull.cli.pipeline import run_export

        with pytest.raises(FileNotFoundError, match="Export did not produce"):
            run_export()

    def test_empty_csv_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """A CSV with only a header row returns 0."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))
        monkeypatch.setattr(pipeline_mod, "_run_export_fn", lambda: None)

        csv_dir = tmp_path / "export" / "csv"
        csv_dir.mkdir(parents=True)
        csv_path = csv_dir / "devices.csv"
        csv_path.write_text("source,name\n")

        from zentinull.cli.pipeline import run_export

        result = run_export()
        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# run_splink
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunSplink:
    """run_splink() runs scripts/run_splink.py as a streaming subprocess.

    Covers lines 117-137 of cli/pipeline.py.
    """

    def test_script_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """When run_splink.py doesn't exist, FileNotFoundError is raised."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)

        from zentinull.cli.pipeline import run_splink

        with pytest.raises(FileNotFoundError, match="run_splink.py"):
            run_splink()

    def test_successful_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When script exists and streaming succeeds, completes without raising."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)
        monkeypatch.setattr(pipeline_mod, "run_streaming", lambda *a, **kw: None)

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "run_splink.py").write_text("# mock script")

        from zentinull.cli.pipeline import run_splink

        run_splink()  # should not raise

    def test_passes_threshold_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When threshold is given, it is passed as SPLINK_THRESHOLD env var."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)

        captured_env: dict[str, str] = {}

        def _capture_streaming(*args: Any, **kw: Any) -> None:
            captured_env.update(kw.get("env") or {})

        monkeypatch.setattr(pipeline_mod, "run_streaming", _capture_streaming)

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "run_splink.py").write_text("# mock")

        from zentinull.cli.pipeline import run_splink

        run_splink(threshold=-5)
        assert captured_env.get("SPLINK_THRESHOLD") == "-5"

    def test_no_threshold_no_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When threshold is None, no env var is set."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)

        captured_env: dict[str, str] = {}

        def _capture_streaming(*args: Any, **kw: Any) -> None:
            captured_env.update(kw.get("env") or {})

        monkeypatch.setattr(pipeline_mod, "run_streaming", _capture_streaming)

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "run_splink.py").write_text("# mock")

        from zentinull.cli.pipeline import run_splink

        run_splink()
        assert "SPLINK_THRESHOLD" not in captured_env

    def test_skip_training_logs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """With skip_training=True, function still runs (log only — feature not yet implemented)."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)
        monkeypatch.setattr(pipeline_mod, "run_streaming", lambda *a, **kw: None)

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "run_splink.py").write_text("# mock")

        from zentinull.cli.pipeline import run_splink

        run_splink(skip_training=True)  # should not raise

    def test_error_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """When streaming fails, RuntimeError is raised and failure recorded."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "ROOT", tmp_path)
        monkeypatch.setattr(
            pipeline_mod,
            "run_streaming",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("Splink crashed")),
        )

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        (script_dir / "run_splink.py").write_text("# mock")

        from zentinull.cli.pipeline import run_splink

        with pytest.raises(RuntimeError, match="Splink failed"):
            run_splink()


# ═══════════════════════════════════════════════════════════════════════════════
# run_load  (uses real DuckDB — no mocking of the DB layer)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunLoad:
    """run_load() builds DuckDB mesh from clusters.csv using temp-and-swap.

    Covers lines 149-203 of cli/pipeline.py.
    All tests use real DuckDB in a temp directory.
    """

    CSV_HEADER = (
        "cluster_id,source,name,name_clean,serial_number,"
        "mac_address,mac_clean,asset_tag,manufacturer,model,os,"
        "os_version,assigned_user,ip_address,imei,extra_attributes"
    )

    @staticmethod
    def _create_clusters_csv(root: Path, lines: list[str]) -> Path:
        """Create clusters.csv under ROOT/export/splink_output/ and return its path."""
        out_dir = root / "export" / "splink_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "clusters.csv"
        csv_path.write_text("\n".join(lines) + "\n")
        return csv_path

    def test_csv_not_found_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When clusters.csv doesn't exist, FileNotFoundError is raised."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        from zentinull.cli.pipeline import run_load

        with pytest.raises(FileNotFoundError, match="not found"):
            run_load()

    def test_successful_load(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When CSV exists, builds DuckDB with correct tables and returns device count."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        self._create_clusters_csv(
            tmp_path,
            [
                self.CSV_HEADER,
                "1,FG,ws28,ws28,SER001,aa:bb:cc:dd:ee:ff,aa:bb:cc:dd:ee:ff,,Dell,Latitude,Windows,,jdoe,10.0.0.1,,",
                "1,AD,WS28,ws28,SER001,,,,,Dell,Latitude,Windows,,jdoe,,,",
                "2,ZBX,srv-core,srv-core,SER002,,,,HP,ProLiant,Linux,,root,10.0.0.2,,",
            ],
        )

        from zentinull.cli.pipeline import run_load

        device_count = run_load()

        # Result
        assert device_count == 2

        # DuckDB file exists at final location
        mesh_path = tmp_path / "data" / "mesh.duckdb"
        assert mesh_path.exists()
        # Temp file cleaned up
        assert not (tmp_path / "data" / "mesh.duckdb.tmp").exists()

        # Verify content
        import duckdb

        conn = duckdb.connect(str(mesh_path), read_only=True)
        try:
            record_count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            assert record_count is not None
            assert record_count[0] == 3

            devices = conn.execute(
                "SELECT device_name, source_count, record_count FROM devices ORDER BY device_name"
            ).fetchall()
            assert len(devices) == 2

            # Cluster 2 (srv-core): single source
            assert devices[0][0] == "srv-core"
            assert devices[0][1] == 1  # source_count

            # Cluster 1 (ws28): two sources
            assert devices[1][0] == "ws28"
            assert devices[1][1] == 2  # source_count
            assert devices[1][2] == 2  # record_count
        finally:
            conn.close()

    def test_cleans_stale_temp_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """A stale mesh.duckdb.tmp from a previous failed run is cleaned up before loading."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        # Create a stale temp file first
        data_dir = tmp_path / "data"
        stale_path = data_dir / "mesh.duckdb.tmp"
        stale_path.write_text("stale data")

        self._create_clusters_csv(
            tmp_path,
            [
                self.CSV_HEADER,
                "1,FG,node1,node1,SER001,,,,,,,,,,,",
            ],
        )

        from zentinull.cli.pipeline import run_load

        device_count = run_load()
        assert device_count == 1

        # Stale file should be gone
        assert not stale_path.exists()

    def test_error_during_load_cleans_temp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """If SQL execution fails after connection opens, the temp DB file is removed."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))
        # Replace SOURCE_RECORDS_SQL with invalid SQL to trigger a failure after connect
        monkeypatch.setattr(pipeline_mod, "SOURCE_RECORDS_SQL", "INVALID SQL STATEMENT $$$")

        self._create_clusters_csv(
            tmp_path,
            [
                self.CSV_HEADER,
                "1,FG,node1,node1,SER001,,,,,,,,,,,",
            ],
        )

        import duckdb

        from zentinull.cli.pipeline import run_load

        with pytest.raises(duckdb.Error):
            run_load()

        # Temp file should be cleaned up
        assert not (tmp_path / "data" / "mesh.duckdb.tmp").exists()

    def test_existing_mesh_replaced(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """When a mesh.duckdb already exists, the new load replaces it atomically."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        # Create an old mesh with a marker table
        import duckdb

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        old_mesh = data_dir / "mesh.duckdb"
        old_conn = duckdb.connect(str(old_mesh))
        old_conn.execute("CREATE TABLE old_data (x INTEGER)")
        old_conn.execute("INSERT INTO old_data VALUES (1)")
        old_conn.close()

        self._create_clusters_csv(
            tmp_path,
            [
                self.CSV_HEADER,
                "1,FG,node1,node1,SER001,,,,,,,,,,,",
            ],
        )

        from zentinull.cli.pipeline import run_load

        device_count = run_load()
        assert device_count == 1

        # Verify old mesh was replaced: new tables exist, old table doesn't
        new_conn = duckdb.connect(str(old_mesh), read_only=True)
        try:
            tables = [r[0] for r in new_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert "old_data" not in tables
            assert "devices" in tables
            assert "source_records" in tables
        finally:
            new_conn.close()

    def test_load_zbx_items_into_metrics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """Zabbix items from zbx.sqlite are loaded into the metrics table during run_load."""
        import sqlite3

        # Create zbx.sqlite with hosts and items tables
        zbx_dir = tmp_path / "data"
        zbx_dir.mkdir(parents=True, exist_ok=True)
        zbx_db = zbx_dir / "zbx.sqlite"
        conn = sqlite3.connect(str(zbx_db))
        conn.execute(
            "CREATE TABLE hosts (id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, "
            "raw_json TEXT NOT NULL, raw_hash TEXT NOT NULL, "
            "remote_updated_at TEXT, fetched_at TEXT)"
        )
        conn.execute(
            "INSERT INTO hosts (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            ("101", '{"hostid":"101","host":"srv-core","name":"srv-core"}', "h1"),
        )
        conn.execute(
            "INSERT INTO hosts (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            ("102", '{"hostid":"102","host":"ws30","name":"ws30"}', "h2"),
        )
        conn.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, "
            "raw_json TEXT NOT NULL, raw_hash TEXT NOT NULL, "
            "remote_updated_at TEXT, fetched_at TEXT)"
        )
        conn.execute(
            "INSERT INTO items (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            (
                "1",
                '{"itemid":"1","hostid":"101","name":"CPU Load","key_":"system.cpu.load","value_type":"0","units":"%","lastvalue":"0.75","lastclock":"1700000000","prevvalue":"0.70"}',
                "aaa",
            ),
        )
        conn.execute(
            "INSERT INTO items (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            (
                "2",
                '{"itemid":"2","hostid":"101","name":"Memory Usage","key_":"vm.memory.used","value_type":"0","units":"GB","lastvalue":"16.5","lastclock":"1700000000","prevvalue":"16.2"}',
                "bbb",
            ),
        )
        # Item for ws30 — should not be found (no zbx record in cluster CSV)
        conn.execute(
            "INSERT INTO items (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            (
                "3",
                '{"itemid":"3","hostid":"102","name":"Disk Free","key_":"vfs.fs.size","value_type":"3","units":"B","lastvalue":"","lastclock":"1700000000","prevvalue":""}',
                "ccc",
            ),
        )
        # Item with non-numeric value (text_value path)
        conn.execute(
            "INSERT INTO items (source_id, raw_json, raw_hash) VALUES (?, ?, ?)",
            (
                "4",
                '{"itemid":"4","hostid":"101","name":"Status","key_":"system.status","value_type":"1","units":"","lastvalue":"OK","lastclock":"1700000000","prevvalue":""}',
                "ddd",
            ),
        )
        conn.commit()
        conn.close()

        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        # CSV with source_id so the primary hostid→source_id lookup works
        header_with_id = (
            "cluster_id,source,source_id,name,name_clean,serial_number,"
            "mac_address,mac_clean,asset_tag,manufacturer,model,os,"
            "os_version,assigned_user,ip_address,imei,extra_attributes"
        )
        self._create_clusters_csv(
            tmp_path,
            [
                header_with_id,
                "1,FG,fg1,ws28,ws28,SER001,aa:bb:cc:dd:ee:ff,aabbccddeeff,,Dell,Latitude,Windows,,jdoe,10.0.0.1,,",
                "2,ZBX,101,srv-core,srv-core,SER002,,,,HP,ProLiant,Linux,,root,10.0.0.2,,",
            ],
        )

        from zentinull.cli.pipeline import run_load

        device_count = run_load()
        assert device_count == 2

        mesh_path = tmp_path / "data" / "mesh.duckdb"
        assert mesh_path.exists()

        import duckdb

        d_conn = duckdb.connect(str(mesh_path), read_only=True)
        try:
            # Verify metrics table has Zabbix items loaded
            metric_rows = d_conn.execute(
                "SELECT metric_name, value, text_value, tags, source, cluster_id FROM metrics ORDER BY metric_name"
            ).fetchall()
            assert len(metric_rows) == 3, f"Expected 3 metrics, got {len(metric_rows)}"

            # CPU Load — numeric value, has tags
            cpu = metric_rows[0]
            assert cpu[0] == "CPU Load"
            assert cpu[1] == 0.75  # value
            assert cpu[2] == ""  # text_value
            assert isinstance(cpu[3], list)
            tags_str = " ".join(str(t) for t in cpu[3])
            assert "key=system.cpu.load" in tags_str
            assert "value_type=0" in tags_str
            assert "units=%" in tags_str
            assert cpu[4] == "zbx"  # source
            assert cpu[5] == "2"  # cluster_id

            # Memory Usage — numeric value
            mem = metric_rows[1]
            assert mem[0] == "Memory Usage"
            assert mem[1] == 16.5
            assert mem[2] == ""

            # Status — text_value path (non-numeric lastvalue)
            status = metric_rows[2]
            assert status[0] == "Status"
            assert status[1] is None
            assert status[2] == "OK"

            # Item for ws30 (hostid=102) should NOT be loaded — no zbx source record in CSV
            names = [r[0] for r in metric_rows]
            assert "Disk Free" not in names

            # Verify recorded_at was correctly parsed
            ts_rows = d_conn.execute("SELECT recorded_at FROM metrics WHERE metric_name = 'CPU Load'").fetchall()
            assert len(ts_rows) == 1
            assert ts_rows[0][0] is not None
        finally:
            d_conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# run_pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunPipeline:
    """run_pipeline() orchestrates all four stages in order.

    Covers lines 219-233 of cli/pipeline.py.
    """

    def test_full_pipeline(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """All four stages run in sequence when no skip flags are set."""
        import zentinull.cli.pipeline as pipeline_mod

        stages: list[str] = []
        monkeypatch.setattr(
            pipeline_mod,
            "run_ingest",
            lambda sources=None, skip_sources=None: stages.append("ingest") or {"default": 1},
        )
        monkeypatch.setattr(pipeline_mod, "run_export", lambda: stages.append("export") or 5)
        monkeypatch.setattr(pipeline_mod, "run_splink", lambda *a, **kw: stages.append("splink"))
        monkeypatch.setattr(pipeline_mod, "run_load", lambda: stages.append("load") or 3)

        from zentinull.cli.pipeline import run_pipeline

        run_pipeline()
        assert stages == ["ingest", "export", "splink", "load"]

    def test_skip_ingest(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """With skip_ingest=True, the ingest stage is skipped."""
        import zentinull.cli.pipeline as pipeline_mod

        stages: list[str] = []
        monkeypatch.setattr(pipeline_mod, "run_ingest", lambda sources=None, skip_sources=None: stages.append("ingest"))
        monkeypatch.setattr(pipeline_mod, "run_export", lambda: stages.append("export") or 5)
        monkeypatch.setattr(pipeline_mod, "run_splink", lambda *a, **kw: stages.append("splink"))
        monkeypatch.setattr(pipeline_mod, "run_load", lambda: stages.append("load") or 3)

        from zentinull.cli.pipeline import run_pipeline

        run_pipeline(skip_ingest=True)
        assert stages == ["export", "splink", "load"]

    def test_passes_sources_to_ingest(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """The sources and skip_sources parameters are forwarded to run_ingest."""
        import zentinull.cli.pipeline as pipeline_mod

        captured: dict[str, object] = {}

        def _capture_ingest(sources: list[str] | None = None, skip_sources: list[str] | None = None) -> dict[str, int]:
            captured["sources"] = sources
            captured["skip_sources"] = skip_sources
            return {}

        monkeypatch.setattr(pipeline_mod, "run_ingest", _capture_ingest)
        monkeypatch.setattr(pipeline_mod, "run_export", lambda: 0)
        monkeypatch.setattr(pipeline_mod, "run_splink", lambda *a, **kw: None)
        monkeypatch.setattr(pipeline_mod, "run_load", lambda: 0)

        from zentinull.cli.pipeline import run_pipeline

        run_pipeline(sources=["sp", "fg"], skip_sources=["ad"])
        assert captured.get("sources") == ["sp", "fg"]
        assert captured.get("skip_sources") == ["ad"]


# ═══════════════════════════════════════════════════════════════════════════════
# export_source
# ═══════════════════════════════════════════════════════════════════════════════


class TestExportSource:
    """export_source() exports a single source to its own CSV file."""

    CSV_HEADER = (
        "source,source_id,name,name_clean,serial_number,"
        "mac_address,mac_clean,asset_tag,manufacturer,model,os,"
        "os_version,assigned_user,ip_address,imei,extra_attributes"
    )

    def test_exports_single_source(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """Exports only the specified source to a per-source CSV."""
        import sqlite3

        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(data_dir / "fg.sqlite"))
        conn.execute(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY, source_id TEXT, raw_json TEXT, raw_hash TEXT, remote_updated_at TEXT, fetched_at TEXT)"
        )
        conn.execute(
            "INSERT INTO clients (id, source_id, raw_json, raw_hash) VALUES (1, 'aa:bb:cc:dd:ee:ff', ?, 'hash1')",
            (
                '{"mac": "aa:bb:cc:dd:ee:ff", "hostname": "ws28", "ipv4_address": "10.0.0.1", "manufacturer": "Dell", "hardware_family": "Latitude", "os_name": "Windows"}',
            ),
        )
        conn.commit()
        conn.close()

        from zentinull.cli.pipeline import export_source

        count = export_source("fg")

        assert count == 1
        csv_path = tmp_path / "export" / "csv" / "fg.csv"
        assert csv_path.exists()

        import csv

        with open(csv_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["source"] == "fg"
        assert rows[0]["name"] == "ws28"

    def test_unknown_source_raises(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """Raises ValueError for unknown source key."""
        from zentinull.cli.pipeline import export_source

        with pytest.raises(ValueError, match="Unknown source key"):
            export_source("nonexistent")


class TestRunIncrementalLoad:
    """run_incremental_load() upserts per-source CSVs into DuckDB."""

    CSV_HEADER = (
        "source,source_id,name,name_clean,serial_number,"
        "mac_address,mac_clean,asset_tag,manufacturer,model,os,"
        "os_version,assigned_user,ip_address,imei,extra_attributes"
    )

    def _create_mesh(self, root: Path, records: list[str]) -> Path:
        """Create a mesh.duckdb with source_records table."""
        import duckdb

        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        mesh_path = data_dir / "mesh.duckdb"

        conn = duckdb.connect(str(mesh_path))
        conn.execute("""
            CREATE TABLE source_records (
                cluster_id VARCHAR, source VARCHAR, source_id VARCHAR,
                name VARCHAR, name_clean VARCHAR, serial_number VARCHAR,
                mac_address VARCHAR, mac_clean VARCHAR, asset_tag VARCHAR,
                manufacturer VARCHAR, model VARCHAR, os VARCHAR,
                os_version VARCHAR, assigned_user VARCHAR, ip_address VARCHAR,
                imei VARCHAR, extra_attributes VARCHAR
            )
        """)
        for rec in records:
            cols = "cluster_id,source,source_id,name,name_clean,serial_number,mac_address,mac_clean,asset_tag,manufacturer,model,os,os_version,assigned_user,ip_address,imei,extra_attributes"
            placeholders = ",".join(["?"] * 17)
            conn.execute(f"INSERT INTO source_records ({cols}) VALUES ({placeholders})", rec.split(","))
        conn.execute("CREATE TABLE devices AS SELECT * FROM source_records LIMIT 0")
        conn.close()
        return mesh_path

    def _create_source_csv(self, root: Path, source_key: str, rows: list[str]) -> Path:
        """Create a per-source CSV for import."""
        import csv

        csv_dir = root / "export" / "csv"
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_path = csv_dir / f"{source_key}.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.CSV_HEADER.split(","))
            for row in rows:
                writer.writerow(row.split(","))
        return csv_path

    def test_upserts_new_records(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """New records from CSV are inserted into source_records."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        self._create_mesh(tmp_path, [])
        self._create_source_csv(
            tmp_path,
            "fg",
            ["fg,mac1,ws28,ws28,,aa:bb:cc:dd:ee:ff,,,,,,,10.0.0.1,,,vlan=100"],
        )

        from zentinull.cli.pipeline import run_incremental_load

        run_incremental_load(["fg"])

        import duckdb

        conn = duckdb.connect(str(tmp_path / "data" / "mesh.duckdb"), read_only=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            assert count[0] == 1
            row = conn.execute("SELECT source, source_id, name FROM source_records").fetchone()
            assert row[0] == "fg"
            assert row[1] == "mac1"
            assert row[2] == "ws28"
        finally:
            conn.close()

    def test_updates_existing_records(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """Existing records (same source + source_id) are updated, not duplicated."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        self._create_mesh(
            tmp_path,
            ["cluster1,fg,mac1,ws28,ws28,,aa:bb:cc:dd:ee:ff,,,,,,,10.0.0.1,,,"],
        )
        self._create_source_csv(
            tmp_path,
            "fg",
            ["fg,mac1,ws28-new,ws28-new,,aa:bb:cc:dd:ee:ff,,,,,,,,10.0.0.2,,,"],
        )

        from zentinull.cli.pipeline import run_incremental_load

        run_incremental_load(["fg"])

        import duckdb

        conn = duckdb.connect(str(tmp_path / "data" / "mesh.duckdb"), read_only=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            assert count[0] == 1
            row = conn.execute("SELECT name, ip_address FROM source_records").fetchone()
            assert row[0] == "ws28-new"
            assert row[1] == "10.0.0.2"
        finally:
            conn.close()

    def test_returns_zero_when_mesh_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """When mesh.duckdb doesn't exist, returns 0 without error."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "PATHS", _make_paths(tmp_path))

        from zentinull.cli.pipeline import run_incremental_load

        result = run_incremental_load(["fg"])
        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# run_incremental_sync
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunIncrementalSync:
    """run_incremental_sync() orchestrates ingest → export → upsert for specific sources."""

    def test_calls_ingest_and_export(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """Runs ingest for specified sources, exports each, then upserts."""
        import zentinull.cli.pipeline as pipeline_mod

        calls: list[str] = []

        # Mock runner.run_system (lazy import inside run_incremental_sync)
        import zentinull.ingest.runner as runner_mod

        monkeypatch.setattr(
            runner_mod,
            "run_system",
            lambda system_key, manifest=None, **kw: calls.append(f"ingest:{system_key}") or {},
        )

        monkeypatch.setattr(
            pipeline_mod,
            "export_source",
            lambda s: calls.append(f"export:{s}") or 0,
        )
        monkeypatch.setattr(
            pipeline_mod,
            "run_incremental_load",
            lambda s: calls.append(f"load:{s}") or 0,
        )

        # Provide minimal manifest globals so export loop functions
        monkeypatch.setattr(pipeline_mod, "_SOURCE_TO_TABLES", {"fg": ["fg_clients"], "zbx": ["zbx_hosts"]})
        monkeypatch.setattr(pipeline_mod, "_get_manifest", lambda: None)  # type: ignore[method-assign]

        paths = _make_paths(tmp_path)
        paths.mesh_path.parent.mkdir(parents=True, exist_ok=True)
        paths.mesh_path.touch()
        monkeypatch.setattr(pipeline_mod, "PATHS", paths)

        from zentinull.cli.pipeline import run_incremental_sync

        run_incremental_sync(["fg", "zbx"])

        assert "ingest:fg" in calls
        assert "ingest:zbx" in calls
        assert "export:fg" in calls
        assert "export:zbx" in calls
        assert "load:['fg', 'zbx']" in calls

    def test_double_run_upserts_zero_when_unchanged(self, monkeypatch: pytest.MonkeyPatch, temp_data_dir: Path) -> None:
        """Second incremental run writes 0 when raw_hash unchanged (Phase 3 acceptance)."""
        from zentinull.ingest import runner
        from zentinull.ingest.strategies import REGISTRY
        from zentinull.manifest.types import Auth, Feed, Manifest, Role, System

        # Build minimal manifest with a test strategy
        manifest = Manifest(
            project="test",
            systems={"zbx": System(auth=Auth("none"), strategy="test_fixed", label="Zabbix")},
            feeds={
                "zbx_hosts": Feed(
                    system="zbx",
                    endpoint={"url": "http://fake/api"},
                    role=Role.ANCHOR,
                    store="hosts",
                    id_path="hostid",
                ),
            },
            profiles={},
        )

        fixed_items = [{"hostid": "101", "hostname": "srv-core"}]

        def test_fixed_fetch(endpoint: object, auth: object) -> list[dict]:
            return fixed_items

        monkeypatch.setitem(REGISTRY, "test_fixed", test_fixed_fetch)

        # Point all PATHS references to temp
        import zentinull.config as config_mod
        import zentinull.ingestors.base as base_mod

        paths = _paths_from_data_dir(temp_data_dir)
        monkeypatch.setattr(config_mod, "PATHS", paths)
        monkeypatch.setattr(base_mod, "PATHS", paths)

        # First run — inserts rows
        first_results = runner.run_system("zbx", manifest, incremental=True)
        assert "zbx_hosts" in first_results
        assert first_results["zbx_hosts"] > 0, f"First run wrote 0: {first_results}"

        # Verify the raw-store table exists and has data
        import sqlite3

        zbx_db = temp_data_dir / "zbx.sqlite"
        assert zbx_db.exists()
        conn = sqlite3.connect(str(zbx_db))
        row = conn.execute("SELECT COUNT(*) FROM hosts").fetchone()
        assert row is not None and row[0] > 0
        conn.close()

        # Second run — same data, raw_hash unchanged → upsert skips all
        second_results = runner.run_system("zbx", manifest, incremental=True)
        assert "zbx_hosts" in second_results
        assert second_results["zbx_hosts"] == 0, f"Second run wrote {second_results['zbx_hosts']}, expected 0"
