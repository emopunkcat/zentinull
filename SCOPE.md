# Zentinull — Scope & Architecture

## Premise

Current Zentinull v9 has 100 bugs rooted in one architectural mistake: **ingest and entity resolution are coupled**. The pipeline tries to unify records during ingestion via field_map, pk_fn, and identity resolution — and gets it wrong pervasively.

**Clean separation:**
- Layer 1: Dumb ingestors. Pull API → dump to SQLite. No unification, no field mapping, no dedup.
- Layer 2: Splink. Purpose-built entity resolution engine. ML-powered, trainable, auditable.
- Layer 3: API (later). Consume matched clusters, serve golden records.

---

## Sources (7)

| # | Source | API Type | Auth | Est. Records | Key Fields |
|---|--------|----------|------|-------------|------------|
| 1 | SharePoint Devices | MS Graph REST | MSAL (client secret) | 581 | serial, mac, asset_tag, name |
| 2 | ManageEngine EC | Zoho REST | OAuth2 refresh | ~65 | serial, mac, resource_id, name |
| 3 | ManageEngine MDM | Zoho REST | OAuth2 refresh | 111 | serial, imei, udid, user_email |
| 4 | FortiGate | REST v2 | Bearer token | ~200 clients | mac, ip, hostname, ssid |
| 5 | Zabbix | JSON-RPC | API token | ~67 hosts | hostid, hostname, inventory |
| 6 | Active Directory | LDAP | Bind | ~100 computers | dnsHostName, operatingSystem |
| 7 | ServiceDesk Plus | Zoho REST | OAuth2 refresh | ~300 assets | serial, asset_tag, name |

**Total: ~1,500 records across 7 sources, representing ~600 unique devices.**

---

## Directory Structure

```
C:\Users\jejo\zentinull\
├── SCOPE.md                  # This file
├── ingestors\                # Python — one module per source
│   ├── __init__.py
│   ├── base.py               # Shared: get_db(), fetch_pages(), create_table()
│   ├── sharepoint.py         # ~80 lines
│   ├── manageengine.py       # ~100 lines (EC + MDM)
│   ├── fortigate.py          # ~120 lines (6 endpoints)
│   ├── zabbix.py             # ~80 lines
│   ├── ad.py                 # ~80 lines
│   └── sdp.py                # ~100 lines
├── data\                     # SQLite databases (one per source)
│   ├── sp.sqlite
│   ├── me.sqlite
│   ├── fg.sqlite
│   ├── zbx.sqlite
│   ├── ad.sqlite
│   └── sdp.sqlite
├── export\                   # SQLite → CSV for Splink
│   └── export_all.py         # Dump each source to data/csv/*.csv
├── splink\                    # Splink configuration
│   ├── config.json           # Training config: fields, match types, blocking
│   ├── training-data\        # Labeled pairs for initial training
│   │   └── labeled.csv       # id1, id2, label (MATCH/NO_MATCH)
│   └── models\               # Trained model output
├── run_ingest.py             # One command: pull all sources
└── requirements.txt          # requests, msal, ldap3, pyodbc (AD)
```

---

## Layer 1: Ingestors

### Design rules

1. **One table per source, not per endpoint.** SP → `sp.sqlite:devices`. FG → `fg.sqlite:clients` + `fg.sqlite:dhcp_leases` etc. (multi-table where endpoints serve different entity types).
2. **Store raw JSON + extracted fields.** Every row has a `raw_json` TEXT column with the full API response. Extracted columns are just the obvious identity fields (serial, mac, name, etc.) — the rest lives in raw_json. No field_map. No normalization.
3. **No dedup, no identity resolution.** If the API returns the same record twice, store it twice. Splink handles dedup.
4. **No dead letter table.** If a field is missing, it's NULL. If a record is malformed, log it and move on.
5. **Schema is fixed per source.** Defined in each ingestor module. No `ALTER TABLE` at runtime. If the API changes, update the ingestor.
6. **Reuse existing auth and fetch code.** `ingestion/auth.py` and `ingestion/fetch.py` from sentinull are battle-tested. Copy, don't rewrite.

### Per-source schemas

