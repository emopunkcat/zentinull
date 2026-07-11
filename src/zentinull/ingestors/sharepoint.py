"""
SharePoint ingest via n8n webhook.
Auto-detects columns from actual API response.
"""

import requests

from ..logging_config import get_logger
from .base import create_table, db, insert_raw

log = get_logger("ingest.sp")

N8N_BASE = "http://192.168.20.56:5678/webhook"

ENDPOINTS = [
    "sp_devices",
    "sp_employees",
    "sp_AccountInfo",
    "sp_devicenotes",
    "sp_vlans",
    "sp_ComponentPurchases",
]


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

            # Auto-detect columns from first record
            fields = items[0].get("fields", items[0])
            # Build all records first
            records = []
            for item in items:
                fields = item.get("fields", item)
                rec = {}
                for k, v in fields.items():
                    if not isinstance(v, (dict, list)):
                        rec[k.lower()] = str(v) if v is not None else ""
                rec["sharepoint_id"] = str(item.get("id", ""))
                records.append(rec)

            # Get all unique column names from records, sanitizing for SQL
            all_cols = list({k for r in records for k in r})
            all_cols = [c for c in all_cols if c not in ("raw_json",)]
            # Sanitize column names: replace @ and special chars
            san_cols = []
            for c in all_cols:
                safe = c.replace("@", "").replace(".", "_").replace("-", "_")
                safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in safe)
                safe = safe.strip("_").lower() or "col"
                # Avoid 'id' conflict with auto-increment PK
                if safe == "id":
                    safe = "sp_id"
                # Remove duplicates
                while safe in san_cols:
                    safe += "_"
                san_cols.append(safe)
            col_map = dict(zip(all_cols, san_cols, strict=True))
            for rec_item in records:
                for old, new in col_map.items():
                    if old != new:
                        rec_item[new] = rec_item.pop(old)
            # Create table with sanitized schema
            create_table(conn, endpoint, san_cols)

            # Bulk insert
            n = insert_raw(conn, endpoint, records)
            log.info({"event": "inserted", "source": "sp", "endpoint": endpoint, "rows": n})
            total += n

        except Exception as e:
            log.error({"event": "fetch_failed", "source": "sp", "endpoint": endpoint, "error": str(e)})

    conn.close()
    return total
