# Zentinull — Data Correlation Cheat Sheet

_Deep-dive: explicit and implicit data connections across 6 sources, 23 feeds, 5 DuckDB tables, 2 views, and 1 runtime SQLite join._

---

## Surface Contract: What the API Returns

### Identity Resolution

| Endpoint | Response Model | Key Fields |
|---|---|---|
| `GET /device/{q}` | `DeviceStory` | `cluster_id`, `device_name`, `source_count`, `sources[]`, `consolidated{field: [values]}`, `enriched{field: value}`, `records[SourceRecord]`, `sot{field: {value, source, priority}}`, `drift_audit[]` |
| `GET /device/{q}/trace` | `DeviceTraceResponse` | `device{}`, `sources[]`, `attachments[]`, `linked_devices[]`, `vlans[]`, `graph{nodes[], edges[]}` |
| `POST /batch` | `[DeviceStory\|null]` | JSON body `{"queries": ["ws28", "00:1a:2b:..."]}` |

**Lookup strategies (tried in order):** cluster_id → name → serial → MAC → IP → assigned_user → full-text search. No relevance scoring — first match by priority, ties broken by `source_count DESC`.

### Time-Series

| Endpoint | Time Window | Output |
|---|---|---|
| `GET /device/{q}/metrics` | `?hours=N` (default 24) | `MetricRecord[]` — raw datapoints: `metric_name`, `value`, `text_value`, `tags[]`, `recorded_at` |
| `GET /device/{q}/metric-summary` | `?hours=N` (default 24) | `{metric_name: {count, avg, max, min, latest}}` |
| `GET /device/{q}/stats` | 24h fixed | `{metrics: {name: {value, text, source, recorded_at}}, event_counts: {severity: count}}` |

**Metric names come from Zabbix key_** — `system.cpu.util[,idle]`, `vm.memory.size[available]`, `vfs.fs.size[/,free]`, `net.if.in[eth0]`, `icmppingloss`, `icmppingsec`. Query `?metric=cpu_pct&hours=168` to filter.

### Events

| Endpoint | Time Window | Output |
|---|---|---|
| `GET /device/{q}/timeline` | `?hours=N` (default 168=7d) | `EventRecord[]` — `event_type`, `detail`, `severity` (info/warning/critical), `recorded_at` |

### Attachments

| Endpoint | Output |
|---|---|
| `GET /device/{q}/attachments` | `AttachmentRecord[]` — `feed_key`, `source_id`, `field`, `value`, `confidence`, `payload{}` |

**Feed keys in play:** `zbx_items`, `sdp_requests`, `sp_employees`, `sp_accountinfo`, `sp_devicenotes`, `sp_componentpurchases`

### Discovery

| Endpoint | Output |
|---|---|
| `GET /search?q=...` | `ClusterInfo[]` — full-text across `device_name`, `serial_number`, `mac_address`, `assigned_user` |
| `GET /clusters` | `ClusterListResponse` — paginated, filterable: `?min_sources=N&source=fg` |
| `GET /anomalies` | Singletons, unnamed, no-serial, zombies, hardware drift |
| `GET /dashboard` | KPI: total clusters/records, per-source counts, coverage %, top clusters, source-count distribution, source combos |
| `GET /mesh` | Cross-source overlap: `by_source_count{2: N, 3: N, ...}`, `by_source_combo{"fg+sp": N, ...}`, `records_per_source` |

---

## Hidden Layer 1: The Device Trace Graph

_`/device/{q}/trace` builds a graph you won't see in any other endpoint._

### Node Types

