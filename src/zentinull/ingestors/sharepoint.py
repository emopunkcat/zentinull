"""
SharePoint ingest via n8n webhook.
Auto-detects columns from actual API response.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from ..logging_config import get_logger
from .base import create_table, db, insert_raw

log = get_logger("ingest.sp")

N8N_BASE = os.environ.get("SHAREPOINT_BASE_URL", "http://192.168.20.56:5678/webhook")

ENDPOINTS = [
    "sp_devices",
    "sp_employees",
    "sp_AccountInfo",
    "sp_devicenotes",
    "sp_vlans",
    "sp_ComponentPurchases",
]


def _sanitize_col_name(name: str, existing: set[str]) -> str:
    """Sanitize a column name for SQL and deduplicate within *existing*."""
    safe = name.replace("@", "").replace(".", "_").replace("-", "_")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in safe)
    safe = safe.strip("_").lower() or "col"
    if safe == "id":
        safe = "sp_id"
    while safe in existing:
        safe += "_"
    existing.add(safe)
    return safe


def _transform_sharepoint(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform raw SharePoint items into cleaned records with sanitized column names.

    Handles auto-detected columns and field extraction.
    Returns (records, sanitized_column_names). Pure function — no I/O.
    """
    # Phase 1: extract raw records from items
    records = []
    for item in items:
        fields = item.get("fields", item)
        rec = {}
        for k, v in fields.items():
            if not isinstance(v, dict | list):
                rec[k.lower()] = str(v) if v is not None else ""
        rec["sharepoint_id"] = str(item.get("id", ""))
        records.append(rec)

    # Phase 2: sanitize column names
    all_cols = list({k for r in records for k in r})
    all_cols = [c for c in all_cols if c not in ("raw_json",)]
    san_cols: list[str] = []
    used: set[str] = set()
    for c in all_cols:
        san_cols.append(_sanitize_col_name(c, used))
    col_map = dict(zip(all_cols, san_cols, strict=True))
    for rec_item in records:
        for old, new in col_map.items():
            if old != new:
                rec_item[new] = rec_item.pop(old)

    return records, san_cols


def ingest() -> int:
    conn = db("sp")
    total = 0

    for endpoint in ENDPOINTS:
        url = f"{N8N_BASE}/{endpoint}"
        log.info({"event": "fetching", "url": url})
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            items = r.json()
            if not items:
                log.info({"event": "empty", "source": "sp", "endpoint": endpoint})
                continue

            records, san_cols = _transform_sharepoint(items)
            create_table(conn, endpoint, san_cols)
            n = insert_raw(conn, endpoint, records)
            log.info({"event": "inserted", "source": "sp", "endpoint": endpoint, "rows": n})
            total += n
        except Exception as e:
            log.error({"event": "fetch_failed", "source": "sp", "endpoint": endpoint, "error": str(e)})
    conn.close()
    return total
