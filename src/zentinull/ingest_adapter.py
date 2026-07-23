"""Ingest adapter — bridges manifest systems to strategy-based runner.

Phase 3: delegates all ingest to the strategy-based runner.
Legacy per-source ingestor modules no longer imported here.

Systems are ingested in parallel via a thread pool — each system is I/O-bound
(HTTP fetch from a different API) and writes to its own SQLite DB, so there is
no shared state or write contention.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .ingest.runner import run_system
from .logging_config import get_logger
from .manifest import Manifest

log = get_logger("ingest.adapter")

# Maximum concurrent system ingests. 6 systems → 6 threads is safe because
# each system hits a different API and writes to its own SQLite file.
_MAX_WORKERS = 6


def _ingest_one_system(manifest: Manifest, system_key: str) -> tuple[str, int]:
    """Ingest a single system, returning (system_key, total_rows).

    Wrapped so it can be submitted to a thread pool.  Exceptions are caught
    here (not in the caller) so one system's failure doesn't abort the others.
    """
    log.info({"event": "ingesting", "system": system_key})
    try:
        per_feed = run_system(system_key, manifest)
        total = sum(per_feed.values())
        log.info({"event": "ingested", "system": system_key, "rows": total})
        return system_key, total
    except Exception as e:
        log.error({"event": "ingest_failed", "system": system_key, "error": str(e)})
        return system_key, -1


def run_ingest(
    manifest: Manifest,
    sources: list[str] | None = None,
    skip_sources: list[str] | None = None,
) -> dict[str, int]:
    """Run ingestors for the given manifest, systems in parallel.

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

    # Validate system keys up front (matches pre-parallel behavior for unknown keys)
    valid: list[str] = []
    for system_key in systems_to_run:
        if system_key not in manifest.systems:
            log.warning({"event": "no_system_in_manifest", "system": system_key})
            results[system_key] = 0
        else:
            valid.append(system_key)

    if not valid:
        return results

    # Single system → skip the pool overhead
    if len(valid) == 1:
        key, total = _ingest_one_system(manifest, valid[0])
        results[key] = total
        return results

    workers = min(len(valid), _MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ingest_one_system, manifest, system_key): system_key for system_key in valid}
        for future in as_completed(futures):
            system_key = futures[future]
            try:
                key, total = future.result()
                results[key] = total
            except Exception as e:
                # _ingest_one_system already catches, but guard against the
                # unexpected (e.g. executor shutdown) so we never lose a key.
                log.error({"event": "ingest_future_failed", "system": system_key, "error": str(e)})
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
