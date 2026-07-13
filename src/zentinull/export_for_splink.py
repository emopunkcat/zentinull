"""
Export all SQLite source DBs to a single Splink-compatible CSV.
Splink expects: one CSV with a 'source' column OR separate CSVs per source.
We use the single-CSV approach (easier to manage).
"""

import csv
import json
import sqlite3

from .config import CSV_DIR, DATA_DIR
from .contracts import SPLINK_FIELDS
from .logging_config import get_logger

#: CSV output directory for Splink
OUT_DIR = CSV_DIR

# Which tables to export as device records (columns auto-detected via PRAGMA table_info)
DEVICE_TABLES = {
    "sp": "sp_devices",
    "me_ec": "computers",
    "me_mdm": "mdm_devices",
    "fg": "clients",
    "zbx": "hosts",
    "ad": "computers",
    "sdp": "assets",
}


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
    "zbx": {
        "hostid": "source_id",
        "name": "name",
        "inventory_os": "os",
        "inventory_serial": "serial_number",
        "inventory_mac": "mac_address",
        "ip_address": "ip_address",
    },
}

log = get_logger("export")

# Computed fields — never auto-map from source column names
_COMPUTED_FIELDS = frozenset({"source", "name_clean", "mac_clean", "extra_attributes"})
# System columns — never auto-map or include in extra_attributes
_SYSTEM_COLS = frozenset({"id", "ingested_at", "raw_json"})


def export() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-compute case-insensitive SPLINK_FIELDS lookup for implicit mapping
    splink_lower: dict[str, str] = {sf.lower(): sf for sf in SPLINK_FIELDS if sf not in _COMPUTED_FIELDS}

    all_rows: list[dict[str, str]] = []
    for source_key, table in DEVICE_TABLES.items():
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

        # Auto-detect typed columns from schema
        col_rows = conn.execute(f"SELECT name FROM pragma_table_info('{table}')").fetchall()
        typed_cols = [r[0] for r in col_rows]

        # Build mapper: explicit FIELD_MAP entries first, then implicit name matches
        explicit = FIELD_MAP.get(source_key, {})
        mapper = dict(explicit)
        mapped_source_cols = set(explicit.keys())
        for col in typed_cols:
            if col in _SYSTEM_COLS or col in mapped_source_cols:
                continue
            match = splink_lower.get(col.lower())
            if match and match not in mapper.values():
                mapper[col] = match
                mapped_source_cols.add(col)

        # Extra-attribute source columns: typed but not mapped and not system
        extra_source_cols = [c for c in typed_cols if c not in _SYSTEM_COLS and c not in mapped_source_cols]

        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except Exception as e:
            log.warning({"event": "skip", "source": source_key, "table": table, "reason": "error", "error": str(e)})
            conn.close()
            continue

        for row in rows:
            row_dict = dict(row)
            rec = {f: "" for f in SPLINK_FIELDS}
            rec["source"] = source_key
            for col_name, splink_field in mapper.items():
                val = row_dict.get(col_name, "")
                if val and str(val).strip():
                    val_str = str(val).strip()
                    existing = rec.get(splink_field, "")
                    if existing:
                        if val_str not in existing.split(","):
                            rec[splink_field] = f"{existing},{val_str}"
                    else:
                        rec[splink_field] = val_str
            # Add normalized fields for better matching
            name_raw = rec.get("name", "")
            rec["name_clean"] = name_raw.lower().split(".")[0] if name_raw else ""
            mac_raw = rec.get("mac_address", "")
            mac_clean = mac_raw.lower().replace(":", "").replace("-", "").replace(".", "").split(",")[0]
            rec["mac_clean"] = mac_clean if len(mac_clean) == 12 else ""
            if not rec["source_id"]:
                rec["source_id"] = str(row_dict.get("id", ""))

            # Extra attributes: unmapped typed columns + raw_json extras
            extra: dict[str, str] = {}
            for col in extra_source_cols:
                val = row_dict.get(col, "")
                if val and str(val).strip():
                    extra[col] = str(val).strip()
            # Parse raw_json to capture new API fields not in typed columns
            raw = row_dict.get("raw_json", "")
            if raw and isinstance(raw, str):
                try:
                    raw_dict = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    raw_dict = {}
                if isinstance(raw_dict, dict):
                    for k, v in raw_dict.items():
                        if (
                            k not in typed_cols
                            and k not in extra
                            and k not in _SYSTEM_COLS
                            and k not in mapped_source_cols
                            and v
                            and str(v).strip()
                        ):
                            extra[k] = str(v).strip()
            rec["extra_attributes"] = json.dumps(extra) if extra else ""
            all_rows.append(rec)

        conn.close()
        source_count = len([r for r in all_rows if r["source"] == source_key])
        log.info({"event": "exported", "source": source_key, "records": source_count})

    # Write CSV
    out_path = OUT_DIR / "devices.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SPLINK_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    # Summary
    log.info({"event": "export_complete", "total_records": len(all_rows), "path": str(out_path)})

    if not all_rows:
        log.warning({"event": "export_empty", "reason": "no_records_from_any_source"})
        out_path.unlink(missing_ok=True)
        return
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
