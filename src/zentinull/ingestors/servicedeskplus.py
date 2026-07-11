"""
ServiceDesk Plus ingest: assets + requests.
Uses OAuth2RefreshAuth from auth.py.
Uses separate SDP OAuth token file.
"""

from __future__ import annotations

import json
import os

import requests

from ..logging_config import get_logger
from .auth import OAuth2RefreshAuth
from .base import create_table, db, insert_raw

log = get_logger("ingest.sdp")

SDP_BASE = os.environ.get("SDP_BASE_URL", "https://sdpondemand.manageengine.com")
CLIENT_ID = os.environ.get("SDP_CLIENT_ID", "1000.I2459W43UMXFIJJY19OVDPJJNFMEOM")
CLIENT_SECRET = os.environ.get("SDP_CLIENT_SECRET", "")
OAUTH_FILE = os.environ.get("SDP_OAUTH_FILE", "data/sdp_oauth.json")
SDP_ACCEPT = "application/vnd.manageengine.sdp.v3+json"

TABLES = {
    "assets": {
        "endpoint": "/api/v3/assets",
        "response_path": "assets",
        "cols": [
            "asset_id",
            "name",
            "serial_number",
            "asset_tag",
            "model",
            "manufacturer",
            "assigned_user",
            "status",
            "department",
            "location",
            "purchase_date",
            "warranty_expiry",
        ],
    },
    "requests": {
        "endpoint": "/api/v3/requests",
        "response_path": "requests",
        "cols": [
            "request_id",
            "subject",
            "status",
            "priority",
            "mode",
            "created_time",
            "due_by_time",
            "completed_time",
            "technician_name",
            "requester_name",
            "department",
        ],
    },
    "contracts": {
        "endpoint": "/api/v3/contracts",
        "response_path": "contracts",
        "cols": ["contract_id", "name", "contract_type", "vendor_name", "start_date", "end_date", "cost", "status"],
    },
    "purchase_orders": {
        "endpoint": "/api/v3/purchase_orders",
        "response_path": "purchase_orders",
        "cols": ["po_id", "name", "po_number", "vendor_name", "total_cost", "status", "created_time", "delivery_date"],
    },
}


def _sdp_fetch(
    auth: OAuth2RefreshAuth,
    endpoint: str,
    response_path: str,
) -> list:  # type: ignore[type-arg]
    """Fetch paginated SDP data with Accept header."""
    headers = auth.get_headers()
    headers["Accept"] = SDP_ACCEPT
    url = f"{SDP_BASE}{endpoint}"
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get(response_path, [])  # type: ignore[no-any-return]
    except Exception as e:
        log.error({"event": "get_failed", "source": "sdp", "endpoint": endpoint, "error": str(e)})
        return []


def _extract(obj: dict, field: str) -> str:  # type: ignore[type-arg]
    """Extract string or nested name from SDP object values."""
    v = obj.get(field)
    if isinstance(v, dict):
        return str(v.get("name", v))
    return str(v) if v is not None else ""


def ingest() -> int:
    conn = db("sdp")
    total = 0

    auth = OAuth2RefreshAuth(
        "https://accounts.zoho.com/oauth/v2/token",
        CLIENT_ID,
        CLIENT_SECRET,
        token_file=OAUTH_FILE,
    )
    if not auth.refresh():
        log.error({"event": "auth_failed", "source": "sdp"})
        conn.close()
        return 0

    for tname, tdef in TABLES.items():
        items = _sdp_fetch(auth, tdef["endpoint"], tdef["response_path"])  # type: ignore[arg-type]
        if not items:
            log.info({"event": "empty", "source": "sdp", "table": tname})
            continue
        records = []
        for item in items:
            rec = {}
            for col in tdef["cols"]:
                rec[col] = _extract(item, col)
            rec["raw_json"] = json.dumps(item)
            records.append(rec)
        create_table(conn, tname, tdef["cols"])  # type: ignore[arg-type]
        n = insert_raw(conn, tname, records)
        log.info({"event": "inserted", "source": "sdp", "table": tname, "rows": n})
        total += n

    conn.close()
    return total
