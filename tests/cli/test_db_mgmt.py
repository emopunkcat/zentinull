"""Tests for cli/db_mgmt.py: list_dbs, vacuum_dbs, check_dbs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from zentinull.config import ProjectPaths


def _make_paths(tmp_path: Path, project: str = "test") -> ProjectPaths:
    """Build a ProjectPaths rooted under *tmp_path* with data_dir created."""
    data_dir = tmp_path / "data"
    export_dir = tmp_path / "export"
    data_dir.mkdir(parents=True, exist_ok=True)
    return ProjectPaths(
        project=project,
        data_dir=data_dir,
        export_dir=export_dir,
        mesh_path=data_dir / "mesh.duckdb",
        status_file=data_dir / "status.json",
        log_file=data_dir / "pipeline.log",
        csv_dir=export_dir / "csv",
        splink_output_dir=export_dir / "splink_output",
        benchmarks_dir=tmp_path / ".benchmarks",
    )


# ---------------------------------------------------------------------------
# list_dbs
# ---------------------------------------------------------------------------


def test_list_dbs_empty_dir(tmp_path, monkeypatch, capsys):
    """No .sqlite files → prints warning message."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: _make_paths(tmp_path))

    from zentinull.cli.db_mgmt import list_dbs

    list_dbs()

    captured = capsys.readouterr()
    assert "No .sqlite files found" in captured.out


def test_list_dbs_with_one_db(tmp_path, monkeypatch, capsys):
    """One SQLite file with a table → output shows db name and row count."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    paths = _make_paths(tmp_path)
    data_dir = paths.data_dir
    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: paths)

    db_path = data_dir / "test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE devices (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO devices VALUES (1, 'test')")
    conn.execute("INSERT INTO devices VALUES (2, 'test2')")
    conn.commit()
    conn.close()

    from zentinull.cli.db_mgmt import list_dbs

    list_dbs()

    captured = capsys.readouterr()
    assert "test.sqlite" in captured.out
    assert "devices" in captured.out
    assert "2" in captured.out


def test_list_dbs_with_multiple_dbs(tmp_path, monkeypatch, capsys):
    """Two SQLite files → output includes both names."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    paths = _make_paths(tmp_path)
    data_dir = paths.data_dir
    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: paths)

    for name in ("alpha.sqlite", "beta.sqlite"):
        db_path = data_dir / name
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

    from zentinull.cli.db_mgmt import list_dbs

    list_dbs()

    captured = capsys.readouterr()
    assert "alpha.sqlite" in captured.out
    assert "beta.sqlite" in captured.out


# ---------------------------------------------------------------------------
# vacuum_dbs
# ---------------------------------------------------------------------------


def test_vacuum_dbs_empty_dir(tmp_path, monkeypatch, capsys):
    """No .sqlite files → prints warning message."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: _make_paths(tmp_path))

    from zentinull.cli.db_mgmt import vacuum_dbs

    vacuum_dbs()

    captured = capsys.readouterr()
    assert "No .sqlite files found" in captured.out


def test_vacuum_dbs_with_db(tmp_path, monkeypatch, capsys):
    """SQLite with data → VACUUM runs, before/after sizes printed."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    paths = _make_paths(tmp_path)
    data_dir = paths.data_dir
    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: paths)

    db_path = data_dir / "test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    from zentinull.cli.db_mgmt import vacuum_dbs

    vacuum_dbs()

    captured = capsys.readouterr()
    assert "test.sqlite" in captured.out
    # Should show "Before" and "After" columns (header)
    assert "Before" in captured.out
    assert "After" in captured.out


# ---------------------------------------------------------------------------
# check_dbs
# ---------------------------------------------------------------------------


def test_check_dbs_empty(tmp_path, monkeypatch, capsys):
    """No .sqlite files → prints warning message."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: _make_paths(tmp_path))

    from zentinull.cli.db_mgmt import check_dbs

    check_dbs()

    captured = capsys.readouterr()
    assert "No .sqlite files found" in captured.out


def test_check_dbs_passes(tmp_path, monkeypatch, capsys):
    """Valid SQLite DB → PASS in output."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    paths = _make_paths(tmp_path)
    data_dir = paths.data_dir
    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: paths)

    db_path = data_dir / "test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    from zentinull.cli.db_mgmt import check_dbs

    check_dbs()

    captured = capsys.readouterr()
    assert "PASS" in captured.out


def test_check_dbs_corrupt_file(tmp_path, monkeypatch, capsys):
    """Non-SQLite file renamed to .sqlite → shows FAIL."""
    import zentinull.cli.db_mgmt as db_mgmt_mod

    paths = _make_paths(tmp_path)
    data_dir = paths.data_dir
    monkeypatch.setattr(db_mgmt_mod, "get_paths", lambda: paths)

    db_path = data_dir / "corrupt.sqlite"
    db_path.write_text("not a database file")

    from zentinull.cli.db_mgmt import check_dbs

    check_dbs()

    captured = capsys.readouterr()
    assert "FAIL" in captured.out
