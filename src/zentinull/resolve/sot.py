"""SOT resolution — picks canonical value per field from per-source records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..manifest.types import ResolutionProfile


def sot_resolve(
    profile: ResolutionProfile,
    source_records: dict[str, dict[str, Any]],
    coverage: Mapping[str, float] | None = None,
) -> dict[str, tuple[str, str | None, str]]:
    """Resolve canonical values per field.

    Args:
        profile: ResolutionProfile with sot dict.
        source_records: {source_key: {field: value}} per source in the cluster.

    Returns:
        {field: (value, source_tag, priority)}
        priority: "primary" | "secondary" | "best_effort"
    """
    result: dict[str, tuple[str, str | None, str]] = {}
    for field, (primary, secondary) in profile.sot.items():
        # Primary
        val = source_records.get(primary, {}).get(field)
        if val:
            result[field] = (str(val), primary, "primary")
            continue
        # Secondary
        if secondary:
            val = source_records.get(secondary, {}).get(field)
            if val:
                result[field] = (str(val), secondary, "secondary")
                continue
        # Best effort — highest-coverage source first (deterministic),
        # falling back to caller order when no coverage map is given.
        # Pass {k: s.coverage for k, s in manifest.systems.items()}.
        cov = coverage or {}
        for src in sorted(source_records, key=lambda s: -cov.get(s, 0.0)):
            val = source_records[src].get(field)
            if val:
                result[field] = (str(val), src, "best_effort")
                break
        else:
            result[field] = ("", None, "none")
    return result
