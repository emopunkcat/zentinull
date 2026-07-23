"""Tests for Splink cluster validation (validate_clusters).

Uses tmp-file CSVs for input and monkeypatches PATHS.splink_output_dir
to isolate the output side-effect (cluster_annotations.csv).
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from zentinull.resolve import validate as _validate_module
from zentinull.resolve.validate import validate_clusters


def _write_clusters_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    """Write a minimal clusters CSV and return the path."""
    fieldnames = ["cluster_id", "serial_number", "name"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestValidateClusters:
    """Tests for validate_clusters() — flagging suspicious Splink decisions."""

    @pytest.fixture(autouse=True)
    def _isolate_output_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redirect PATHS.splink_output_dir to a temp dir for every test."""
        _v_paths = _validate_module.get_paths()
        monkeypatch.setattr(
            _validate_module,
            "get_paths",
            lambda: replace(_v_paths, splink_output_dir=tmp_path),
        )

    def test_serial_conflict(self, tmp_path: Path) -> None:
        """Cluster with 2 distinct non-empty serials → SERIAL_CONFLICT."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                {"cluster_id": "c1", "serial_number": "SN001", "name": "DeviceA"},
                {"cluster_id": "c1", "serial_number": "SN002", "name": "DeviceB"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert len(rows) == 1
        assert rows[0]["kind"] == "SERIAL_CONFLICT"
        assert rows[0]["cluster_id"] == "c1"
        assert "SN001" in rows[0]["values"]
        assert "SN002" in rows[0]["values"]
        assert rows[0]["field"] == "serial_number"

    def test_split_identity(self, tmp_path: Path) -> None:
        """Same serial in 2 different clusters → SPLIT_IDENTITY."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                {"cluster_id": "c1", "serial_number": "SN001", "name": "DeviceA"},
                {"cluster_id": "c2", "serial_number": "SN001", "name": "DeviceB"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert len(rows) == 1
        assert rows[0]["kind"] == "SPLIT_IDENTITY"
        assert rows[0]["values"] == "SN001"
        assert "c1" in rows[0]["detail"]
        assert "c2" in rows[0]["detail"]

    def test_clean_cluster_no_annotations(self, tmp_path: Path) -> None:
        """Cluster with a single serial → no annotations."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                {"cluster_id": "c1", "serial_number": "SN001", "name": "DeviceA"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert rows == []

    def test_empty_serials_no_annotations(self, tmp_path: Path) -> None:
        """Cluster with only empty/sentinel serials → no annotations."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                {"cluster_id": "c1", "serial_number": "", "name": "DeviceA"},
                {"cluster_id": "c1", "serial_number": "N/A", "name": "DeviceB"},
                {"cluster_id": "c1", "serial_number": "--", "name": "DeviceC"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert rows == []

    def test_serial_conflict_and_split_independent(self, tmp_path: Path) -> None:
        """Two independent clean clusters → no annotations."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                {"cluster_id": "c1", "serial_number": "SN001", "name": "DeviceA"},
                {"cluster_id": "c2", "serial_number": "SN002", "name": "DeviceB"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert rows == []

    def test_serial_conflict_and_split_same_data(self, tmp_path: Path) -> None:
        """Both SERIAL_CONFLICT and SPLIT_IDENTITY in one dataset."""
        csv_path = _write_clusters_csv(
            tmp_path / "clusters.csv",
            [
                # c1 has two distinct serials
                {"cluster_id": "c1", "serial_number": "SN001", "name": "DeviceA"},
                {"cluster_id": "c1", "serial_number": "SN002", "name": "DeviceB"},
                # SN002 also appears in c2
                {"cluster_id": "c2", "serial_number": "SN002", "name": "DeviceC"},
            ],
        )
        rows = validate_clusters(csv_path)
        assert len(rows) == 2
        kinds = {r["kind"] for r in rows}
        assert "SERIAL_CONFLICT" in kinds
        assert "SPLIT_IDENTITY" in kinds
