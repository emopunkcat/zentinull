#!/usr/bin/env python3
"""
Seed the mesh database with realistic demo data.

Generates a mesh.duckdb with synthetic device records from all 6 sources,
including intentional overlaps (same device reported by multiple sources)
so Splink-style entity resolution is visible in the consolidated devices table.

Usage:
    python scripts/seed_demo_data.py              # seed data/mesh.duckdb
    python scripts/seed_demo_data.py --force      # overwrite existing
    python scripts/seed_demo_data.py --rows 500   # scale (default: 80)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MESH_DB = ROOT / "data" / "mesh.duckdb"

# ── Synthetic data generators ─────────────────────────────────────────────────

_OUT_NAMES = [
    "WS-%03d",  # Workstations
    "LT-%03d",  # Laptops
    "SRV-%03d",  # Servers
    "PRT-%03d",  # Printers
    "NMS-%03d",  # Network monitors
]
_OS_LIST = [
    "Windows 11 Pro",
    "Windows 10 Enterprise",
    "Windows Server 2022",
    "Windows Server 2019",
    "Ubuntu 22.04 LTS",
    "Ubuntu 24.04 LTS",
    "Debian 12",
    "RHEL 9",
    "macOS Sonoma 14",
    "iOS 17",
    "Android 14",
    "VyOS 1.4",
    "Proxmox VE 8",
]
_MANUFACTURERS = [
    "Dell",
    "HP",
    "Lenovo",
    "Apple",
    "Cisco",
    "HPE",
    "Synology",
    "Ubiquiti",
    "Supermicro",
    "Fujitsu",
]
_MODELS = {
    "Dell": ["OptiPlex 7080", "Latitude 5440", "PowerEdge R750", "Precision 5820"],
    "HP": ["EliteDesk 800 G6", "ProBook 450 G10", "ProLiant DL380 Gen11"],
    "Lenovo": ["ThinkCentre M75s", "ThinkPad X1 Carbon Gen 12", "ThinkSystem SR650"],
    "Apple": ["Mac Mini M2 Pro", 'MacBook Pro 16" M3 Max'],
    "Cisco": ["ISR 4431", "Catalyst 9300", "Meraki MX250"],
    "HPE": ["ProLiant DL325 Gen11"],
    "Synology": ["RS1221+", "DS1823xs+"],
    "Ubiquiti": ["Dream Machine SE", "Switch Pro 48 PoE"],
    "Supermicro": ["AS-4125GS-TNRT"],
    "Fujitsu": ["Primergy RX2540 M7"],
}
_USERS = [
    "jdoe",
    "asmith",
    "mwilson",
    "klee",
    "rjohnson",
    "tnguyen",
    "pbrown",
    "lchen",
    "srivera",
    "dgarcia",
    "jpark",
    "hpatel",
    "cwright",
    "atorres",
    "nkumar",
]


def _rand_mac() -> str:
    return ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))


def _rand_serial() -> str:
    prefix = random.choice(["SN", "CN", "MXL"])
    return f"{prefix}{random.randint(1000000, 9999999)}"


def _rand_ip(subnet: str = "10.0") -> str:
    return f"{subnet}.{random.randint(1, 254)}.{random.randint(1, 254)}"


def _rand_imei() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(15))


def _name_to_clean(name: str) -> str:
    return name.lower().replace("-", "")


# ── Device templates ──────────────────────────────────────────────────────────


def _build_device_templates(count: int) -> list[dict]:
    """Generate a list of canonical devices with consistent properties."""
    templates: list[dict] = []
    names_pool = []
    prefix_idx = 0
    sub_idx = 1
    while len(names_pool) < count:
        prefix = _OUT_NAMES[prefix_idx % len(_OUT_NAMES)]
        names_pool.append(prefix % sub_idx)
        sub_idx += 1
        if sub_idx > 999:
            prefix_idx += 1
            sub_idx = 1

    for i in range(count):
        name = names_pool[i]
        mfr = random.choice(_MANUFACTURERS)
        model = random.choice(_MODELS.get(mfr, ["Generic"]))
        os = random.choice(_OS_LIST)
        user = random.choice(_USERS)
        mac = _rand_mac()
        serial = _rand_serial()
        ip = _rand_ip()
        imei = _rand_imei() if random.random() < 0.15 else ""
        templates.append(
            {
                "name": name,
                "name_clean": _name_to_clean(name),
                "mfr": mfr,
                "model": model,
                "os": os,
                "user": user,
                "mac": mac,
                "mac_clean": mac.replace(":", ""),
                "serial": serial,
                "ip": ip,
                "imei": imei,
            }
        )
    return templates


# ── Source weighting ──────────────────────────────────────────────────────────
# Each source has a coverage probability — how likely it is to report a given
# device.  Overlapping sources are the signal Splink uses for entity resolution.

SOURCE_WEIGHTS = {
    "sp": 0.55,  # SharePoint — good coverage
    "me": 0.50,  # ManageEngine — moderate
    "fg": 0.40,  # FortiGate — network visibility
    "zbx": 0.45,  # Zabbix — monitoring
    "ad": 0.60,  # Active Directory — best coverage (domain-joined)
    "sdp": 0.30,  # ServiceDesk Plus — ticket system (spottier)
}

# Field reliability per source — fields that appear when present
SOURCE_FIELDS: dict[str, list[str]] = {
    "sp": ["name", "mfr", "model", "serial", "os", "user", "ip"],
    "me": ["name", "mfr", "model", "serial", "os", "user"],
    "fg": ["name", "os", "ip", "mac"],
    "zbx": ["name", "ip", "os"],
    "ad": ["name", "serial", "os", "user", "ip", "mac"],
    "sdp": ["name", "mfr", "model", "serial", "os", "user", "mac", "imei"],
}


def _overlap_count() -> int:
    """Number of sources covering a device — weighted toward 1-3 overlapping."""
    r = random.random()
    if r < 0.30:
        return 1
    elif r < 0.60:
        return 2
    elif r < 0.80:
        return 3
    elif r < 0.93:
        return 4
    else:
        return random.randint(5, 6)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def seed_demo_data(row_count: int = 80, force: bool = False) -> int:
    """Generate and write demo mesh database.

    Returns:
        Number of source_records written.
    """
    import duckdb

    MESH_DB.parent.mkdir(parents=True, exist_ok=True)

    if MESH_DB.exists():
        if force:
            MESH_DB.unlink()
        else:
            print(f"  {MESH_DB} exists. Use --force to overwrite.")
            return 0

    from zentinull.api.schema import DEVICES_SQL, EVENTS_SQL, INDEXES_SQL, METRICS_SQL

    templates = _build_device_templates(row_count)
    now = datetime.now(UTC)

    source_records: list[tuple] = []
    cluster_id: str

    for idx, dev in enumerate(templates):
        cluster_id = f"c{idx + 1}"
        n_sources = _overlap_count()
        chosen_sources = random.sample(
            sorted(SOURCE_WEIGHTS.keys()),
            n_sources,
        )

        # Each source gets a slightly different view of the same device
        for src in chosen_sources:
            fields = SOURCE_FIELDS.get(src, ["name"])
            src_id = f"{src[:2]}_{random.randint(1000, 9999)}"

            # Apply per-source field reliability (not every source sees every field)
            rec = {
                "cluster_id": cluster_id,
                "source": src,
                "source_id": src_id,
                "name": dev["name"] if "name" in fields else "",
                "name_clean": dev["name_clean"] if "name" in fields else "",
                "serial_number": dev["serial"] if "serial" in fields and random.random() < 0.9 else "",
                "mac_address": dev["mac"] if "mac" in fields and random.random() < 0.85 else "",
                "mac_clean": dev["mac_clean"] if "mac" in fields and random.random() < 0.85 else "",
                "manufacturer": dev["mfr"] if "mfr" in fields else "",
                "model": dev["model"] if "model" in fields else "",
                "os": dev["os"] if "os" in fields else "",
                "assigned_user": dev["user"] if "user" in fields and random.random() < 0.8 else "",
                "ip_address": dev["ip"] if "ip" in fields else "",
                "imei": dev["imei"] if "imei" in fields and dev["imei"] else "",
            }
            source_records.append(tuple(rec.values()))

    # Write to DuckDB
    conn = duckdb.connect(str(MESH_DB) + ".tmp")

    # ── source_records table ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE source_records (
            cluster_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            name_clean TEXT DEFAULT '',
            serial_number TEXT DEFAULT '',
            mac_address TEXT DEFAULT '',
            mac_clean TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            model TEXT DEFAULT '',
            os TEXT DEFAULT '',
            assigned_user TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            imei TEXT DEFAULT ''
        )
    """)
    for row in source_records:
        conn.execute("INSERT INTO source_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)

    # ── devices (consolidated) ──────────────────────────────────────────
    conn.execute(DEVICES_SQL)

    # ── metrics ─────────────────────────────────────────────────────────
    conn.execute(METRICS_SQL)

    metric_names = ["cpu_pct", "memory_pct", "disk_pct", "network_in_bps", "network_out_bps"]
    for idx, _ in enumerate(templates):
        cid = f"c{idx + 1}"
        for src in sorted(SOURCE_WEIGHTS.keys()):
            for mn in metric_names:
                if random.random() < 0.6:  # not every source reports every metric
                    continue
                recorded_at = now - timedelta(
                    hours=random.randint(0, 72),
                    minutes=random.randint(0, 59),
                )
                if mn == "cpu_pct":
                    val = round(random.triangular(5, 95, 30), 1)
                elif mn == "memory_pct":
                    val = round(random.triangular(20, 95, 55), 1)
                elif mn == "disk_pct":
                    val = round(random.triangular(10, 98, 60), 1)
                elif mn == "network_in_bps":
                    val = round(random.uniform(1e3, 1e8), 0)
                else:
                    val = round(random.uniform(1e3, 5e7), 0)
                conn.execute(
                    "INSERT INTO metrics (cluster_id, source, metric_name, value, text_value, tags, recorded_at, ingested_at) "
                    "VALUES (?, ?, ?, ?, NULL, [], ?, ?)",
                    (cid, src, mn, val, recorded_at, now),
                )

    # ── events ──────────────────────────────────────────────────────────
    conn.execute(EVENTS_SQL)

    event_types = [
        ("alert", "info"),
        ("warning", "warning"),
        ("critical", "critical"),
        ("recovery", "info"),
        ("info", "info"),
        ("maintenance", "info"),
    ]
    event_templates = [
        "CPU usage above threshold",
        "Disk space low — {pct}% used",
        "Host unreachable from collector {src}",
        "Memory utilization at {pct}%",
        "Service restarted on {name}",
        "Patching completed on {name}",
        "Certificate expiring in {days} days",
        "Network link down on interface {iface}",
        "Backup completed for {name}",
        "Failed login attempt from {ip}",
    ]

    for idx, dev in enumerate(templates[: min(row_count, 50)]):
        cid = f"c{idx + 1}"
        n_events = random.randint(0, 4)
        for _ in range(n_events):
            etype, sev = random.choice(event_types)
            recorded_at = now - timedelta(
                hours=random.randint(0, 168),
                minutes=random.randint(0, 59),
            )
            detail = random.choice(event_templates).format(
                pct=random.randint(75, 99),
                name=dev["name"],
                src=random.choice(list(SOURCE_WEIGHTS.keys())),
                days=random.randint(1, 90),
                iface=f"eth{random.randint(0, 4)}",
                ip=dev["ip"],
            )
            conn.execute(
                "INSERT INTO events (cluster_id, source, event_type, detail, severity, recorded_at, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, random.choice(list(SOURCE_WEIGHTS.keys())), etype, detail, sev, recorded_at, now),
            )

    # ── indexes ─────────────────────────────────────────────────────────
    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
    conn.close()
    os.replace(str(MESH_DB) + ".tmp", str(MESH_DB))

    dev_count = len(templates)
    rec_count = len(source_records)
    overlap_pct = 100 * rec_count // max(dev_count, 1)

    print(f"  Seeded {MESH_DB}")
    print(f"  Devices:     {dev_count}")
    print(f"  Records:     {rec_count}  ({overlap_pct}% overlap — avg {rec_count / dev_count:.1f} sources/device)")
    print("  Metrics:     seeded with CPU, memory, disk, network")
    print("  Events:      alerts, warnings, and info events")
    print("  Ready:       docker compose up  or  python serve.py start")

    return rec_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo data for Zentinull mesh database")
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing mesh.duckdb",
    )
    parser.add_argument(
        "--rows",
        "-n",
        type=int,
        default=80,
        help="Number of devices to generate (default: 80)",
    )
    args = parser.parse_args()
    seed_demo_data(row_count=args.rows, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
