"""
ManageEngine ingest: EC computers + MDM devices.
Uses OAuth2RefreshAuth from auth.py.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from ..logging_config import get_logger
from .auth import OAuth2RefreshAuth
from .base import create_table, db, insert_raw

log = get_logger("ingest.me")

CLOUD_BASE = "https://endpointcentral.manageengine.com/api/1.4"
MDM_BASE = "https://mdm.manageengine.com/api/v1/mdm"
CLIENT_ID = os.environ.get("ME_CLIENT_ID", "1000.I2459W43UMXFIJJY19OVDPJJNFMEOM")
CLIENT_SECRET = os.environ.get("ME_CLIENT_SECRET", "")
OAUTH_FILE = os.environ.get("ME_OAUTH_FILE", "data/me_oauth.json")


def _me_auth() -> OAuth2RefreshAuth:
    return OAuth2RefreshAuth(
        "https://accounts.zoho.com/oauth/v2/token",
        CLIENT_ID,
        CLIENT_SECRET,
        token_file=OAUTH_FILE,
    )


def _me_fetch(
    url: str,
    auth: OAuth2RefreshAuth,
    response_path: str | None = None,
) -> list:  # type: ignore[type-arg]
    """Paginated fetch for ME EC API."""
    headers = {"Accept": "application/json", **auth.get_headers()}
    all_items: list = []  # type: ignore[type-arg]
    page = 1
    while True:
        page_url = f"{url}?page={page}"
        log.info({"event": "fetching", "url": page_url})
        r = requests.get(page_url, headers=headers, timeout=60)
        if r.status_code == 204 or not r.text.strip():
            break
        r.raise_for_status()
        data = r.json()
        items = data
        if response_path:
            for part in response_path.split("."):
                if isinstance(items, dict):
                    items = items.get(part, [])
        if not items:
            break
        all_items.extend(items)
        page += 1
    return all_items


def _mdm_fetch(auth: OAuth2RefreshAuth) -> list:  # type: ignore[type-arg]
    """Fetch all MDM devices."""
    headers = {"Accept": "application/json", **auth.get_headers()}
    url = f"{MDM_BASE}/devices"
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()  # type: ignore[no-any-return]


def _transform_ec_computers(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform raw ManageEngine EC computer data into cleaned records.

    Returns (records, columns). Pure function — no I/O.
    """
    records = []
    for item in items:
        records.append(
            {
                "resource_id": str(item.get("resource_id", "")),
                "serial_number": str(item.get("serial_number", "")),
                "mac_address": str(item.get("mac_address", "")),
                "name": str(item.get("name", "")),
                "manufacturer": str(item.get("manufacturer", "")),
                "model": str(item.get("model", "")),
                "os_name": str(item.get("os_name", "")),
                "os_version": str(item.get("os_version", "")),
                "assigned_user": str(item.get("logged_on_user", "")),
                "last_seen": str(item.get("last_scan_time", "")),
                "domain_name": str(item.get("domain_name", "")),
                "ip_address": str(item.get("ip_address", "")),
                "raw_json": json.dumps(item),
                "source_type": "ec",
            }
        )
    columns = [
        "resource_id",
        "serial_number",
        "mac_address",
        "name",
        "manufacturer",
        "model",
        "os_name",
        "os_version",
        "assigned_user",
        "last_seen",
        "domain_name",
        "ip_address",
        "source_type",
    ]
    return records, columns


def _transform_mdm_devices(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform raw ManageEngine MDM device data into cleaned records.

    Returns (records, columns). Pure function — no I/O.
    """
    records = []
    for item in items:
        records.append(
            {
                "device_id": str(item.get("device_id", "")),
                "serial_number": str(item.get("serial_number", "")),
                "imei": str(item.get("imei", "")),
                "udid": str(item.get("udid", "")),
                "name": str(item.get("name", "")),
                "model": str(item.get("model", "")),
                "os_version": str(item.get("os_version", "")),
                "user_email": str(item.get("user_email", "")),
                "platform": str(item.get("platform", "")),
                "enrolled_at": str(item.get("enrolled_time", "")),
                "last_seen": str(item.get("last_seen_time", "")),
                "raw_json": json.dumps(item),
                "source_type": "mdm",
            }
        )
    columns = [
        "device_id",
        "serial_number",
        "imei",
        "udid",
        "name",
        "model",
        "os_version",
        "user_email",
        "platform",
        "enrolled_at",
        "last_seen",
        "source_type",
    ]
    return records, columns


def ingest() -> int:
    conn = db("me")
    total = 0

    auth = _me_auth()
    if not auth.refresh():
        log.error({"event": "auth_failed", "source": "me"})
        conn.close()
        return 0

    # --- EC Computers ---
    items = _me_fetch(
        f"{CLOUD_BASE}/inventory/scancomputers",
        auth,
        "message_response.scancomputers",
    )
    if items:
        records, columns = _transform_ec_computers(items)
        create_table(conn, "computers", columns)
        n = insert_raw(conn, "computers", records)
        log.info({"event": "inserted", "source": "me", "table": "computers", "rows": n})
        total += n

    # --- MDM Devices ---
    items = _mdm_fetch(auth)
    if items:
        records, columns = _transform_mdm_devices(items)
        create_table(conn, "mdm_devices", columns)
        n = insert_raw(conn, "mdm_devices", records)
        log.info({"event": "inserted", "source": "me", "table": "mdm_devices", "rows": n})
        total += n

    conn.close()
    return total
