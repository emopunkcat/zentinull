"""
FortiGate ingest: all 8 endpoints.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from ..config import FG_API_KEY, FG_HOST, FG_PORT
from ..logging_config import get_logger
from .auth import APIKeyAuth
from .base import create_table, db, insert_raw

log = get_logger("ingest.fg")

FG_BASE = f"https://{FG_HOST}:{FG_PORT}/api/v2"

ENDPOINTS = {
    "resource_usage": {
        "path": "/monitor/system/resource/usage",
        "response_path": "results",
        "cols": ["fg_host", "mem_used_pct", "cpu_user", "disk_used_pct", "session_count"],
    },
    "clients": {
        "path": "/monitor/user/device/query",
        "response_path": "results",
        "cols": [
            "fg_host",
            "mac",
            "ip",
            "hostname",
            "ssid",
            "vlan",
            "user_name",
            "os",
            "manufacturer",
            "model",
            "signal",
            "ap_name",
            "interface",
        ],
    },
    "dhcp_leases": {
        "path": "/monitor/system/dhcp",
        "response_path": "results",
        "cols": ["fg_host", "mac", "ip", "hostname", "lease_type", "expires", "interface", "vlan_id"],
    },
    "vpn_sessions": {
        "path": "/monitor/vpn/ssl",
        "response_path": "results",
        "cols": [
            "fg_host",
            "user",
            "auth_type",
            "src_ip",
            "tunnel_ip",
            "login_time",
            "idle_time",
            "duration",
            "bytes_sent",
            "bytes_rcvd",
            "public_ip",
        ],
    },
    "known_devices": {
        "path": "/monitor/user/device",
        "response_path": "results",
        "cols": [
            "fg_host",
            "mac",
            "ip",
            "hostname",
            "os",
            "manufacturer",
            "model",
            "user",
            "vendor",
            "type",
            "first_seen",
            "last_seen",
        ],
    },
    "firewall_policies": {
        "path": "/monitor/firewall/policy",
        "response_path": "results",
        "cols": [
            "fg_host",
            "policy_id",
            "name",
            "src_intf",
            "dst_intf",
            "src_addr",
            "dst_addr",
            "action",
            "service",
            "hit_count",
            "last_used",
            "bytes",
            "sessions",
            "schedule",
            "log",
            "status",
        ],
    },
    "interfaces": {
        "path": "/monitor/system/interface",
        "response_path": "results",
        "cols": [
            "fg_host",
            "name",
            "ip",
            "mac",
            "status",
            "speed",
            "mtu",
            "duplex",
            "rx_bytes",
            "tx_bytes",
            "rx_packets",
            "tx_packets",
            "rx_errors",
            "tx_errors",
            "rx_drops",
            "tx_drops",
            "vlan",
            "type",
        ],
    },
    "arp_table": {
        "path": "/monitor/system/arp-table",
        "response_path": "results",
        "cols": ["fg_host", "ip", "mac", "interface", "type", "vlan_id"],
    },
}


def _fg_get(path: str, auth: APIKeyAuth) -> list:  # type: ignore[type-arg]
    url = f"{FG_BASE}{path}"
    headers = auth.get_headers()
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=(10, 30))
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if isinstance(results, dict):
            # Some endpoints (e.g. /monitor/system/interface) return dict
            results = [results]
        return results if isinstance(results, list) else []
    except Exception as e:
        log.error({"event": "get_failed", "source": "fg", "path": path, "error": str(e)})
        return []


def _transform_fg(items: list[dict[str, Any]], cols: list[str], fg_host: str) -> list[dict[str, Any]]:
    """Transform raw FortiGate endpoint data into cleaned records.

    Returns list of records. Pure function — no I/O.
    """
    records = []
    for item in items:
        rec = {}
        for col in cols:
            rec[col] = str(item.get(col, ""))
        rec["fg_host"] = fg_host
        rec["raw_json"] = json.dumps(item)
        records.append(rec)
    return records


def ingest() -> int:
    conn = db("fg")
    total = 0

    auth = APIKeyAuth(FG_API_KEY, header_name="Authorization", prefix="Bearer")

    for tname, tdef in ENDPOINTS.items():
        items = _fg_get(tdef["path"], auth)  # type: ignore[arg-type]
        if not items:
            log.info({"event": "empty", "source": "fg", "table": tname})
            continue
        records = _transform_fg(items, tdef["cols"], FG_HOST)  # type: ignore[arg-type]
        with conn:
            conn.execute(f"DROP TABLE IF EXISTS {tname}")
            create_table(conn, tname, tdef["cols"])  # type: ignore[arg-type]
            n = insert_raw(conn, tname, records)
        total += n

    conn.close()
    return total
