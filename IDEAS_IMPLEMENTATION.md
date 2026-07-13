# Zentinull — Creative Implementation Ideas

Ideas for features, tools, and integrations built on the Zentinull API data surface.
Each entry includes the data it consumes, implementation approach, and effort/impact rating.

---

## Data Surface Recap

The API exposes a **device identity mesh** resolved from 6 independent IT sources:

| Source | What it provides |
|---|---|
| SharePoint / n8n | Manual inventory, procurement data |
| ManageEngine EC | Endpoint management, OS, software, hardware specs |
| ManageEngine MDM | Mobile devices, IMEI |
| FortiGate | Network data: IP, MAC, subnet, VLAN |
| Zabbix | Monitoring: time-series metrics (CPU, disk, memory) |
| Active Directory | LDAP: hostname, user assignments, OU |
| ServiceDesk Plus | Help desk: tickets, events, lifecycle |

**Key endpoints**: `/device/{query}`, `/device/{query}/timeline`, `/device/{query}/metrics`, `/device/{query}/stats`, `/search`, `/clusters`, `/anomalies`, `/dashboard`, `/mesh`, `/batch`

---

## 1. Operational Intelligence

### 1.1 Cross-Source Drift Detector
**Effort: medium | Impact: high**

Compare every field per-device across all 6 sources. Flag discrepancies:
- Name mismatches (AD says `WS28`, FortiGate says `WS-28`)
- Serial number conflicts
- Device in one source but missing from another
- Stale records (seen in AD but absent from FortiGate for 90+ days)

**Implementation sketch:**
```
GET /drift → per-cluster field comparison matrix
GET /drift/{cluster_id} → detailed field-by-field diff with source attribution
```
- Query `source_records` for all records per cluster, group by `source`
- For each field (name, serial, mac, manufacturer, model, os, assigned_user), compute consensus vs outliers
- Return a drift score and per-field "votes" (which sources agree/disagree)
- Dashboard: sortable drift leaderboard

**Data used**: `source_records` per cluster, `devices` consolidated view

---

### 1.2 Unmanaged Device Hunter
**Effort: low | Impact: critical**

Devices present in AD or SharePoint inventory but MISSING from ManageEngine Endpoint Central are security blind spots — no patching, no AV enforcement, no policy.

**Implementation sketch:**
```
GET /unmanaged → list of clusters where source coverage lacks "manageengine_ec"
```
- Single SQL: `devices` with `sources` containing `"ad"` or `"sharepoint"` but NOT `"manageengine_ec"`
- New Streamlit panel: red-card list with device name, assigned user, last seen
- Optional: webhook alert when new unmanaged devices appear after pipeline run

**Data used**: `mesh_stats` (source coverage per cluster), `source_records`

---

### 1.3 Asset Risk Scorer
**Effort: low | Impact: high**

Score every device 0-100:
- +20: not in ManageEngine (unmanaged)
- +20: in AD but no assigned user (orphaned)
- +15: high ticket volume from SDP (problematic)
- +15: not in Zabbix (unmonitored)
- +15: first seen > 3 years ago (aging hardware)
- +15: serial number missing across all sources (unidentifiable)

**Implementation sketch:**
```
GET /risks → list of all devices with risk scores, sortable
GET /risks/{cluster_id} → risk breakdown per factor
```
- Pure computation on existing data, no new storage
- Dashboard: risk heatmap, top-10 risky devices
- Color-code: green (0-25), yellow (26-50), orange (51-75), red (76-100)

**Data used**: `devices`, `source_records` coverage, `metrics`, `events`

---

### 1.4 Predictive Fleet Health
**Effort: high | Impact: high**

Zabbix time-series metrics (SMART disk health, CPU temp, memory errors) + SDP ticket patterns → predict failure probability per device.

**Implementation sketch:**
- Load device metrics from `/device/{query}/metrics` for all devices
- Extract: disk SMART reallocated sectors, temperature trends, memory ECC errors
- Combine with ticket frequency from `/device/{query}/timeline`
- Simple regression or threshold-based model: "3+ tickets in 30 days + rising disk errors = 78% failure probability"
- Dashboard: "Devices likely to fail this quarter" with replacement cost estimate
- Model-level lemon detection: aggregate by manufacturer/model → flag systemic issues

**Data used**: `device metrics` (time-series), `device timeline` (events), `device model/manufacturer`

