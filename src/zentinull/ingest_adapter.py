"""Ingest adapter — bridges manifest systems to strategy-based runner.

Phase 3: delegates all ingest to the strategy-based runner.
Legacy per-source ingestor modules no longer imported here.
"""

from __future__ import annotations

from .ingest.runner import run_system
from .logging_config import get_logger
from .manifest import Manifest

log = get_logger("ingest.adapter")


def run_ingest(
    manifest: Manifest,
    sources: list[str] | None = None,
    skip_sources: list[str] | None = None,
) -> dict[str, int]:
    """Run ingestors for the given manifest.

    Args:
        manifest: The loaded manifest.
        sources: If provided, only run these system keys.
        skip_sources: If provided, skip these system keys.

    Returns:
        Dict mapping system key → total rows across that system's feeds.
    """
    systems_to_run = [s for s in sources if s in manifest.systems] if sources else list(manifest.systems.keys())

    if skip_sources:
        systems_to_run = [s for s in systems_to_run if s not in skip_sources]

    results: dict[str, int] = {}
    for system_key in systems_to_run:
        if system_key not in manifest.systems:
            log.warning({"event": "no_system_in_manifest", "system": system_key})
            results[system_key] = 0
            continue

        log.info({"event": "ingesting", "system": system_key})
        try:
            per_feed = run_system(system_key, manifest)
            results[system_key] = sum(per_feed.values())
            log.info({"event": "ingested", "system": system_key, "rows": results[system_key]})
        except Exception as e:
            log.error({"event": "ingest_failed", "system": system_key, "error": str(e)})
            results[system_key] = -1

    return results


def get_system_label(manifest: Manifest, system_key: str) -> str:
    """Get display label for a system."""
    system = manifest.systems.get(system_key)
    if system and system.label:
        return system.label
    return system_key


def get_all_system_labels(manifest: Manifest) -> dict[str, str]:
    """Get display labels for all systems."""
    return {key: get_system_label(manifest, key) for key in manifest.systems}
