"""
Zabbix ingest: hosts + items via JSON-RPC.
"""

from __future__ import annotations

import json
import os

import requests

from ..logging_config import get_logger
from .base import create_table, db, insert_raw

log = get_logger("ingest.zbx")

ZBX_URL = os.environ.get("ZBX_URL", "https://zabbix.example.com/api_jsonrpc.php")
ZBX_TOKEN = os.environ.get("ZBX_TOKEN", "")


def _zbx_call(method: str, params: dict) -> dict | None:  # type: ignore[type-arg]
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "auth": ZBX_TOKEN,
        "id": 1,
    }
    r = requests.post(ZBX_URL, json=payload, timeout=30)
    r.raise_for_status()
    resp = r.json()
    if "error" in resp:
        log.error({"event": "api_error", "source": "zbx", "method": method, "message": str(resp["error"])})
        return None
    return resp.get("result")  # type: ignore[no-any-return]


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
        conn.execute("DROP TABLE IF EXISTS hosts")
        create_table(
            conn,
            "hosts",
            [
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
            ],
        )
        n = insert_raw(conn, "hosts", records)
        log.info({"event": "inserted", "source": "zbx", "table": "hosts", "rows": n})
        total += n

    conn.close()
    return total
