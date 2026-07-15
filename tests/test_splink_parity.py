"""Phase 4 acceptance gate — Splink parity test.

Verifies that the manifest-driven Splink runner produces clusters that cover
every cluster_id in the reference fixture, and that the ResolutionProfile knobs
match the reference settings.json.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from zentinull.manifest import load_manifest

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "splink_reference"
_LIVE_CLUSTERS = Path(__file__).resolve().parent.parent / "export" / "splink_output" / "clusters.csv"


def test_reference_clusters_covered_by_live() -> None:
    """Every cluster_id in reference clusters.csv appears in live clusters.csv."""
    if not _LIVE_CLUSTERS.exists():
        pytest.skip("run splink first — export/splink_output/clusters.csv not found")

    ref_ids: set[str] = set()
    with open(_FIXTURE_DIR / "clusters.csv", newline="") as f:
        for row in csv.DictReader(f):
            ref_ids.add(row["cluster_id"])

    live_ids: set[str] = set()
    with open(_LIVE_CLUSTERS, newline="") as f:
        for row in csv.DictReader(f):
            live_ids.add(row["cluster_id"])

    missing = ref_ids - live_ids
    assert not missing, (
        f"Live clusters.csv is missing {len(missing)} cluster_ids present in reference: {sorted(missing)[:10]}"
    )


def test_settings_json_matches_manifest_profile() -> None:
    """The 5 knobs in settings.json match the manifest ResolutionProfile values."""
    with open(_FIXTURE_DIR / "settings.json") as f:
        ref = json.load(f)

    profile = load_manifest().profiles["device"]

    assert profile.predict_threshold == ref["predict_threshold"]
    assert profile.cluster_threshold == ref["cluster_threshold"]
    assert list(profile.sweep_thresholds) == [float(t) for t in ref["sweep_thresholds"]]
    assert profile.u_max_pairs == ref["u_max_pairs"]
    assert profile.lambda_recall == ref["lambda_recall"]


def test_splink_runner_retains_all_profile_fields() -> None:
    """additional_columns_to_retain in splink_runner.py includes all profile.fields."""
    runner_path = Path(__file__).resolve().parent.parent / "src" / "zentinull" / "resolve" / "splink_runner.py"
    content = runner_path.read_text()

    profile = load_manifest().profiles["device"]
    # The runner uses `additional_columns_to_retain=list(profile.fields)` — verify it's present
    assert "additional_columns_to_retain=list(profile.fields)" in content, (
        "splink_runner.py must use additional_columns_to_retain=list(profile.fields)"
    )

    # Verify all profile fields are in the manifest (structural check — they flow through profile.fields at runtime)
    expected = set(profile.fields)
    assert expected, "profile.fields must not be empty"
