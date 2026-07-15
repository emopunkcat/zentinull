"""Strategy-driven ingest runner.

Replaces legacy per-source ingestor modules with manifest-driven strategy
execution that writes raw rows to per-feed raw-store tables.

Endpoint resolution is performed here before calling the strategy:
- If the feed endpoint has a ``"base"`` key, it names a :mod:`zentinull.config`
  constant whose value is used as the base URL; ``"path"`` is appended.
- If it has ``"search_base_conf"``, that names a config constant for LDAP.
- All other keys pass through verbatim to the strategy.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import config as _config
from ..ingestors import base
from ..logging_config import get_logger
from ..manifest import Manifest, get_system_feeds
from .auth_factory import build_auth
from .strategies import REGISTRY as STRATEGY_REGISTRY

log = get_logger("ingest.runner")


def _resolve_endpoint(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a feed's endpoint config against config constants.

    Operates on a **copy** so the original manifest endpoint is never mutated.
    """
    resolved = dict(endpoint)

    if "base" in resolved:
        base_conf = resolved.pop("base")
        base_url = str(getattr(_config, base_conf)).rstrip("/")
        path = resolved.pop("path", "")
        resolved["url"] = base_url + path

    if "search_base_conf" in resolved:
        resolved["search_base"] = getattr(_config, resolved.pop("search_base_conf"))

    return resolved


def run_feed(
    manifest: Manifest,
    feed_key: str,
    *,
    incremental: bool = False,
) -> int:
    """Run a single feed, writing rows to its raw-store table.

    Returns the number of rows written (0 on error or empty fetch).
    """
    feed = manifest.feeds.get(feed_key)
    if feed is None:
        log.warning({"event": "feed_not_found", "feed_key": feed_key})
        return 0

    system = manifest.systems.get(feed.system)
    if system is None:
        log.warning({"event": "system_not_found", "feed_key": feed_key, "system": feed.system})
        return 0

    # Resolve endpoint
    endpoint = _resolve_endpoint(feed.endpoint)

    # Build auth
    auth = build_auth(system.auth)

    # Look up strategy
    strategy_fn = STRATEGY_REGISTRY.get(system.strategy)
    if strategy_fn is None:
        log.error({"event": "strategy_not_found", "feed_key": feed_key, "strategy": system.strategy})
        return 0

    # Fetch
    log.info(
        {
            "event": "fetch_start",
            "feed": feed_key,
            "strategy": system.strategy,
            "url": endpoint.get("url", ""),
        }
    )
    try:
        rows = strategy_fn(endpoint, auth)
    except Exception as e:
        log.error({"event": "fetch_failed", "feed": feed_key, "error": str(e)})
        return 0

    log.info({"event": "rows_fetched", "feed": feed_key, "count": len(rows)})

    if not rows:
        log.warning({"event": "empty_fetch", "feed": feed_key})
        return 0

    # Write to raw store
    conn = base.db(feed.system)
    try:
        if incremental:
            base.ensure_raw_store(conn, feed.store)
            written = base.upsert_raw_rows(conn, feed.store, rows, feed.id_path, feed.updated_path)
        else:
            base.create_raw_store(conn, feed.store)
            written = base.insert_raw_rows(conn, feed.store, rows, feed.id_path, feed.updated_path)
    except Exception as e:
        log.error({"event": "write_failed", "feed": feed_key, "error": str(e)})
        conn.close()
        return 0

    conn.close()
    log.info({"event": "rows_written", "feed": feed_key, "count": written})
    return written


def run_system(
    system_key: str,
    manifest: Manifest | None = None,
    *,
    incremental: bool = False,
    feed_keys: list[str] | None = None,
) -> dict[str, int]:
    """Run all feeds for a system, returning per-feed row counts.

    Builds auth **once** per system, resolves endpoints, fetches via the
    configured strategy, and writes to each feed's raw-store table.

    Args:
        system_key: Key into the manifest.
        manifest: Loaded manifest; loads from defaults if ``None``.
        incremental: When ``True``, use upsert (insert/update/skip) semantics.
        feed_keys: Optional subset of feeds to run (only those belonging to
            this system are used).

    Returns:
        Dict mapping feed key → rows written per feed. 0 on empty fetch, error,
        or skipped feed.
    """
    if manifest is None:
        from ..manifest import load_manifest

        manifest = load_manifest()

    system = manifest.systems.get(system_key)
    if system is None:
        log.warning({"event": "system_not_found", "system_key": system_key})
        return {}

    if feed_keys is not None:
        feeds_to_run = [k for k in feed_keys if k in manifest.feeds and manifest.feeds[k].system == system_key]
    else:
        feeds_to_run = get_system_feeds(manifest, system_key)

    if not feeds_to_run:
        log.warning({"event": "no_feeds", "system_key": system_key})
        return {}

    # Build auth + resolve strategy once per system
    auth = build_auth(system.auth)
    strategy_fn = STRATEGY_REGISTRY.get(system.strategy)
    if strategy_fn is None:
        log.error({"event": "strategy_not_found", "system": system_key, "strategy": system.strategy})
        return {}

    results: dict[str, int] = {}
    conn = base.db(system_key)
    try:
        for feed_key in feeds_to_run:
            feed = manifest.feeds[feed_key]
            endpoint = _resolve_endpoint(feed.endpoint)

            log.info(
                {
                    "event": "fetch_start",
                    "feed": feed_key,
                    "strategy": system.strategy,
                    "url": endpoint.get("url", ""),
                }
            )
            try:
                rows = strategy_fn(endpoint, auth)
            except Exception as e:
                log.error({"event": "fetch_failed", "feed": feed_key, "error": str(e)})
                results[feed_key] = 0
                continue

            log.info({"event": "rows_fetched", "feed": feed_key, "count": len(rows)})

            if not rows:
                log.warning({"event": "empty_fetch", "feed": feed_key})
                results[feed_key] = 0
                continue  # Don't touch existing table on transient failure

            try:
                if incremental:
                    base.ensure_raw_store(conn, feed.store)
                    written = base.upsert_raw_rows(conn, feed.store, rows, feed.id_path, feed.updated_path)
                else:
                    base.create_raw_store(conn, feed.store)
                    written = base.insert_raw_rows(conn, feed.store, rows, feed.id_path, feed.updated_path)
                conn.commit()
                results[feed_key] = written
                log.info({"event": "rows_written", "feed": feed_key, "count": written})
            except Exception as e:
                log.error({"event": "write_failed", "feed": feed_key, "error": str(e)})
                results[feed_key] = 0
    finally:
        conn.close()

    return results
