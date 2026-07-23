"""Attachment resolver — links non-device records to device clusters.

Three strategies:
- exact: direct lookup of a field value against the keyspace
- normalized: apply a transform then lookup
- extract_fuzzy: tokenize text, reject boilerplate, match surviving tokens

Never merges anchor records — attachments are N rows per link, never a merge.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import duckdb

from ..export_for_splink import _FEED_SOURCE_MAP
from ..ingestors.base import validate_identifier
from ..logging_config import get_logger
from ..manifest.types import Feed, Link

log = get_logger("resolve.attach")

# ── Constants ──────────────────────────────────────────────────────────

_MIN_TOKEN_LEN = 4
_MIN_SINGLE_TOKEN_LEN = 6
_REJECT_WORDS = frozenset(
    {
        "iphone",
        "ipad",
        "ipod",
        "laptop",
        "desktop",
        "printer",
        "server",
        "workstation",
        "router",
        "switch",
        "plotter",
        "toughbook",
        "macbook",
        "monitor",
        "scanner",
        "camera",
        "phone",
        "tablet",
        "console",
    }
)
_CONFIDENCE_EXACT_TOKEN = 0.9
_CONFIDENCE_DOMAIN_STRIPPED = 0.7


@dataclass
class AttachResult:
    """Single attachment resolution result."""

    cluster_id: str
    confidence: float


# ── Keyspace builder ───────────────────────────────────────────────────


def build_keyspace(db_path: str, link_scope: tuple[str, ...] | None = None) -> dict[str, str]:
    """Build token → cluster_id lookup from source_records in the mesh DB.

    ``link_scope`` is a tuple of feed keys; they are translated to the source
    column values used in ``source_records`` (e.g. ``sp_devices`` -> ``sp``).
    """
    conn = duckdb.connect(db_path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT cluster_id, source, source_id, name_clean, mac_clean, serial_number, asset_tag, assigned_user FROM source_records"
        ).fetchall()
        keyspace: dict[str, str] = {}
        scope_sources: set[str] | None = None
        if link_scope:
            scope_sources = {_FEED_SOURCE_MAP.get(feed_key, feed_key) for feed_key in link_scope}
        for row in rows:
            cid, source, source_id, name, mac, serial, asset, user = row
            if scope_sources is not None and source not in scope_sources:
                continue
            for val in [source_id, name, mac, serial, asset, user]:
                if val and str(val).strip():
                    keyspace[str(val).lower().strip()] = cid
        return keyspace
    finally:
        conn.close()


def build_reject_lists(db_path: str) -> tuple[set[str], set[str]]:
    """Build manufacturer and OS reject-word sets from source_records."""
    conn = duckdb.connect(db_path, read_only=True)
    try:
        mfr_rows = conn.execute(
            "SELECT DISTINCT lower(manufacturer) FROM source_records WHERE manufacturer != ''"
        ).fetchall()
        os_rows = conn.execute("SELECT DISTINCT lower(os) FROM source_records WHERE os != ''").fetchall()
        manufacturers = {r[0] for r in mfr_rows if r[0]}
        os_values = {r[0] for r in os_rows if r[0]}
        return manufacturers, os_values
    finally:
        conn.close()


# ── Resolvers ──────────────────────────────────────────────────────────


def _resolve_dotted(obj: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path like 'user.email' against a dict."""
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_exact(link: Link, raw_record: dict[str, Any], keyspace: dict[str, str]) -> list[AttachResult]:
    """Exact lookup: raw_record[link.field] → keyspace."""
    val = _resolve_dotted(raw_record, link.field)
    if val is None or not str(val).strip():
        return []
    cid = keyspace.get(str(val).lower().strip())
    if cid:
        return [AttachResult(cluster_id=cid, confidence=1.0)]
    return []


def resolve_normalized(link: Link, raw_record: dict[str, Any], keyspace: dict[str, str]) -> list[AttachResult]:
    """Normalized lookup: apply transform then keyspace lookup."""
    from ..manifest.transforms import REGISTRY as TRANSFORM_REGISTRY

    val = _resolve_dotted(raw_record, link.field)
    if val is None or not str(val).strip():
        return []
    if link.transform and link.transform in TRANSFORM_REGISTRY:
        normalized = TRANSFORM_REGISTRY[link.transform](str(val))
    else:
        normalized = str(val).strip()
    if not normalized:
        return []
    cid = keyspace.get(normalized.lower().strip())
    if cid:
        return [AttachResult(cluster_id=cid, confidence=1.0)]
    return []