---

## 2. Visualization & Exploration

### 2.1 Network Topology Explorer
**Effort: high | Impact: high**

FortiGate source records contain IP, MAC, subnet, and VLAN data. Build an interactive force-directed graph showing the actual network fabric.

**Implementation sketch:**
```
GET /topology → nodes + edges JSON for graph rendering
```
- Parse FortiGate `extra_attributes` for VLAN, subnet, gateway, interface data
- Nodes: devices (colored by source count), subnets (colored by VLAN)
- Edges: "connected to" (same subnet), "managed by" (FortiGate → device)
- Click node → sidebar with device story from `/device/{cluster_id}`
- Frontend: D3.js force simulation or vis.js network
- Embed as a Streamlit component or standalone React panel

**Data used**: `source_records` (FortiGate IP/MAC/extra_attributes), `cluster` data

---

### 2.2 Fleet OS/Hardware Census
**Effort: medium | Impact: high**

ManageEngine EC `extra_attributes` contains OS version, RAM, disk size, CPU model. Aggregate fleet-wide.

**Implementation sketch:**
```
GET /census → OS distribution, hardware stats, EOL warnings
GET /census/os → {"Windows 10": 342, "Windows 11": 89, "Ubuntu 22.04": 12, ...}
GET /census/hardware → RAM/disk/CPU distribution histograms
```
- Parse `extra_attributes` JSON from ManageEngine source records
- Extract: `os_name`, `os_version`, `ram_gb`, `disk_gb`, `cpu_model`, `installed_software`
- Dashboard: pie chart (OS), bar chart (RAM buckets), table (EOL OS versions)
- "End of Life" detector: flag Windows 10 (EOL Oct 2025), Ubuntu 18.04, etc.
- Software license compliance: count Office/Adobe installs vs purchased licenses

**Data used**: `source_records.extra_attributes` (ManageEngine), `device model/manufacturer`

---

### 2.3 Device Digital Passport
**Effort: medium | Impact: medium**

"Carfax for IT assets." Per-device visual timeline showing its entire known history.

**Implementation sketch:**
- Enhance the existing `/device-view` HTML endpoint (currently a static template)
- Render: horizontal timeline with colored markers
  - First seen in each source system
  - User assignment changes
  - Ticket events from SDP
  - Metric anomalies (spikes/drops)
  - Hardware changes (RAM upgrade, disk replacement)
- Embeddable iframe or standalone page
- QR code integration: scan → passport

**Data used**: `/device/{query}/timeline`, `/device/{query}/metrics`, `/device/{query}/stats`

---

### 2.4 Cluster Confidence Explorer
**Effort: high | Impact: high**

Splink's probabilistic matching isn't perfect. Build a UI for IT staff to review and correct clusters.

**Implementation sketch:**
```
GET /clusters/review?confidence=low → clusters below threshold
POST /clusters/{id}/merge → merge two clusters
POST /clusters/{id}/split → split a cluster into two
GET /clusters/{id}/compare → side-by-side field comparison of all source records
```
- Show low-confidence clusters (Splink provides match probability scores)
- Side-by-side diff of all source records within a cluster
- "Should these be merged?" → yes/no → record as training label
- "Should this be split?" → select records to extract → new cluster
- Feed corrections back as labeled pairs for `build_training_set.py`
- Gamify: review streak counter, "You fixed 12 clusters this week"

**Data used**: `clusters`, `source_records` per cluster, Splink confidence scores (from CSV export)

---

## 3. Integration & Automation

### 3.1 Outbound Webhook Notifier
**Effort: low | Impact: high**

After each pipeline run, compute a delta from the previous mesh snapshot. POST changes to configurable webhooks.

**Implementation sketch:**
- Hook into `cli/pipeline.py` `run_load()` completion
- Store a snapshot of `devices` + `source_records` after each run
- On next run, diff: new devices, disappeared devices, field changes per device
- POST JSON payload to configured webhook URLs (Slack, Teams, ServiceNow, custom)
- Format adapters: Slack Block Kit, Teams Adaptive Card, plain JSON
- Config via env vars: `ZENTINULL_WEBHOOK_URL`, `ZENTINULL_WEBHOOK_FORMAT`

**Payload example:**
```json
{
  "run_id": "2026-07-12T09:00:00",
  "new_devices": 3,
  "disappeared_devices": 1,
  "changed_devices": 12,
  "changes": [
    {"cluster_id": "ws-28", "field": "assigned_user", "old": "alice", "new": "bob"}
  ]
}
```

