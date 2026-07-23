"""Tests for ProjectPaths dataclass and resolve_paths()."""

from __future__ import annotations

from zentinull.config import ROOT, get_paths, resolve_paths


def test_default_project_paths_byte_identical():
    """ZENTINULL_PROJECT=default -> resolved paths match current constants."""
    paths = resolve_paths("default")
    assert str(paths.data_dir) == str(ROOT / "data")
    assert str(paths.export_dir) == str(ROOT / "export")
    assert str(paths.mesh_path) == str(ROOT / "data" / "mesh.duckdb")
    assert str(paths.status_file) == str(ROOT / "data" / "status.json")
    assert str(paths.log_file) == str(ROOT / "data" / "pipeline.log")
    assert str(paths.csv_dir) == str(ROOT / "export" / "csv")
    assert str(paths.splink_output_dir) == str(ROOT / "export" / "splink_output")


def test_non_default_project_paths():
    """resolve_paths('demo') -> projects/demo/state/..."""
    paths = resolve_paths("demo")
    base = ROOT / "projects" / "demo" / "state"
    assert paths.data_dir == base / "data"
    assert paths.mesh_path == base / "data" / "mesh.duckdb"
    assert paths.export_dir == base / "export"


def test_paths_singleton_matches_default():
    """get_paths() matches resolve_paths('default')."""
    default = resolve_paths("default")
    assert get_paths().data_dir == default.data_dir
    assert get_paths().mesh_path == default.mesh_path


def test_benchmarks_dir_global_for_all_projects():
    """Benchmarks dir is ROOT/.benchmarks regardless of project."""
    default = resolve_paths("default")
    demo = resolve_paths("demo")
    assert default.benchmarks_dir == ROOT / ".benchmarks"
    assert demo.benchmarks_dir == ROOT / ".benchmarks"
