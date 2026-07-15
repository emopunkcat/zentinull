"""Tests for multi-project path isolation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zentinull.config import ROOT, resolve_paths


def test_two_projects_have_disjoint_data_dirs():
    """resolve_paths('project_a') and resolve_paths('project_b') have different data_dirs."""
    paths_a = resolve_paths("project_a")
    paths_b = resolve_paths("project_b")
    assert paths_a.data_dir != paths_b.data_dir
    assert paths_a.mesh_path != paths_b.mesh_path


def test_project_files_do_not_leak(tmp_path: Path) -> None:
    """Files written in project A's data_dir do not appear in project B's data_dir."""
    # Use tmp_path-based project names to avoid polluting real project dirs
    paths_a = resolve_paths("test_isolation_a")
    paths_b = resolve_paths("test_isolation_b")

    # Create project A's data dir and write a SQLite DB + status.json
    paths_a.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = paths_a.data_dir / "test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    status_path = paths_a.data_dir / "status.json"
    status_path.write_text(json.dumps({"stage": "ingest", "status": "done"}))

    # Assert project A's files exist
    assert db_path.exists()
    assert status_path.exists()

    # Assert project B's data_dir does not contain project A's files
    if paths_b.data_dir.exists():
        assert not (paths_b.data_dir / "test.sqlite").exists()
        assert not (paths_b.data_dir / "status.json").exists()

    # Cleanup
    import shutil

    shutil.rmtree(paths_a.data_dir.parent.parent, ignore_errors=True)
    if paths_b.data_dir.exists():
        shutil.rmtree(paths_b.data_dir.parent.parent, ignore_errors=True)


def test_default_project_uses_root_data():
    """Default project paths are under ROOT/data, not ROOT/projects/default/."""
    paths = resolve_paths("default")
    assert "projects" not in str(paths.data_dir)
    assert paths.data_dir == ROOT / "data"