**sp.sqlite:devices**
```sql
CREATE TABLE devices (
    id INTEGER PRIMARY KEY,
    sharepoint_id TEXT,
    serial_number TEXT,
    asset_number TEXT,
    eth_mac TEXT,
    wlan_mac TEXT,
    name TEXT,
    manufacturer TEXT,
    model TEXT,
    assigned_user TEXT,
    status TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**me.sqlite:computers**
```sql
CREATE TABLE computers (
    id INTEGER PRIMARY KEY,
    resource_id TEXT,
    serial_number TEXT,
    mac_address TEXT,
    name TEXT,
    manufacturer TEXT,
    model TEXT,
    os_name TEXT,
    os_version TEXT,
    assigned_user TEXT,
    last_seen TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE mdm_devices (
    id INTEGER PRIMARY KEY,
    device_id TEXT,
    serial_number TEXT,
    imei TEXT,
    udid TEXT,
    name TEXT,
    model TEXT,
    os_version TEXT,
    user_email TEXT,
    enrolled_at TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**fg.sqlite:clients** (+ dhcp_leases, known_devices, arp_table, interfaces, vpn_sessions)
```sql
CREATE TABLE clients (
    id INTEGER PRIMARY KEY,
    mac TEXT,
    ip TEXT,
    hostname TEXT,
    ssid TEXT,
    vlan TEXT,
    user_name TEXT,
    os TEXT,
    manufacturer TEXT,
    model TEXT,
    signal TEXT,
    ap_name TEXT,
    interface TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**zbx.sqlite:hosts**
```sql
CREATE TABLE hosts (
    id INTEGER PRIMARY KEY,
    hostid TEXT,
    hostname TEXT,
    visible_name TEXT,
    status TEXT,
    groups TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**ad.sqlite:computers**
```sql
CREATE TABLE computers (
    id INTEGER PRIMARY KEY,
    sam_account_name TEXT,
    dns_host_name TEXT,
    operating_system TEXT,
    os_version TEXT,
    distinguished_name TEXT,
    last_logon TEXT,
    created TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

**sdp.sqlite:assets**
```sql
CREATE TABLE assets (
    id INTEGER PRIMARY KEY,
    asset_id TEXT,
    serial_number TEXT,
    asset_tag TEXT,
    name TEXT,
    model TEXT,
    manufacturer TEXT,
    assigned_user TEXT,
    status TEXT,
    raw_json TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);
```

### Reuse from sentinull

| File | What we reuse |
|------|--------------|
| `APPLICATION/server/ingestion/auth.py` | MSALAuth, OAuth2RefreshAuth, APIKeyAuth, ZabbixAuth, LDAPBindAuth — copy as-is |
| `APPLICATION/server/ingestion/fetch.py` | PaginatedFetcher — copy, strip TableEndpoint dependency |
| `APPLICATION/server/config.py` | Sentinull config loader — just for reading API keys from sentinel.json |
| `CONFIG/sentinel.json` | Read-only reference for API endpoints, credentials, OAuth tokens |

**We do NOT reuse:** base.py, pipeline.py, config_models.py, mapping.py, identity.py, observability.py — those are the unification layer we're replacing.

### `base.py` (new, minimal)

```python
import sqlite3, json, logging
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

def get_db(source_name: str) -> sqlite3.Connection:
    """Return connection to data/{source}.sqlite, creating if needed."""
    db_path = DATA_DIR / f"{source_name}.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def insert_records(conn, table: str, records: list[dict], extra_cols: dict = None):
    """INSERT raw records. No upsert, no dedup — Splink handles that."""
    if not records:
        return 0
    cols = list(records[0].keys())
    if extra_cols:
        cols.extend(extra_cols.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    rows = []
    for r in records:
        row = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in r.values()]
        if extra_cols:
            row.extend(extra_cols.values())
        rows.append(tuple(row))
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)
```

### Example ingestor (SharePoint — ~60 lines)

```python
from .base import get_db, insert_records
from ingestion.auth import MSALAuth
from ingestion.fetch import PaginatedFetcher
from config import sharepoint_config

TABLES = {
    "devices": {
        "url": "https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/items?expand=fields",
        "columns": ["sharepoint_id", "serial_number", "asset_number", "eth_mac",
                     "wlan_mac", "name", "manufacturer", "model", "assigned_user", "status"],
        "map": lambda item: {
            "sharepoint_id": item.get("id", ""),
            "serial_number": fields.get("SerialNumber", ""),
            "asset_number": fields.get("AssetNumber", ""),
            "eth_mac": fields.get("ETHMAC", ""),
            "wlan_mac": fields.get("WLANMAC", ""),
            "name": fields.get("Title", ""),
            "manufacturer": fields.get("ManufacturerString", ""),
            "model": fields.get("DeviceModel", ""),
            "assigned_user": fields.get("AssignedUserString", ""),
            "status": fields.get("Status", ""),
        }
    }
}

def ingest():
    cfg = sharepoint_config()
    auth = MSALAuth(cfg.tenant_id, cfg.client_id, cfg.client_secret, "https://graph.microsoft.com/.default")
    fetcher = PaginatedFetcher()
    conn = get_db("sp")
    
    for table_name, tdef in TABLES.items():
        records = []
        for page in fetcher.fetch_pages(url=tdef["url"], auth=auth):
            for item in page.get("value", []):
                fields = item.get("fields", {})
                record = tdef["map"](item)
                record["raw_json"] = json.dumps(item)
                records.append(record)
        n = insert_records(conn, table_name, records)
        print(f"  sp/{table_name}: {n} rows")
    conn.close()
```

---

## Layer 2: Splink

### What Splink does

Splink is an open-source entity resolution framework (Java, Apache-licensed). It takes multiple input sources, learns which records refer to the same entity, and outputs clusters.

**Input:** CSV files (one per source), config JSON, optional training data.
**Output:** Cluster assignments — which records from different sources belong to the same device.

### Why Splink fits this problem

| Problem | Current Zentinull | Splink |
|---------|-----------------|-------|
| MAC format differences | Regex in mapping.py | Fuzzy matching (`fuzzy` match type) |
| Name variations | Exact match only | `fuzzy` with configurable threshold |
| Serial missing from some sources | pk_fn=None → append-only | Blocks on other fields, learns weights |
| New device appears | Manual fingerprint tuning | Model adapts via retraining |
| Cross-source dedup | Complex identity resolution chain | Built-in, configurable blocking |

### Splink config (`splink/config.json`)

```json
{
  "fieldDefinitions": [
    {"fieldName": "id", "matchType": "dont_use", "fields": "id"},
    {"fieldName": "source", "matchType": "dont_use", "fields": "source"},
    {"fieldName": "serial_number", "matchType": "fuzzy", "fields": "serial_number"},
    {"fieldName": "mac_address", "matchType": "fuzzy", "fields": "mac_address"},
    {"fieldName": "name", "matchType": "fuzzy", "fields": "name"},
    {"fieldName": "manufacturer", "matchType": "fuzzy", "fields": "manufacturer"},
    {"fieldName": "model", "matchType": "fuzzy", "fields": "model"},
    {"fieldName": "asset_number", "matchType": "exact", "fields": "asset_number"},
    {"fieldName": "assigned_user", "matchType": "fuzzy", "fields": "assigned_user"}
  ],
  "output": [{"name": "output", "format": "csv"}],
  "data": [
    {"name": "sp", "format": "csv", "props": {"location": "data/csv/sp_devices.csv"}},
    {"name": "me", "format": "csv", "props": {"location": "data/csv/me_computers.csv"}},
    {"name": "mdm", "format": "csv", "props": {"location": "data/csv/me_mdm.csv"}},
    {"name": "fg", "format": "csv", "props": {"location": "data/csv/fg_clients.csv"}},
    {"name": "zbx", "format": "csv", "props": {"location": "data/csv/zbx_hosts.csv"}},
    {"name": "ad", "format": "csv", "props": {"location": "data/csv/ad_computers.csv"}},
    {"name": "sdp", "format": "csv", "props": {"location": "data/csv/sdp_assets.csv"}}
  ],
  "modelId": 100,
  "numPartitions": 4,
  "labelDataSampleSize": 0.5,
  "blocking": [
    {"blockFields": ["serial_number"]},
    {"blockFields": ["mac_address"]},
    {"blockFields": ["name"]}
  ]
}
```

### Training approach

1. **Initial seed:** Manually label ~100 record pairs. Pick obvious matches (same serial across SP+ME, same MAC across FG+SP) and obvious non-matches (different manufacturers, different device types).
2. **Active learning:** Splink presents uncertain pairs → label those → retrain → repeat until accuracy stabilizes.
3. **Output:** Splink produces `clusters.csv` with columns: `cluster_id, source, record_id`. Every record assigned to a cluster. Cluster of size 1 = unmatched.

### Export script (`export/export_all.py`)

Reads each SQLite DB, joins relevant tables, writes flat CSV with `source` column. Splink expects one CSV per source (or one unified CSV with `source` column — we'll use the latter for simplicity).

```python
# For each source DB:
#   SELECT *, 'sp' as source FROM devices  → sp_devices.csv
#   SELECT *, 'me' as source FROM computers → me_computers.csv
#   etc.
```

---

## What changes vs. current Zentinull
| Aspect | Current Zentinull | Splink Zentinull |
|--------|-----------------|----------------|
| Ingestion code per source | ~250-530 lines | ~60-120 lines |
| Field mapping | 3-tier system (config → normalize → infer) | Hardcoded in record builder |
| Dedup | pk_fn + upsert_record per source | Splink model |
| Identity resolution | resolve_or_register_sentinel() chain | Splink clustering |
| Dead letters | 36.7M rows in dead_letter table | None. Missing fields → NULL. |
| Raw cache | 1.36M rows, no TTL | Stored as `raw_json` column per record |
| Sync metrics | 130K rows, useless granularity | Simple row counts per source |
| Field history | 33K rows, 0 real deltas | Not needed (Splink model captures drift) |
| Schema changes | `ALTER TABLE` at runtime | Fixed per ingestor |
| Test coverage | 427 mocked tests, 0 integration | Each ingestor tested against real API |
| Config file | 1200-line sentinel.json, secrets embedded | Minimal config, secrets from env vars |
| Docker image | Secrets in layers | No Docker in scope yet |

---

## Implementation plan

### Phase 1: Ingestors (3-4 hours)

1. Copy `auth.py` and `fetch.py` from sentinull → `zentinull/ingestors/`
2. Write `base.py` (get_db, insert_records, create_tables)
3. Write each ingestor module (SP, ME, FG, Zabbix, AD, SDP)
4. Write `run_ingest.py` — imports all ingestors, runs them, prints summary
5. Test: run against live APIs, verify row counts

### Phase 2: Export (30 min)

1. Write `export/export_all.py` — SQLite → CSV per source with `source` column
2. Run, verify CSV output

### Phase 3: Splink setup (1-2 hours)

1. Install Splink (Java 11+, download release or use Docker)
2. Write `config.json` for device matching
3. Run `splink.sh --phase findTrainingData` → generates candidate pairs
4. Label ~100 pairs in `training-data/labeled.csv`
5. Run `splink.sh --phase train` → builds model
6. Run `splink.sh --phase match` → produces clusters

### Phase 4: Validate (1 hour)

1. Check cluster quality — do known devices (WS28, DC01, FS05) cluster correctly?
2. Inspect edge cases: devices with no serial, MAC-only matches, name collisions
3. Tune blocking keys and match thresholds
4. Retrain if needed

### Phase 5: API (out of scope for now)

Once clusters are solid, build a thin FastAPI layer that reads Splink output + SQLite source data → serves golden records.

---

## Open questions

1. **One SQLite per source vs. one DB with multiple tables?** Per-source is simpler, isolates failures, enables parallel ingest. Recommendation: per-source.

2. **Re-ingest strategy?** Full refresh (DROP TABLE, re-insert) vs. incremental (upsert by API ID). Recommendation: full refresh initially — datasets are small (<600 records each), sync time is dominated by API latency not DB writes.

3. **Splink on Windows?** Java-based, works on Windows. But we'll test. Docker alternative exists (`splink/splink` image).

4. **Training data: who labels?** jejo labels the first ~100 pairs. Active learning reduces labeling burden.

5. **SDP tickets/requests?** Skip for now — SDP assets table is the entity source. Requests and reference tables don't map to devices.

---

## Success criteria

- [ ] All 7 sources ingest successfully against live APIs
- [ ] Row counts match expected volumes (SP: 581, ME EC: ~65, MDM: 111, FG: ~200, ZBX: 67, AD: ~100, SDP: ~300)
- [ ] Splink produces clusters covering >80% of known cross-source matches
- [ ] No append-only growth — full refresh model means tables stay at source row count
- [ ] No config baked into code — API keys from sentinel.json (read-only) or env vars
- [ ] Each ingestor module is <150 lines
