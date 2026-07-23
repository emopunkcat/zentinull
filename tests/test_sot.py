"""Unit tests for sot_resolve() — SOT priority resolution.

5 scenarios per plan:
- Primary source present → picks primary
- Primary missing, secondary present → picks secondary
- Both missing → picks best_effort (highest coverage wins)
- All missing → returns empty string with "none" priority
- Single source with value → picks it as best_effort
"""

from __future__ import annotations

from zentinull.manifest.types import ResolutionProfile
from zentinull.resolve.sot import sot_resolve


def test_primary_present_picks_primary() -> None:
    """Primary source has the value → picks it with 'primary' priority."""
    profile = ResolutionProfile(
        name="device",
        fields=("name",),
        derived={},
        comparisons=(),
        blocking=(),
        deterministic=(),
        em_passes=(),
        sot={"name": ("sp", "me")},
        predict_threshold=0.5,
        cluster_threshold=0.5,
    )
    source_records = {
        "sp": {"name": "WS28"},
        "me": {"name": "WS28-ME"},
    }
    result = sot_resolve(profile, source_records)
    value, source, priority = result["name"]
    assert value == "WS28"
    assert source == "sp"
    assert priority == "primary"


def test_primary_missing_secondary_present() -> None:
    """Primary absent, secondary has the value → picks secondary with 'secondary' priority."""
    profile = ResolutionProfile(
        name="device",
        fields=("serial_number",),
        derived={},
        comparisons=(),
        blocking=(),
        deterministic=(),
        em_passes=(),
        sot={"serial_number": ("sp", "me")},
        predict_threshold=0.5,
        cluster_threshold=0.5,
    )
    source_records = {
        "sp": {"name": "WS28"},
        # sp has no serial_number
        "me": {"serial_number": "SN001"},
    }
    result = sot_resolve(profile, source_records)
    value, source, priority = result["serial_number"]
    assert value == "SN001"
    assert source == "me"
    assert priority == "secondary"


def test_both_missing_picks_best_effort_by_coverage() -> None:
    """Both primary and secondary absent → picks best_effort from highest-coverage source."""
    profile = ResolutionProfile(
        name="device",
        fields=("name",),
        derived={},
        comparisons=(),
        blocking=(),
        deterministic=(),
        em_passes=(),
        sot={"name": ("sp", "")},
        predict_threshold=0.5,
        cluster_threshold=0.5,
    )
    source_records = {
        "sp": {"name": ""},  # empty — not picked
        "me": {"name": "WS28-ME"},
        "fg": {"name": "WS28-FG"},
    }
    coverage = {"sp": 0.55, "me": 0.80, "fg": 0.30}
    result = sot_resolve(profile, source_records, coverage=coverage)
    value, source, priority = result["name"]
    # me has coverage=0.80 — highest of the two with values (fg=0.30)
    assert source == "me"
    assert priority == "best_effort"
    assert value == "WS28-ME"


def test_all_missing_returns_empty_string() -> None:
    """No source has the field → returns ('', None, 'none')."""
    profile = ResolutionProfile(
        name="device",
        fields=("asset_tag",),
        derived={},
        comparisons=(),
        blocking=(),
        deterministic=(),
        em_passes=(),
        sot={"asset_tag": ("sp", "me")},
        predict_threshold=0.5,
        cluster_threshold=0.5,
    )
    source_records: dict[str, dict[str, str]] = {
        "sp": {},
        "me": {},
    }
    result = sot_resolve(profile, source_records)
    value, source, priority = result["asset_tag"]
    assert value == ""
    assert source is None
    assert priority == "none"


def test_single_source_picks_as_best_effort() -> None:
    """Only one source has the field, no primary/secondary set → picks as best_effort."""
    profile = ResolutionProfile(
        name="device",
        fields=("imei",),
        derived={},
        comparisons=(),
        blocking=(),
        deterministic=(),
        em_passes=(),
        sot={"imei": ("", "")},  # no primary or secondary
        predict_threshold=0.5,
        cluster_threshold=0.5,
    )
    source_records: dict[str, dict[str, str]] = {
        "me_mdm": {"imei": "356789012345678"},
    }
    result = sot_resolve(profile, source_records)
    value, source, priority = result["imei"]
    assert value == "356789012345678"
    assert source == "me_mdm"
    assert priority == "best_effort"