**Data used**: mesh snapshots, all device fields

---

### 3.2 Automated CMDB Reconciliation Reports
**Effort: medium | Impact: high**

Scheduled weekly PDF/email report covering everything an IT manager needs for audit prep.

**Implementation sketch:**
- New `serve.py report` subcommand or cron-triggered script
- Generate PDF via `reportlab` or `weasyprint` with sections:
  1. Executive summary: device count, coverage %, anomalies
  2. New devices this week
  3. Disappeared devices (potential decommissions)
  4. Field changes (reassignments, renames)
  5. Source coverage gaps
  6. Top-10 riskiest devices
  7. OS/hardware census summary
- Email via SMTP (env-configured) or save to disk
- Schedule: `0 8 * * MON` (Monday morning)

**Data used**: `mesh_stats`, `anomalies`, `dashboard`, `freshness`, risk scores

---

### 3.3 Slack/Teams Chatbot
**Effort: medium | Impact: medium**

Natural language device queries from chat.

**Implementation sketch:**
- Standalone bot (Python, `slack-bolt` or `teams-ai-library`)
- Queries the Zentinull API internally
- Intents:
  - "who owns WS28?" → `/device/ws28` → format as rich card
  - "what's on 192.168.20.0/24?" → parse subnet, search by IP prefix → list devices
  - "any anomalies?" → `/anomalies` → summary
  - "risk report" → `/risks` → top-N
  - "find device with serial X" → `/search?q=X`
- Response: Slack Block Kit / Teams Adaptive Card with key fields + link to full device view
- Deploy as a separate container in docker-compose

**Data used**: all API endpoints

---

### 3.4 QR Code Asset Label Generator
**Effort: low | Impact: medium**

Generate printable label sheets with QR codes linking to each device's digital passport.

**Implementation sketch:**
```
GET /labels → PDF of label sheet for all devices
GET /labels?cluster_id=ws28 → single label PDF
```
- `qrcode` library to generate QR PNG per device
- `reportlab` to compose label sheet PDF (Avery template compatible)
- Each QR encodes: `https://zentinull/device-view?q={cluster_id}`
- Include: device name, serial number, assigned user as human-readable text
- Print on sticker paper, affix to physical hardware → scan with phone → instant device story

**Data used**: `cluster_id`, `device_name`, `serial_number`, `assigned_user`

---

## 4. User-Centric Views

### 4.1 User Device Portfolio
**Effort: low | Impact: medium**

Flip the view: "Show me everything assigned to Jane."

**Implementation sketch:**
```
GET /users → list of all unique assigned users
GET /users/{username} → all devices assigned to that user across all sources
GET /users/{username}/history → device assignment timeline
```
- Query `source_records` and `devices` on `assigned_user`
- Flag conflicts: same device claimed by two users in different sources
- Show device handoff history: device moved from Alice → Bob → Charlie
- Leaderboard: top users by device count (useful for offboarding audits)
- Dashboard panel: user search → device portfolio card

**Data used**: `source_records.assigned_user`, `devices`, `user search`

---

### 4.2 Department-Level IT Spend Attribution
**Effort: medium | Impact: medium**

If `extra_attributes` carry department or cost center tags, aggregate IT footprint per department.

**Implementation sketch:**
```
GET /departments → device count, ticket count, license count per department
```
- Map `assigned_user` to department (via AD `extra_attributes` or lookup table)
- Aggregate: device count, ticket volume, software licenses per department
- TCO dashboard: cost per device × device count per department
- Enables IT chargeback/showback to business units

**Data used**: `source_records.extra_attributes`, `assigned_user`, `events`

---

## 5. Compliance & Governance

### 5.1 NIST CSF Alignment Dashboard
**Effort: medium | Impact: high**

Map device data to NIST Cybersecurity Framework pillars for audit-ready compliance posture.

**Implementation sketch:**
```
GET /compliance/nist → one-page CSF alignment with coverage percentages
```
- **IDENTIFY** (Asset Management): inventory completeness = devices with serial number / total
- **PROTECT** (Access Control + Protective Technology): devices in ManageEngine / total
- **DETECT** (Anomalies & Events): devices in Zabbix / total
- **RESPOND** (Response Planning): tickets with SLA met / total tickets (from SDP)
- **RECOVER** (Recovery Planning): devices with backup status / total (if available)
- Single-page dashboard with gauges per pillar
- Trend over time by snapshotting compliance scores after each pipeline run
- Export as PDF for auditor evidence

