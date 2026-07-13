"""Shared pytest fixtures for CLI module tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a data/ subdirectory inside tmp_path and return its Path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def temp_status_file(tmp_path: Path) -> Path:
    """Create a minimal status.json inside a tmp_path data/ directory.

    Returns the Path to the status file.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_file = data_dir / "status.json"
    status_file.write_text(json.dumps({"stages": {}, "freshness": {}}))
    return status_file


@pytest.fixture
def isolated_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Monkeypatch status.py module-level path to isolate fixture from real data/.

    Patches zentinull.cli.status.STATUS_FILE so status reads/writes
    target tmp_path instead of the project root.
    """
    import zentinull.cli.status as status_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(status_mod, "STATUS_FILE", data_dir / "status.json")


@pytest.fixture
def temp_sqlite_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite database inside tmp_path/data.

    Contains a single table 'test_table' with one row of dummy data.
    Returns the data directory Path.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO test_table (id, name) VALUES (1, 'test_row')")
    conn.commit()
    conn.close()
    return data_dir