| Type | What It Represents | Fields |
|---|---|---|
| `device` | The anchor cluster | `cluster_id`, `device_name`, `source_count`, `sources[]` |
| `source` | Per-source record within the cluster | `source`, `source_id`, `name`, `serial_number`, `mac_address`, `ip_address`, `assigned_user`, `manufacturer`, `model`, `os` |
| `attachment` | Linked record from a non-anchor feed | `feed_key`, `source_id`, `value`, `confidence` |
| `linked_device` | A different cluster sharing user/attachment with anchor | `cluster_id`, `device_name`, `linked_via` (why they're linked) |
| `vlan` | Subnet the device belongs to | `vlan_name`, `network_id` (CIDR), `network` |

### Edge Types

| Type | From → To | Meaning |
|---|---|---|
| `has_source` | device → source | This device cluster contains this source record |
| `has_attachment` | device → attachment | This attachment links to this device |
| `linked_by_user` | device → linked_device | Both have same `assigned_user` |
| `linked_by_attachment` | device → linked_device | Both share an attachment value (e.g., same accountinfo entry) |
| `in_vlan` | device → vlan | Device IP falls in this subnet |

### Hidden Correlation: `linked_by_attachment`

Two devices that share an attachment value get linked in the graph. Example: if `sp_accountinfo` has `DeviceString` = "WS28-LAPTOP" and it matches two different clusters (e.g., via name_clean on both the fg and me_ec records), those two devices become `linked_by_attachment`. This is an **implicit cross-device edge** — no explicit foreign key, just shared attachment token matching. The `attach.py::build_keyspace()` token index makes this possible.

---

## Hidden Layer 2: The Enriched View (`v_device_enriched`)

_31 concepts COALESCEd across ALL sources into one row per cluster. Accessible via `DeviceStory.enriched`._

The Valentine field registry maps raw keys across sources to unified concepts:

```
Concept → COALESCE(
    sp.column_or_v_extra_key,
    me_ec.column_or_v_extra_key,
    me_mdm.column_or_v_extra_key,
    fg.column_or_v_extra_key,
    zbx.column_or_v_extra_key,
    sdp.column_or_v_extra_key,
    ad.column_or_v_extra_key
)
```

### Enriched Concepts (31 total)

| Concept | Sources Contributing | Type |
|---|---|---|
| **purchase_cost** | sp, sdp | Financial |
| **purchase_order** | sp | Financial |
| **purchase_date** | sp | Financial |
| **total_cost** | sdp | Financial |
| **component_cost** | sp (ComponentPurchases) | Financial |
| **product_number** | sp | Hardware |
| **phone_number** | sp `MobileNumber` | Telecom |
| **iccid** | sp | Telecom |
| **mobile_service** | sp | Telecom |
| **sim_type** | sp | Telecom |
| **category** | sdp `category.name` | Classification |
| **form_factor** | sdp `product.product_type.display_name` | Classification |
| **tpm_version** | sp | Security |
| **branch_office** | me_ec | Location |
| **agent_version** | me_ec | Agent Health |
| **agent_status** | me_ec `installation_status` | Agent Health |
| **live_status** | me_ec `computer_live_status` | Agent Health |
| **last_scan_time** | me_ec `last_successful_scan` | Agent Health |
| **domain** | me_ec `domain_netbios_name` | Network |
| **os_build** | me_ec `build_number` | OS Detail |
| **os_service_pack** | me_ec `service_pack` | OS Detail |
| **battery_level** | me_mdm | Power |
| **udid** | me_mdm | MDM Identity |
| **owned_by** | me_mdm | Ownership |
| **is_supervised** | me_mdm | MDM Policy |
| **vlan** | fg `detected_interface` | Network |
| **is_online** | fg | Presence |
| **vdom** | fg | Network Segment |
| **location** | zbx `inventory.location` | Physical Location |

**Key insight:** `DeviceStory.enriched` is the richest single source of cross-source data. It merges financial (purchase_cost from SDP), agent health (agent_version from ME EC), MDM policy (is_supervised from ME MDM), and network presence (is_online from FG) into one flat dict per device. No other endpoint does this.

---

## Hidden Layer 3: Data Trapped in extra_attributes

_Fields present in raw JSON but never surfaced beyond `extra_attributes` dict. Accessible via `SourceRecord.extra_attributes` or `GET /diagnostics/unmapped-fields`._

### SharePoint — Trapped Fields (30+ unmapped keys)

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `fields.Comments` | HTML notes (3-4KB) | Full-text search for device history/notes |
| `fields.Location` | "Server Room B" | Physical location (already in enriched via zbx, but SP has it too) |
| `fields.Room` | "201" | Room-level location |
| `fields.Building` | "HQ" | Building-level location |
| `fields.AC_ReplacedDevice` | lookup ID | Lifecycle: which device replaced this one |
| `fields.AC_ReplacementDevice` | lookup ID | Lifecycle: which device this one replaced |
| `fields.MobileNumber` | "+1-555-..." | Phone number (already in enriched as `phone_number`) |
| `fields.ICCID` | SIM card ID | Telecom (already in enriched as `iccid`) |
| `fields.TPMVersion` | "2.0" | TPM version (already in enriched) |
| `fields.ProductNumber` | "5YJ23AV" | HP/Dell product SKU |
| `fields.SIMType` | "eSIM" | SIM type (already in enriched) |
| `fields.ViewCategory` | "Server" | SharePoint list category |
| `fields.PONumber` | "PO-2024-001" | Procurement cross-ref (already in enriched) |
| `fields.PurchaseDate` | "2024-01-15" | Procurement timeline |
| `fields.TotalofAllPurchasesforDevice` | "$1,234.00" | Total spend per device |

### FortiGate — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `purdue_level` | "3" | OT/ICS security zone (Purdue model) |
| `fortiswitch_port_id` | "port24" | Physical switch port — trace cable path |
| `fortiap_id` / `fortiap_name` / `fortiap_ssid` | "AP-3F-01" | WiFi AP association — physical location proxy |
| `is_online` | `true` | Real-time presence (already in enriched) |
| `vdom` | "root" | Virtual domain / network segment |
| `detected_interface` | "VLAN 20" | (already in enriched as `vlan`) |
| `hardware_version` | "v2.0" | Hardware revision |
| `device_type` | "Router" | FortiGate's own classification |
| `total_vuln_count` | 12 | Vulnerability count from FortiGuard |
| `active_start_time` / `last_seen` | timestamps | Session start / last activity |
| `dhcp_lease_status` | "active" | DHCP lease state |
| `host_src` | "dhcp" | How the host was discovered |

### ManageEngine MDM — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `device_type` | 1 (iOS), 2 (iPad), 3 (Windows), 4 (Mac) | Form factor classification (different from SDP's form_factor) |
| `is_supervised` | `true`/`false` | DEP supervision status (already in enriched) |
| `is_activation_lock_enabled` | `true`/`false` | iCloud lock — theft/loss indicator |
| `is_cloud_backup_enabled` | `true`/`false` | Backup compliance |
| `is_device_locator_enabled` | `true`/`false` | Tracking enabled |
| `lost_mode_status` | "disabled" | Lost mode state |
| `managed_status` | "Managed" | MDM enrollment state |
| `battery_level` | 85 | (already in enriched) |
| `device_capacity` | 64.0 (GB) | Storage capacity |
| `build_version` | "21F79" | iOS build number |
| `cellular_technology` | "5G" | Network technology |
| `udid` | device UDID | (already in enriched) |
| `owned_by` | "Corporate" | (already in enriched) |
| `eas_device_identifier` | Exchange ID | Mail sync identity |
| `customer_id` / `customer_name` | tenant identifier | Multi-tenant segmentation |
| `is_removed` | `false` | Device retired from MDM |

### Active Directory — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `dn` | `CN=WS28,OU=Workstations,DC=corp,DC=local` | OU hierarchy — extract department/site |
| `description` | "John's laptop" | Human-readable label |
| `location` | "Floor 3" | AD site location |
| `userAccountControl` | 4096 (WORKSTATION_TRUST_ACCOUNT) | Account flags: disabled, locked, password-expired |
| `whenCreated` | timestamp | Join date — device age |
| `whenChanged` | timestamp | Last directory update |

### SDP — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `created_by.email_id` | "user@corp.com" | Cross-ref with `sp_employees` for user identity |
| `created_by.department.name` | "Engineering" | Organizational context |
| `created_by.job_title` | "IT Manager" | Role context |
| `created_by.mobile` | phone | Contact |
| `purchase_cost` | 1299.00 | (already in enriched) |
| `total_cost` | 1450.00 | (already in enriched) |
| `current_cost` | 800.00 | Depreciated value |
| `is_loanable` / `is_loaned` | `true`/`false` | Asset pool status |
| `support_vendor.name` / `.email` | "Dell Support" | Vendor contact |
| `last_updated_by.name` | "Jane Smith" | Last editor |

### Zabbix — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `groups` | `[{"name":"ML-Servers"}]` | Device class: Server vs Workstation — could tag all devices |
| `interfaces` | `[{ip, dns, port, type}]` | Network interface details |
| `status` | 0 (enabled) / 1 (disabled) | Monitoring state |
| `inventory.location` | "DC1 Rack 4" | Physical location (already in enriched) |

### ME EC — Trapped Fields

| Raw Key | Value Example | What You Could Do |
|---|---|---|
| `branch_office_name` | "NY Office" | (already in enriched) |
| `agent_version` | "10.5.2345" | (already in enriched) |
| `installation_status` | "Installed successfully" | (already in enriched) |
| `computer_live_status` | "Alive" | (already in enriched) |
| `last_successful_scan` | timestamp | (already in enriched) |
| `domain_netbios_name` | "CORP" | (already in enriched) |
| `build_number` | "22631" | (already in enriched) |
| `service_pack` | "23H2" | (already in enriched) |
| `customer_id` | tenant ID | Multi-tenant |
| `computer_status_update_time` | timestamp | Agent last report |
| `agent_last_contact_time` | timestamp | Agent heartbeat |

---

## Hidden Layer 4: Non-Obvious Cross-Source Correlations

_Connections the code makes implicitly, or could make but doesn't._

### 1. User Identity Web

```
assigned_user (on any device)
    │
    ├── linked_by_user → other devices with same assigned_user (trace graph)
    │
    ├── sp_employees ← exact match on BusEmailAddress → employee record (attachment)
    │
    ├── sdp created_by.email_id ← COULD cross-ref sp_employees (NOT WIRED)
    │
    └── me_mdm user.user_email ← COULD cross-ref sp_employees (NOT WIRED)
```

**Currently wired:** `assigned_user` → `sp_employees` attachment (exact match on email). The trace graph shows `linked_by_user` edges for devices sharing `assigned_user`.

**Not wired:** SDP `created_by.email_id` and ME MDM `user.user_email` do NOT cross-reference against `sp_employees`. Two devices assigned to the same person via different sources won't link unless the `assigned_user` field is identical (normalized to same email format).

### 2. Device Lifecycle Chain

```
sp_devices: AC_ReplacedDevice → previous device lookup ID
sp_devices: AC_ReplacementDevice → next device lookup ID
sp_devices: PurchaseDate → when acquired
sp_devices: TotalofAllPurchasesforDevice → total spend
sp_devices: ComponentPurchases → individual part purchases
sdp_assets: purchase_cost + current_cost → depreciation
ad_computers: whenCreated → join date
me_mdm: is_removed → retired from MDM
```

**None of this is wired into any API response.** The SP lifecycle flags (`AC_ReplacedDevice`, `AC_ReplacementDevice`) are raw SharePoint lookup IDs trapped in `extra_attributes`. You'd need to resolve them yourself.

### 3. Network Presence Triangulation

```
Device X IP = 192.168.20.55
    │
    ├── fg_clients: last_seen (30min refresh) → was it online recently?
    ├── fg_dhcp_leases: dhcp_lease_status → does it have an active lease?
    ├── fg: detected_interface → which VLAN?
    ├── fg: fortiswitch_port_id → which physical switch port?
    ├── fg: fortiap_name + ssid → which WiFi AP? (location proxy!)
    ├── sp_vlans: NetworkID (CIDR match) → VLAN name
    └── zbx: icmppingloss + icmppingsec → is it responding to ping?
```

**Wired:** VLAN CIDR match (`device_vlans()`), Zabbix metrics, FortiGate clients.

**Not wired:** FortiAP association (WiFi location proxy), switch port tracing, DHCP lease correlation against ARP table. The FG ARP table and DHCP leases are CONTEXT feeds — stored but never linked to device clusters.

### 4. OT/ICS Security (FortiGate purdue_level)

FortiGate's `purdue_level` is the Purdue model for ICS/OT network segmentation:
- Level 0: Physical process
- Level 1: Basic control (PLC, RTU)
- Level 2: Supervisory control (SCADA)
- Level 3: Operations management
- Level 4: Business logistics
- Level 5: Enterprise

This field exists in `fg_clients` raw JSON but is **never surfaced** in any API response or enrichment view. For OT environments, this is the most important classification field.

### 5. Hardware Drift Detection

`GET /anomalies` → `hardware_drift_list[]` detects clusters where **two different serial numbers** appear in the same cluster. The query:

```sql
SELECT cluster_id, COUNT(DISTINCT serial_number) as serial_cnt
FROM source_records
WHERE serial_number != ''
GROUP BY cluster_id HAVING serial_cnt > 1
```

This surfaces Splink false-positives: same device resolved to one cluster but with conflicting serials. The `DeviceStory.drift_audit[]` field expands this to per-field disagreement across sources.

### 6. Zabbix Group Classification

Every Zabbix host has `groups` — e.g., `[{"groupid": "15", "name": "ML-Servers"}]`. This classifies hosts as Servers vs Workstations. It's trapped in raw JSON (`zbx_hosts` → `extra_attributes`). If surfaced as a `device_class` concept in the enrichment registry, every device that maps to a Zabbix host would get a Server/Workstation tag. Currently not wired.

### 7. Cost & Procurement Cross-Reference

```
sp_devices: TotalofAllPurchasesforDevice = $1,234
sp_ComponentPurchases: lines with device lookup ID → itemized costs
sdp_assets: purchase_cost = $1,200  (from different source!)
sdp_assets: current_cost  = $800    (depreciated)
sdp_assets: purchase_orders → PO references
```

SDP and SharePoint both track cost for the same physical device (if Splink resolved them to the same cluster). The enriched view COALESCEs both `purchase_cost` concepts. But you can **compare** them: if SP says $1,234 and SDP says $1,200, that's a procurement discrepancy.

### 8. MDM Device Type vs SDP Form Factor

ME MDM has `device_type` (1=iOS, 2=iPad, 3=Windows, 4=Mac). SDP has `form_factor` (freeform text). Both classify the device type but from different taxonomies. You could cross-validate: an MDM `device_type=3` (Windows) should match SDP `form_factor` containing "Laptop" or "Desktop". Currently neither field is mapped to a shared concept.

### 9. Agent Health Dashboard

ME EC traps agent health fields that ARE in the enriched view:
- `agent_version` — is it current?
- `agent_status` (`installation_status`) — did install succeed?
- `live_status` (`computer_live_status`) — is the agent reporting?
- `last_scan_time` — when was the last inventory scan?
- `domain` — which domain is it joined to?

These 5 fields form a de-facto agent health dashboard per device. They exist in `DeviceStory.enriched` but are not surfaced in any dedicated health endpoint.

---

## Hidden Layer 5: Coverage Gaps — What Sources Can't Contribute

| Field | sp | me_ec | me_mdm | fg | fg_dhcp | zbx | ad | sdp |
|---|---|---|---|---|---|---|---|---|
| **name** | 100% | 100% | 94% | 83% | 93% | 100% | 100% | 100% |
| **serial_number** | 90% | 91% | 94% | **0%** | **0%** | **0%** | **0%** | **0%** |
| **mac_address** | 50% | 100% | 94% | 100% | 100% | **0%** | **0%** | **0%** |
| **mac_clean** | (derived) | (derived) | (derived) | (derived) | (derived) | **0%** | **0%** | **0%** |
| **ip_address** | 0% | 100% | 0% | 97% | 100% | 97% | 0% | 0% |
| **assigned_user** | 50% | 46% | 94% | 16% | 0% | 0% | 100% | 100% |
| **manufacturer** | 95% | 0% (in raw JSON!) | 94% | 79% | 0% | 0% | 0% | 47% |
| **model** | 99% | 0% (in raw JSON!) | 94% | 52% | 0% | 0% | 0% | 100% |
| **os** | 14% | 100% | 94% | 74% | 0% | 44% | 100% | 0% |
| **os_version** | 0% | 100% | 94% | 45% | 0% | 0% | 100% | 0% |
| **imei** | 0% | 0% | 64% | 0% | 0% | 0% | 0% | 0% |

**Implications for Entity Resolution:**

- **FG, FG_DHCP, ZBX, AD, SDP all have 0% serial coverage** — Splink's `serial_number` blocking rule does nothing for 5 of 6 sources. Only SP and ME contribute serials.
- **ZBX, AD, SDP have 0% MAC coverage** — `mac_clean` blocking misses these sources entirely.
- **ZBX has only hostname + IP** — survives blocking only via `name_clean` + `name_fallback` (derived). Without those, ZBX would be 100% isolated.
- **ME EC `manufacturer` and `model` show 0%** despite being in raw JSON — the manifest spec paths may not match. Check `hardware_vendor` vs `manufacturer` key name.
- **SP has 0% IP** — SharePoint never captures IP. This device can't be found by IP lookup.

---

## Hidden Layer 6: Field History Volume

_`/device/{q}/history` returns last 100 entries. But the raw table has far more._

| Source | Field History Rows |
|---|---|
| **zbx** | 14,822 |
| **me** | 94 |
| **sp** | 48 |
| **ad** | 0 |
| **fg** | 0 |
| **sdp** | 0 |

ZBX dominates — every Zabbix item update writes a field history row. This is effectively a metric changelog. The endpoint caps at 100 rows per cluster. For forensic analysis (when did this server's CPU spike?), you'd need direct DuckDB access to the full 14k rows.

---

## Quick Recipe Map

```
"I need everything about this device"          → /device/{q} + /device/{q}/trace
"What's its full graph?"                       → /device/{q}/trace (watch linked_devices!)
"What cross-source fields are enriched?"       → .enriched{} on DeviceStory
"What raw data is trapped?"                    → .extra_attributes{} on SourceRecord
"What raw keys aren't mapped at all?"          → /diagnostics/unmapped-fields
"Which devices share a user with this one?"    → trace.linked_devices (linked_by_user edges)
"Which devices share attachments?"             → trace.linked_devices (linked_by_attachment edges)
"What VLAN/subnet is it on?"                   → trace.vlans[] (CIDR join from sp.sqlite!)
"Is it online right now?"                      → .enriched.is_online (FG) OR stats metrics
"Is the MDM agent healthy?"                    → .enriched.agent_status + .enriched.live_status
"Is it supervised/Managed?"                    → .enriched.is_supervised (MDM)
"What's the battery level?"                    → .enriched.battery_level (MDM)
"What did this cost?"                          → .enriched.purchase_cost (SP + SDP COALESCE)
"Where is it physically?"                      → .enriched.location (ZBX) OR extra_attributes.Location (SP)
"Which WiFi AP is it on?"                      → extra_attributes.fortiap_name (FG) — NOT surfaced
"Which switch port?"                           → extra_attributes.fortiswitch_port_id (FG) — NOT surfaced
"OT security zone?"                            → extra_attributes.purdue_level (FG) — NOT surfaced
"Has the serial number changed?"               → drift_audit[] on DeviceStory
"Has any field changed over time?"             → /device/{q}/history (last 100, zbx has 14k more)
"How does cost compare across sources?"        → Compare SP TotalofAllPurchases vs SDP purchase_cost
"When was it purchased? When was it retired?"  → STILL TRAPPED in extra_attributes
"Which OU/department in AD?"                   → extra_attributes.dn (AD) — parse CN=...,OU=...
"Is the AD account disabled?"                  → extra_attributes.userAccountControl (AD) — NOT surfaced
```