**Data used**: `mesh_stats`, source coverage, `metrics`, `events`

---

### 5.2 Decommissioning Advisor
**Effort: low | Impact: medium**

Flag devices that are candidates for decommissioning or archival.

**Implementation sketch:**
```
GET /stale → devices unseen for 90+ days across all sources
```
- Query `freshness` data per source: when was each device last seen?
- If ALL sources show last-seen > 90 days → strong decommission candidate
- Categories: "Likely retired" (no sources see it), "Possibly offline" (some sources see it), "Ghost" (only in one source)
- Suggest actions: archive from mesh, remove from AD, reclaim license
- GDPR/data retention: flag devices with user data that should be purged

**Data used**: `freshness`, `device metrics` (last seen), `events timeline`

---

## 6. Developer & API Experience

### 6.1 Grafana Data Source Plugin
**Effort: high | Impact: high**

IT teams already use Grafana for Zabbix dashboards. Make Zentinull a first-class Grafana data source.

**Implementation sketch:**
- Build a Grafana data source plugin (TypeScript + Go backend, or pure TypeScript for simple HTTP)
- Three query types:
  - **Device lookup**: search by name/serial/MAC → table of fields
  - **Mesh stats**: device count, source coverage → stat panels, pie charts
  - **Metrics over time**: per-device metric history → time-series graphs
- Plugin connects to `{ZENTINULL_URL}/api` endpoints
- Publish to Grafana plugin catalog
- Now Zentinull device data overlays Zabbix monitoring dashboards

**Data used**: `metrics`, `search`, `dashboard`, `mesh stats`

---

### 6.2 OpenAPI SDK Generation
**Effort: low | Impact: medium**

FastAPI auto-generates an OpenAPI schema at `/openapi.json`. Generate typed SDK clients.

**Implementation sketch:**
- FastAPI already serves OpenAPI at `/docs` and `/openapi.json`
- Use `openapi-generator` or `datamodel-code-generator` to produce:
  - Python client (`pip install zentinull-client`)
  - TypeScript client (`npm install @moonlite/zentinull-client`)
  - Go client
- Publish to PyPI/npm
- External scripts, CI pipelines, and tools query the mesh natively without raw HTTP

**Data used**: all endpoints (defined by OpenAPI schema)

---

## Implementation Priority Matrix

```
                    HIGH IMPACT
                        │
    1.4 Predictive     │  1.1 Drift Detector
    2.1 Topology       │  1.2 Unmanaged Hunter  ← START HERE
    2.4 Cluster UX     │  1.3 Risk Scorer
    4.2 Spend Attr     │  2.2 OS Census
    6.1 Grafana        │  3.1 Webhook Notifier
                        │  3.2 CMDB Reports
                        │  5.1 NIST Dashboard
    ────────────────────┼────────────────────
    5.2 Decommission   │  3.3 Slack Bot
    4.1 User Portfolio │  2.3 Digital Passport
                        │  3.4 QR Labels
                        │
                        │  6.2 OpenAPI SDK
                    LOW IMPACT

    LOW EFFORT ─────────────────── HIGH EFFORT
```

**Recommended first sprint** (low effort, high impact):
1. Unmanaged Device Hunter (1.2)
2. Asset Risk Scorer (1.3)
3. Outbound Webhook Notifier (3.1)

**Second sprint** (medium effort, high impact):
4. Cross-Source Drift Detector (1.1)
5. Fleet OS/Hardware Census (2.2)
6. CMDB Reconciliation Reports (3.2)

---

## Technical Notes

- All ideas that produce new API endpoints should follow the existing patterns: frozen Pydantic models in `models.py`, SQL query methods in `db.py`, route handlers in `router.py`, and tests mirroring the `tests/api/` conventions
- Streamlit dashboard additions go in `dashboard.py` using `st.cache_data`/`st.cache_resource`
- New CLI commands go in `serve.py` as `cmd_<name>()` functions
- Auth: all new endpoints inherit CORS from `server.py` (wide open); no per-endpoint auth currently exists
- Rate limiting: none currently — consider adding for public-facing endpoints (Grafana plugin, chatbot)
