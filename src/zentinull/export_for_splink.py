"""
Export all SQLite source DBs to a single Splink-compatible CSV.
Splink expects: one CSV with a 'source' column OR separate CSVs per source.
We use the single-CSV approach (easier to manage).
"""

import csv
import sqlite3
from pathlib import Path

from .logging_config import get_logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
OUT_DIR = Path(__file__).resolve().parent.parent.parent / "export" / "csv"

# Which tables to export as device records (skip reference tables)
DEVICE_TABLES = {
    "sp": (
        "sp_devices",
        [
            "sharepoint_id",
            "title",
            "serialnumber",
            "assetnumber",
            "ethmac",
            "wlanmac",
            "manufacturerstring",
            "devicemodel",
            "assigneduserstring",
            "status",
            "operating_system",
        ],
    ),
    "me_ec": (
        "computers",
        [
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
            "ip_address",
        ],
    ),
    "me_mdm": (
        "mdm_devices",
        [
            "device_id",
            "serial_number",
            "imei",
            "udid",
            "name",
            "model",
            "os_version",
            "user_email",
            "platform",
            "last_seen",
        ],
    ),
    "fg": ("clients", ["mac", "ip", "hostname", "ssid", "vlan", "user_name", "os", "manufacturer", "model"]),
    "zbx": (
        "hosts",
        [
            "hostid",
            "hostname",
            "name",
            "groups",
            "inventory_os",
            "inventory_type",
            "inventory_serial",
            "inventory_mac",
            "inventory_location",
            "ip_address",
        ],
    ),
    "ad": (
        "computers",
        ["sam_account_name", "dns_host_name", "operating_system", "os_version", "description", "location"],
    ),
    "sdp": ("assets", ["asset_id", "name", "serial_number", "asset_tag", "model", "manufacturer", "assigned_user"]),
}

# Splink field names (unified across sources) — these become the CSV columns
SPLINK_FIELDS = [
    "source",  # which source system
    "source_id",  # original ID in that system
    "name",  # device name / hostname / title (raw)
    "name_clean",  # normalized: lowercase, strip domain suffix
    "serial_number",  # matches across SP, ME, MDM, SDP, ZBX
    "mac_address",  # matches across SP, FG, ME, ZBX, MDM (raw)
    "mac_clean",  # normalized: lowercase, strip :-.,
    "asset_tag",  # SP + SDP only
    "manufacturer",  # hardware vendor
    "model",  # device model
    "os",  # operating system
    "os_version",  # OS version
    "assigned_user",  # who uses this device
    "ip_address",  # network location
    "imei",  # MDM mobile identifier
]

# Map each source's column names → Splink unified field names
FIELD_MAP = {
    "sp": {
        "sharepoint_id": "source_id",
        "title": "name",
        "serialnumber": "serial_number",
        "ethmac": "mac_address",
        "wlanmac": "mac_address",
        "manufacturerstring": "manufacturer",
        "devicemodel": "model",
        "assigneduserstring": "assigned_user",
        "operating_system": "os",
    },
    "me_ec": {
        "resource_id": "source_id",
        "name": "name",
        "serial_number": "serial_number",
        "mac_address": "mac_address",
        "manufacturer": "manufacturer",
        "model": "model",
        "os_name": "os",
        "os_version": "os_version",
        "assigned_user": "assigned_user",
        "ip_address": "ip_address",
    },
    "me_mdm": {
        "device_id": "source_id",
        "name": "name",
        "serial_number": "serial_number",
        "model": "model",
        "os_version": "os_version",
        "user_email": "assigned_user",
        "imei": "imei",
    },
    "fg": {
        "mac": "mac_address",
        "ip": "ip_address",
        "hostname": "name",
        "user_name": "assigned_user",
        "os": "os",
        "manufacturer": "manufacturer",
        "model": "model",
    },
    "zbx": {
        "hostid": "source_id",
        "hostname": "name",
        "name": "name",
        "inventory_os": "os",
        "inventory_serial": "serial_number",
        "inventory_mac": "mac_address",
        "ip_address": "ip_address",
    },
    "ad": {
        "sam_account_name": "source_id",
        "dns_host_name": "name",
        "operating_system": "os",
        "os_version": "os_version",
    },
    "sdp": {
        "asset_id": "source_id",
        "name": "name",
        "serial_number": "serial_number",
        "asset_tag": "asset_tag",
        "model": "model",
        "manufacturer": "manufacturer",
        "assigned_user": "assigned_user",
    },
}

log = get_logger("export")


def export() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_rows = []
    for source_key, (table, _cols) in DEVICE_TABLES.items():
        db_file = source_key.split("_")[0]  # "me_ec" → "me", "me_mdm" → "me"
        db_path = DATA_DIR / f"{db_file}.sqlite"
        if not db_path.exists():
            log.warning({"event": "skip", "source": source_key, "reason": "db_not_found", "path": str(db_path)})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Check if table exists
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if table not in tables:
            log.warning({"event": "skip", "source": source_key, "table": table, "reason": "table_not_found"})
            conn.close()
            continue

        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": source_key, "table": table, "reason": "error", "error": str(e)})
            conn.close()
            continue

        mapper = FIELD_MAP.get(source_key, {})
        for row in rows:
            row_dict = dict(row)
            rec = {f: "" for f in SPLINK_FIELDS}
            rec["source"] = source_key
            for col_name, splink_field in mapper.items():
                val = row_dict.get(col_name, "")
                if val and str(val).strip():
                    rec[splink_field] = str(val).strip()
            # Add normalized fields for better matching
            name_raw = rec.get("name", "")
            rec["name_clean"] = name_raw.lower().split(".")[0] if name_raw else ""
            mac_raw = rec.get("mac_address", "")
            mac_clean = mac_raw.lower().replace(":", "").replace("-", "").replace(".", "").split(",")[0]
            rec["mac_clean"] = mac_clean if len(mac_clean) == 12 else ""
            if not rec["source_id"]:
                rec["source_id"] = str(row_dict.get("id", ""))
            all_rows.append(rec)

        conn.close()
        log.info(
            {
                "event": "exported",
                "source": source_key,
                "records": len([r for r in all_rows if r["source"] == source_key]),
            }
        )

    # Write CSV
    out_path = OUT_DIR / "devices.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SPLINK_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    # Summary
    log.info({"event": "export_complete", "total_records": len(all_rows), "path": str(out_path)})
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
