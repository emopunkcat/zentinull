"""
Zabbix ingest: hosts + items via JSON-RPC.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from ..config import ZBX_TOKEN, ZBX_URL
from ..logging_config import get_logger
from .base import create_table, db, insert_raw

log = get_logger("ingest.zbx")


def _zbx_call(method: str, params: dict[str, Any]) -> Any:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "auth": ZBX_TOKEN,
        "id": 1,
    }
    r = requests.post(ZBX_URL, json=payload, timeout=(10, 30))
    r.raise_for_status()
    resp = r.json()
    if "error" in resp:
        log.error({"event": "api_error", "source": "zbx", "method": method, "message": str(resp["error"])})
        return None
    return resp.get("result")


def _transform_hosts(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform raw Zabbix host data into cleaned records.

    Returns (records, columns). Pure function — no I/O.
    """
    records = []
    for item in items:
        groups = ", ".join(g["name"] for g in item.get("groups", []))
        inv = item.get("inventory", {}) or {}
        ifaces = item.get("interfaces", [])
        ip = ifaces[0].get("ip", "") if ifaces else ""
        records.append(
            {
                "hostid": item.get("hostid", ""),
                "hostname": item.get("host", ""),
                "name": item.get("name", ""),
                "status": item.get("status", ""),
                "groups": groups,
                "inventory_os": inv.get("os", ""),
                "inventory_type": inv.get("type", ""),
                "inventory_serial": inv.get("serial_no_a", ""),
                "inventory_mac": inv.get("macaddress_a", ""),
                "inventory_location": inv.get("location", ""),
                "ip_address": ip,
                "raw_json": json.dumps(item),
            }
        )
    columns = [
        "hostid",
        "hostname",
        "name",
        "status",
        "groups",
        "inventory_os",
        "inventory_type",
        "inventory_serial",
        "inventory_mac",
        "inventory_location",
        "ip_address",
    ]
    return records, columns


def ingest() -> int:
    conn = db("zbx")
    total = 0

    # --- Hosts ---
    items = _zbx_call(
        "host.get",
        {
            "output": ["hostid", "host", "name", "status"],
            "selectGroups": ["name"],
            "selectInventory": [
                "os",
                "os_short",
                "os_full",
                "type",
                "type_full",
                "serial_no_a",
                "serial_no_b",
                "macaddress_a",
                "macaddress_b",
                "tag",
                "location",
            ],
            "selectInterfaces": ["ip", "dns", "port", "type"],
            "selectTags": ["tag", "value"],
        },
    )
    if items:
        records, columns = _transform_hosts(items)
        with conn:
            conn.execute("DROP TABLE IF EXISTS hosts")
            create_table(conn, "hosts", columns)
            n = insert_raw(conn, "hosts", records)
        log.info({"event": "inserted", "source": "zbx", "table": "hosts", "rows": n})
        total += n

    conn.close()
    return total
