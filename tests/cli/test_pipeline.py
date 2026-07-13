"""Tests for zentinull.cli.pipeline — pipeline orchestrator functions.

Covers: run_ingest, run_export, run_splink, run_load, run_pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# run_ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunIngest:
    """run_ingest() calls each ingestor module and returns row counts.

    Covers lines 48-83 of cli/pipeline.py.
    """

    def _patch_ingestors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Monkeypatch all 6 ingestor .ingest() to return known counts without live APIs."""
        import zentinull.ingestors.ad as ad_mod
        import zentinull.ingestors.fortigate as fg_mod
        import zentinull.ingestors.manageengine as me_mod
        import zentinull.ingestors.servicedeskplus as sdp_mod
        import zentinull.ingestors.sharepoint as sp_mod
        import zentinull.ingestors.zabbix as zbx_mod

        monkeypatch.setattr(sp_mod, "ingest", lambda: 42)
        monkeypatch.setattr(me_mod, "ingest", lambda: 100)
        monkeypatch.setattr(fg_mod, "ingest", lambda: 200)
        monkeypatch.setattr(zbx_mod, "ingest", lambda: 75)
        monkeypatch.setattr(ad_mod, "ingest", lambda: 50)
        monkeypatch.setattr(sdp_mod, "ingest", lambda: 30)

    def test_runs_all_sources(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """When sources=None, all 6 sources run and return counts."""
        self._patch_ingestors(monkeypatch)
        from zentinull.cli.pipeline import SOURCE_MAP, run_ingest

        result = run_ingest()

        assert isinstance(result, dict)
        assert len(result) == len(SOURCE_MAP)
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
        self._patch_ingestors(monkeypatch)
        import zentinull.ingestors.sharepoint as sp_mod

        monkeypatch.setattr(sp_mod, "ingest", lambda: (_ for _ in ()).throw(ValueError("API down")))
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
        from zentinull.cli.pipeline import SOURCE_MAP, run_ingest

        result = run_ingest(skip_sources=None)

        assert len(result) == len(SOURCE_MAP)


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

        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")
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

        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")
        monkeypatch.setattr(pipeline_mod, "_run_export_fn", lambda: None)

        from zentinull.cli.pipeline import run_export

        with pytest.raises(FileNotFoundError, match="Export did not produce"):
            run_export()

    def test_empty_csv_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any
    ) -> None:
        """A CSV with only a header row returns 0."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")
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

        monkeypatch.setattr(pipeline_mod, "SPLINK_OUTPUT_DIR", tmp_path / "export" / "splink_output")
        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")

        from zentinull.cli.pipeline import run_load

        with pytest.raises(FileNotFoundError, match="not found"):
            run_load()

    def test_successful_load(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_status: Any) -> None:
        """When CSV exists, builds DuckDB with correct tables and returns device count."""
        import zentinull.cli.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "SPLINK_OUTPUT_DIR", tmp_path / "export" / "splink_output")
        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")

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

        monkeypatch.setattr(pipeline_mod, "SPLINK_OUTPUT_DIR", tmp_path / "export" / "splink_output")
        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")

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

        monkeypatch.setattr(pipeline_mod, "SPLINK_OUTPUT_DIR", tmp_path / "export" / "splink_output")
        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")
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

        monkeypatch.setattr(pipeline_mod, "SPLINK_OUTPUT_DIR", tmp_path / "export" / "splink_output")
        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")

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

        monkeypatch.setattr(pipeline_mod, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(data_dir / "fg.sqlite"))
        conn.execute(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY, mac TEXT, ip TEXT, hostname TEXT, manufacturer TEXT, model TEXT, os TEXT)"
        )
        conn.execute(
            "INSERT INTO clients VALUES (1, 'aa:bb:cc:dd:ee:ff', '10.0.0.1', 'ws28', 'Dell', 'Latitude', 'Windows')"
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


# ═══════════════════════════════════════════════════════════════════════════════
# run_incremental_load
# ═══════════════════════════════════════════════════════════════════════════════


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

        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")
        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")

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

        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")
        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")

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

        monkeypatch.setattr(pipeline_mod, "MESH_DB", tmp_path / "data" / "mesh.duckdb")
        monkeypatch.setattr(pipeline_mod, "CSV_DIR", tmp_path / "export" / "csv")

        from zentinull.cli.pipeline import run_incremental_load

        result = run_incremental_load(["fg"])
        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════════
# run_incremental_sync
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunIncrementalSync:
    """run_incremental_sync() orchestrates ingest → export → upsert for specific sources."""

    def test_calls_ingest_and_export(self, monkeypatch: pytest.MonkeyPatch, isolated_status: Any) -> None:
        """Runs ingest for specified sources, exports each, then upserts."""
        import zentinull.cli.pipeline as pipeline_mod

        calls: list[str] = []
        monkeypatch.setattr(
            pipeline_mod,
            "run_ingest",
            lambda sources=None: calls.append(f"ingest:{sources}") or {},
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
        fake_mesh = Path("/tmp/fake/mesh.duckdb")
        fake_mesh.parent.mkdir(parents=True, exist_ok=True)
        fake_mesh.touch()
        monkeypatch.setattr(pipeline_mod, "MESH_DB", fake_mesh)

        from zentinull.cli.pipeline import run_incremental_sync

        run_incremental_sync(["fg", "zbx"])

        assert "ingest:['fg', 'zbx']" in calls
        assert "export:fg" in calls
        assert "export:zbx" in calls
        assert "load:['fg', 'zbx']" in calls
