"""Manifest walker — extract fields from SQLite rows using feed specs.

Reads raw_json (parsed as dict) for all field extraction. No typed-column
fallback — all sources store raw_json.
"""

from __future__ import annotations

import json
from typing import Any

from ..normalizer import NULL_SENTINELS, strip_sentinels
from .transforms import REGISTRY as TRANSFORM_REGISTRY
from .types import Feed, FieldSpec

# Columns never included in extra_attributes
_SYSTEM_COLS = frozenset({"id", "ingested_at", "raw_json", "source_id", "raw_hash", "remote_updated_at", "fetched_at"})


def _resolve_dotted(obj: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path like ``user.email`` against a dict or list.

    Numeric segments index into lists: ``interfaces.0.ip`` returns the
    ``ip`` key of the first interface dict, or ``None`` on bounds/type miss.
    """
    current = obj
    for part in path.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                return None
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _extract_field(
    raw_dict: dict[str, Any] | None,
    spec: FieldSpec,
) -> str:
    """Extract a field collecting ALL non-empty values across ALL paths.

    Collects every non-empty, deduplicated value across all paths from
    raw_json, then comma-joins them.  Strips sentinels, then applies
    *spec.transform* (if set) to the combined result.

    The comma-join preserves the current SP behaviour where EthMAC **and**
    WLANMac both contribute to ``mac_address``.
    """
    values: list[str] = []

    for path in spec.paths:
        found = None
        if raw_dict is not None:
            raw_val = _resolve_dotted(raw_dict, path)
            if raw_val is not None:
                # Normalize list/tuple: comma-join before string conversion.
                # str(["a","b"]) = "['a', 'b']" — corrupted. We want "a,b".
                if isinstance(raw_val, (list, tuple)):
                    parts = [
                        str(x).strip()
                        for x in raw_val
                        if x is not None and str(x).strip() and str(x).strip() not in NULL_SENTINELS
                    ]
                    raw_str = ",".join(parts)
                else:
                    raw_str = str(raw_val).strip()

                if raw_str and raw_str not in NULL_SENTINELS:
                    found = raw_str

        if found is not None and found not in values:
            values.append(found)

    if not values:
        return ""

    # Comma-join unique values (multi-MAC / multi-value merge)
    result = ",".join(values)

    # Strip sentinels from the combined result
    result = strip_sentinels(result)
    if not result:
        return ""

    # Apply transform to the combined value
    if spec.transform and spec.transform in TRANSFORM_REGISTRY:
        result = TRANSFORM_REGISTRY[spec.transform](result)

    return result


def _extract_source_id(
    raw_dict: dict[str, Any] | None,
    feed: Feed,
) -> str:
    """Extract the stable source id from *feed.id_path*.

    Reads from raw_json only.
    """
    if raw_dict is not None:
        raw_val = _resolve_dotted(raw_dict, feed.id_path)
        if isinstance(raw_val, (list, tuple)):
            joined = ",".join(str(x).strip() for x in raw_val if x is not None and str(x).strip())
            if joined:
                return joined
        elif raw_val is not None and str(raw_val).strip():
            return str(raw_val).strip()

    return ""


def _flatten_raw(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten a nested raw value into (dotted_key, scalar_string) pairs.

    Dicts recurse with dotted keys (``fields.Title``); lists of scalars are
    comma-joined; lists of dicts / other structures are JSON-encoded. This keeps
    ``extra_attributes`` clean JSON — never a Python ``repr`` blob.
    """
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_flatten_raw(v, key))
    elif isinstance(obj, (list, tuple)):
        if all(not isinstance(x, (dict, list, tuple)) for x in obj):
            joined = ",".join(str(x).strip() for x in obj if x is not None and str(x).strip())
            if joined:
                out.append((prefix, joined))
        else:
            out.append((prefix, json.dumps(obj, default=str)))
    elif obj is not None and str(obj).strip() and str(obj).strip() not in NULL_SENTINELS:
        out.append((prefix, str(obj).strip()))
    return out


def _collect_extra_attributes(
    raw_dict: dict[str, Any] | None,
    feed: Feed,
) -> str:
    """Collect unmapped keys into a JSON ``extra_attributes`` string.

    Excludes every path referenced in ``feed.spec``, ``feed.id_path``, and
    system columns — all matched case-insensitively.
    """
    mapped_keys_lower: set[str] = set()
    for field_spec in feed.spec.values():
        for path in field_spec.paths:
            mapped_keys_lower.add(path.lower())
    mapped_keys_lower.add(feed.id_path.lower())
    for col in _SYSTEM_COLS:
        mapped_keys_lower.add(col.lower())

    extra: dict[str, str] = {}

    if raw_dict is not None and isinstance(raw_dict, dict):
        for dotted_key, val in _flatten_raw(raw_dict):
            if dotted_key.lower() in mapped_keys_lower:
                continue
            if dotted_key in extra:
                continue
            extra[dotted_key] = val

    return json.dumps(extra) if extra else ""


def walk_feed(
    feed: Feed,
    rows: list[Any],  # sqlite3.Row objects
) -> list[dict[str, str]]:
    """Extract fields from SQLite rows using the feed spec.

    Reads ``raw_json`` (parsed as dict) for all field extraction.

    Parameters
    ----------
    feed:
        Feed descriptor whose ``.spec`` maps target field names to
        :class:`FieldSpec` objects.
    rows:
        Iterable of row objects (dict-like, e.g. ``sqlite3.Row``).

    Returns
    -------
    list[dict[str, str]]
        One dict per input row.  Keys match ``feed.spec`` plus ``source_id``
        and ``extra_attributes``.
    """
    results: list[dict[str, str]] = []

    for row in rows:
        row_dict = dict(row) if not isinstance(row, dict) else row

        raw_val = row_dict.get("raw_json", "")
        raw_dict: dict[str, Any] | None = None
        if raw_val and isinstance(raw_val, str) and raw_val.strip():
            try:
                parsed = json.loads(raw_val)
                if isinstance(parsed, dict):
                    raw_dict = parsed
            except (json.JSONDecodeError, TypeError):
                raw_dict = None

        rec: dict[str, str] = {}
        for field_name, field_spec in feed.spec.items():
            rec[field_name] = _extract_field(raw_dict, field_spec)

        rec["source_id"] = _extract_source_id(raw_dict, feed)
        rec["extra_attributes"] = _collect_extra_attributes(raw_dict, feed)

        results.append(rec)

    return results
