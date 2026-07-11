"""Tests for zentinull.cli.backup: create_backup() and _fmt_bytes()."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

# ── _fmt_bytes ────────────────────────────────────────────────────────────


class TestFmtBytes:
    """Unit tests for _fmt_bytes helper."""

    def test_zero_bytes(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(0) == "0.0 B"

    def test_bytes_below_1024(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(512) == "512.0 B"

    def test_kilobytes(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(1536) == "1.5 KB"

    def test_megabytes(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(1048576) == "1.0 MB"

    def test_gigabytes(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(1073741824) == "1.0 GB"

    def test_terabytes(self) -> None:
        from zentinull.cli.backup import _fmt_bytes

        assert _fmt_bytes(2_199_023_255_552) == "2.0 TB"


# ── create_backup ─────────────────────────────────────────────────────────


class TestCreateBackup:
    """Integration-style tests for create_backup using tmp_path isolation."""

    def test_writes_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given a SQLite DB in data/, when create_backup runs, then manifest.json is written with files dict."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _create_sqlite(data_dir / "src1.sqlite")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        manifest_path = backup_dir / "manifest.json"

        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "files" in manifest
        assert isinstance(manifest["files"], dict)
        assert manifest["files"]["src1.sqlite"]["copied"] is True

    def test_copies_sqlite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given a SQLite DB, when create_backup runs, then the DB file is copied to the backup dir."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db_path = _create_sqlite(data_dir / "src1.sqlite")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        copied_db = backup_dir / "src1.sqlite"

        assert copied_db.exists()
        assert copied_db.stat().st_size == db_path.stat().st_size

    def test_copies_duckdb(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given mesh.duckdb in data/, when create_backup runs, then it is copied to the backup dir."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mesh_path = data_dir / "mesh.duckdb"
        mesh_path.write_text("duckdb-content")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        copied_mesh = backup_dir / "mesh.duckdb"

        assert copied_mesh.exists()
        assert copied_mesh.read_text() == "duckdb-content"

    def test_missing_mesh_no_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given no mesh.duckdb, when create_backup runs, then it completes without error."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _create_sqlite(data_dir / "src1.sqlite")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        manifest = json.loads((backup_dir / "manifest.json").read_text())

        assert "mesh.duckdb" not in manifest["files"]
        assert backup_dir.exists()

    def test_missing_export_no_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given no export/ directory, when create_backup runs, then it completes without error."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _create_sqlite(data_dir / "src1.sqlite")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        manifest = json.loads((backup_dir / "manifest.json").read_text())

        # No export/ entries in manifest
        for key in manifest["files"]:
            assert not key.startswith("export/")
        assert backup_dir.exists()

    def test_empty_data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given an empty data/ dir, when create_backup runs, then manifest.json is still written with empty files dict."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup()
        manifest_path = backup_dir / "manifest.json"

        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "files" in manifest
        assert manifest["files"] == {}

    def test_custom_output_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Given a custom output_dir, when create_backup runs, then files go to that custom directory."""
        import zentinull.cli.backup as backup_mod

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _create_sqlite(data_dir / "src1.sqlite")

        monkeypatch.setattr(backup_mod, "ROOT", tmp_path)

        custom_dir = tmp_path / "custom_backups" / "my_backup"

        from zentinull.cli.backup import create_backup

        backup_dir = create_backup(output_dir=custom_dir)

        assert backup_dir == custom_dir
        assert (backup_dir / "src1.sqlite").exists()
        assert (backup_dir / "manifest.json").exists()


# ── Helpers ────────────────────────────────────────────────────────────────


def _create_sqlite(path: Path) -> Path:
    """Create a minimal SQLite database at path and return it."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return path