def _tokenize(text: str) -> list[tuple[str, bool]]:
    """Tokenize text for fuzzy matching. Returns (token, was_domain_stripped) pairs."""
    text = text.lower()
    # Strip possessives
    text = re.sub(r"([a-z])'s\b", r"\1", text)
    # Split on non-alnum-dash
    raw_tokens = re.split(r"[^a-z0-9\-]+", text)
    result: list[tuple[str, bool]] = []
    for tok in raw_tokens:
        if not tok:
            continue
        # Check if domain-stripped (had a dot originally)
        was_dotted = "." in tok
        # Take first label before dot
        token = tok.split(".")[0] if was_dotted else tok
        # Drop short tokens
        if len(token) < _MIN_TOKEN_LEN:
            continue
        # Drop short tokens without digits
        if len(token) < _MIN_SINGLE_TOKEN_LEN and not re.search(r"\d", token):
            continue
        result.append((token, was_dotted))
    return result


def resolve_extract_fuzzy(
    link: Link,
    raw_record: dict[str, Any],
    keyspace: dict[str, str],
    all_manufacturers: set[str],
    all_os_values: set[str],
) -> list[AttachResult]:
    """Fuzzy extract: tokenize, reject boilerplate, match surviving tokens."""
    val = _resolve_dotted(raw_record, link.field)
    if val is None or not str(val).strip():
        return []

    tokens = _tokenize(str(val))
    if not tokens:
        return []

    all_reject = _REJECT_WORDS | all_manufacturers | all_os_values

    seen_cids: dict[str, tuple[float, str]] = {}  # cid → (confidence, token)
    for token, was_dotted in tokens:
        if token in all_reject:
            continue
        cid = keyspace.get(token)
        if cid is None:
            continue
        # DF rule: token must resolve to exactly 1 cluster (keyspace is 1:1 by construction)
        confidence = _CONFIDENCE_DOMAIN_STRIPPED if was_dotted else _CONFIDENCE_EXACT_TOKEN
        # Keep highest confidence per cluster_id
        if cid not in seen_cids or confidence > seen_cids[cid][0]:
            seen_cids[cid] = (confidence, token)

    results: list[AttachResult] = []
    if link.multi:
        # Return ALL distinct cluster_ids
        for cid, (conf, _) in sorted(seen_cids.items()):
            results.append(AttachResult(cluster_id=cid, confidence=conf))
    else:
        # Return only the first distinct cluster_id (longest token wins)
        if seen_cids:
            best_cid = max(seen_cids, key=lambda c: len(seen_cids[c][1]))
            results.append(AttachResult(cluster_id=best_cid, confidence=seen_cids[best_cid][0]))

    return results


# ── Orchestrator ───────────────────────────────────────────────────────


def resolve_feed_attachments(
    feed: Feed,
    feed_key: str,
    mesh_db_path: str,
    sqlite_db_path: str,
) -> list[dict[str, Any]]:
    """Run a single ATTACHMENT feed's links, returning attachment row dicts."""
    results: list[dict[str, Any]] = []

    # 1. Read raw rows from the feed's SQLite store
    conn = sqlite3.connect(sqlite_db_path)
    conn.row_factory = sqlite3.Row
    try:
        store_table = validate_identifier(feed.store)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if store_table not in tables:
            log.warning({"event": "table_not_found", "feed": feed_key, "table": store_table})
            return results
        rows = conn.execute(f"SELECT source_id, raw_json FROM {store_table}").fetchall()
    except Exception as e:
        log.error({"event": "read_failed", "feed": feed_key, "error": str(e)})
        return results
    finally:
        conn.close()

    if not rows:
        return results

    # 2. Build keyspace from mesh
    link_scope = feed.links[0].scope if feed.links else None
    keyspace = build_keyspace(mesh_db_path, link_scope=link_scope)
    if not keyspace:
        log.warning({"event": "empty_keyspace", "feed": feed_key})
        return results

    # 3. Build reject lists for extract_fuzzy
    all_manufacturers: set[str] = set()
    all_os_values: set[str] = set()
    with contextlib.suppress(Exception):
        all_manufacturers, all_os_values = build_reject_lists(mesh_db_path)
    # 4. Resolve each raw record against each link
    for row in rows:
        try:
            raw_dict = json.loads(row["raw_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        source_id = row["source_id"]

        for link in feed.links:
            # Pre-check the field exists before dispatching to resolvers
            raw_val = _resolve_dotted(raw_dict, link.field)
            if raw_val is None or not str(raw_val).strip():
                continue

            if link.strategy == "exact":
                attaches = resolve_exact(link, raw_dict, keyspace)
            elif link.strategy == "normalized":
                attaches = resolve_normalized(link, raw_dict, keyspace)
            elif link.strategy == "extract_fuzzy":
                attaches = resolve_extract_fuzzy(link, raw_dict, keyspace, all_manufacturers, all_os_values)
            else:
                log.warning({"event": "unknown_strategy", "strategy": link.strategy})
                continue

            for at in attaches:
                results.append(
                    {
                        "cluster_id": at.cluster_id,
                        "feed_key": feed_key,
                        "source_id": str(source_id),
                        "field": link.field,
                        "value": str(raw_val)[:500],
                        "confidence": at.confidence,
                        "payload": json.dumps(raw_dict, default=str),
                    }
                )

    log.info({"event": "attach_resolved", "feed": feed_key, "links": len(results)})
    return results
