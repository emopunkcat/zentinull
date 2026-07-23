"""
Export all SQLite source DBs to a single Splink-compatible CSV.

Phase 2 COMPAT mode: uses manifest + walker for field extraction.
"""

from __future__ import annotations

import csv
import sqlite3
from typing import Any

from .config import get_paths
from .ingestors.base import validate_identifier
from .logging_config import get_logger
from .manifest import get_anchor_feeds, load_manifest
from .manifest.walker import walk_feed
from .normalizer import normalize_mac, normalize_name, normalize_os_family, normalize_serial, strip_sentinels

log = get_logger("export")

#: Fields computed after extraction (not from source columns, never auto-mapped)
_COMPUTED_FIELDS = frozenset({"source", "name_clean", "mac_clean", "name_fallback", "os_family", "extra_attributes"})

# Feed key → source column value mapping (preserves current CSV source values for backward compat)
_FEED_SOURCE_MAP: dict[str, str] = {
    "sp_devices": "sp",
    "me_ec": "me_ec",
    "me_mdm": "me_mdm",
    "fg_clients": "fg",
    "fg_dhcp": "fg_dhcp",
    "zbx_hosts": "zbx",
    "ad_computers": "ad",
    "sdp_assets": "sdp",
}


def normalize_record(rec: dict[str, Any], feed_key: str, splink_fields: list[str]) -> dict[str, Any]:
    """Apply derived-field computation, sentinel stripping, and field filling to an extracted record."""
    rec["source"] = _FEED_SOURCE_MAP.get(feed_key, feed_key)
    rec["name_clean"] = normalize_name(rec.get("name", ""))
    rec["mac_clean"] = normalize_mac(rec.get("mac_address", ""))
    rec["os_family"] = normalize_os_family(rec.get("os", ""))
    rec["serial_number"] = normalize_serial(rec.get("serial_number", ""))
    rec["name_fallback"] = rec["name_clean"] if rec["serial_number"] == "" and rec["mac_clean"] == "" else ""

    if rec.get("manufacturer"):
        rec["manufacturer"] = rec["manufacturer"].lower()

    for fld in splink_fields:
        if fld in _COMPUTED_FIELDS or fld == "source_id":
            continue
        rec[fld] = strip_sentinels(rec.get(fld, ""))

    for fld in splink_fields:
        if fld not in rec:
            rec[fld] = ""

    return rec


def export() -> None:
    paths = get_paths()
    """Export all ANCHOR feeds to a single Splink CSV via manifest + walker."""
    paths.csv_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    profile = manifest.profiles["device"]
    splink_fields = list(profile.fields)
    anchor_feeds = get_anchor_feeds(manifest, profile="device")

    all_rows: list[dict[str, str]] = []

    for feed_key in anchor_feeds:
        feed = manifest.feeds[feed_key]
        db_path = paths.data_dir / f"{feed.system}.sqlite"
        if not db_path.exists():
            log.warning({"event": "skip", "source": feed_key, "reason": "db_not_found", "path": str(db_path)})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        table_name = validate_identifier(feed.store)
        if table_name not in tables:
            log.warning({"event": "skip", "source": feed_key, "table": table_name, "reason": "table_not_found"})
            conn.close()
            continue
        try:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": feed_key, "table": table_name, "reason": "error", "error": str(e)})
            conn.close()
            continue

        # Walk the feed spec against these rows — raw_json extraction,
        # transforms, multi-column merge, extra_attributes
        extracted = walk_feed(feed, rows)

        for rec in extracted:
            normalize_record(rec, feed_key, splink_fields)

        all_rows.extend(extracted)
        conn.close()

        mapped_source = _FEED_SOURCE_MAP.get(feed_key, feed_key)
        source_count = sum(1 for r in all_rows if r["source"] == mapped_source)
        log.info({"event": "exported", "source": feed_key, "records": source_count})

    # Write CSV
    out_path = paths.csv_dir / "devices.csv"
    tmp_path = paths.csv_dir / "devices.csv.tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=splink_fields)
        writer.writeheader()
        writer.writerows(all_rows)

    # Atomic swap: rename tmp → devices.csv. If the target is locked (e.g., open
    # in Excel on Windows/WSL), keep the .tmp file and point downstream at it.
    final_path = out_path
    try:
        out_path.unlink(missing_ok=True)
        tmp_path.rename(out_path)
    except (PermissionError, OSError):
        log.warning({"event": "export_swap_fallback", "path": str(out_path), "reason": "target locked"})
        # Leave devices.csv.tmp in place; downstream (splink_runner, pipeline)
        # checks for .tmp when devices.csv is stale or missing.
        final_path = tmp_path

    log.info({"event": "export_complete", "total_records": len(all_rows), "path": str(final_path)})

    if not all_rows:
        log.warning({"event": "export_empty", "reason": "no_records_from_any_source"})
        out_path.unlink(missing_ok=True)
        return

    # Per-source breakdown
    sources: dict[str, int] = {}
    for r in all_rows:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    for s, c in sorted(sources.items()):
        log.info({"event": "source_breakdown", "source": s, "records": c})

    # Coverage stats
    for field in ["serial_number", "mac_address", "name", "assigned_user"]:
        filled = sum(1 for r in all_rows if r[field])
        log.info(
            {
                "event": "coverage",
                "field": field,
                "filled": filled,
                "total": len(all_rows),
                "pct": 100 * filled // len(all_rows),
            }
        )


if __name__ == "__main__":
    export()
