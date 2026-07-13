# Zentinull — The Endgame

What does "done properly" look like? This document is the north star.

---

## Current State (July 2026)

**Working**: 6-source ingest → Splink entity resolution → DuckDB mesh → FastAPI API → Streamlit dashboard. 5,900 lines of source, 450+ tests, 92% coverage. Docker deployment. Scheduled worker. Architectural audit with 21 findings.

**Not working**: 1 P0 race condition in status tracking, dual pipeline orchestrators, dashboard bypasses API for pipeline triggers, dead Prometheus metrics, config drift across 8+ env vars, no contract validation tests.

**Missing**: Authentication/authorization, incremental sync, production hardening, most value-add features.

---

## The Endgame: Five Layers

### Layer 1: Rock-Solid Foundation

*The pipeline never loses data. The API never lies. Configuration is never guessed.*

| What | Why | Done When |
|---|---|---|
| Status module uses exclusive lock across read→write cycle | P0 data loss race condition | `record_start` + `record_done` from 2 threads → both survive |
| PID lock uses `O_EXCL\|O_CREAT` atomic acquisition | Concurrent pipeline detection is unreliable | Two `run_pipeline()` calls → one succeeds, one gets clean error |
| SQLite ingest writes to temp table, validates, then swaps | Crash between DROP and CREATE loses data | Kill process mid-ingest → data survives on restart |
| Single pipeline orchestrator (delete legacy `pipeline.py`) | Two implementations = maintenance tax | `python -m zentinull.pipeline` redirects to `cli.pipeline` |
| Contract validation tests enforce SPLINK_FIELDS ↔ DDL ↔ models | Column drift is silent and catastrophic | Rename a field in contracts.py → test suite breaks immediately |
| All `config.py` constants are consumed (no dead LOG_*, API_HOST, SPLINK_THRESHOLD) | Dead config = confusion | `grep -r "from.*config import" src/` covers every constant |
| Docker dashboard service gets auth env vars OR pipeline moves to API | Dashboard can't run ingest in Docker | `docker compose --profile all up` → dashboard can trigger pipeline |

**Estimated effort**: 4-6 hours. **Payoff**: The foundation stops leaking.

---

### Layer 2: Operational Maturity

*You can run this in production, monitor it, and sleep at night.*

| What | Why | Done When |
|---|---|---|
| Prometheus metrics actually count things | `/metrics` lying about 0 errors is worse than no metrics | `db_errors_total` increments on DuckDB failures; `pipeline_runs_total` increments on each run |
| Single logging path (no dual FileHandler + RotatingFileHandler) | Split-brain logs are unsearchable | One handler per logger, 12-factor stdout, file logging at process supervisor level |
| `get_logger()` caches setup (no redundant `setup()` calls) | Wasteful reconfiguration on every call | `get_logger("x")` called 1000x → `setup()` called once |
| `logging_config.py` imports from `config.py` (not raw `os.environ`) | 8 env vars read independently of config module | Delete `os.environ.get("ZENTINULL_LOG_*")` from formatters |
| Ingestor isolation: per-source try/except + optional subprocess boundary | One bad source shouldn't crash the pipeline | FortiGate auth fails → pipeline continues with 5 sources |
| Incremental sync in worker (delta detection, not full re-ingest) | Full re-ingest of 1500 records every 10 min is wasteful | Zabbix sync only fetches changed records since last run |
| Health check uses `db.ping()` not `db._path.exists()` | Leaky abstraction to filesystem | Health endpoint doesn't import `Path` |

**Estimated effort**: 8-12 hours. **Payoff**: You can deploy and observe.

---

### Layer 3: Security & Access Control

*The API serves data to authorized consumers. Nothing else.*

| What | Why | Done When |
|---|---|---|
| API key authentication (header-based, configurable) | API is wide open today | `curl localhost:8001/device/ws28` → 401 without key |
| Per-endpoint RBAC: read-only vs admin | Dashboard should read, pipeline should write | `/pipeline/run` requires admin key; `/device/ws28` requires any valid key |
| Rate limiting (per-IP or per-key) | Public-facing endpoints need abuse protection | 100 req/min per key; Grafana plugin doesn't DDoS the API |
| OAuth2/OIDC option for SSO integration | Enterprise environments need SSO | `ZENTINULL_OIDC_ISSUER` env var → JWT validation on all endpoints |
| Secrets in vault, not .env files | .env files leak in git, Docker layers, CI logs | `.env` for dev only; production uses Vault/AWS SM/K8s secrets |
| HTTPS termination documented (reverse proxy config) | API serves plain HTTP | Nginx/Caddy config example in docs |

**Estimated effort**: 12-16 hours. **Payoff**: Safe to expose beyond localhost.

---

### Layer 4: Value-Add Features (The Product)

*This is where Zentinull goes from "pipeline" to "IT asset intelligence platform."*

**Sprint 1 — Quick Wins (low effort, high impact):**

| Feature | Endpoint | What It Does |
|---|---|---|
| Unmanaged Device Hunter | `GET /unmanaged` | Devices in AD/SharePoint but missing from ManageEngine — security blind spots |
| Asset Risk Scorer | `GET /risks` | Score every device 0-100 based on coverage, age, ticket volume, serial presence |
| Outbound Webhook Notifier | Post-pipeline | Delta diff → POST changes to Slack/Teams/ServiceNow |

**Sprint 2 — Intelligence (medium effort, high impact):**

| Feature | Endpoint | What It Does |
|---|---|---|
| Cross-Source Drift Detector | `GET /drift` | Per-device field comparison across all 6 sources — flag mismatches |
| Fleet OS/Hardware Census | `GET /census` | Aggregate ManageEngine `extra_attributes` → OS distribution, RAM/CPU stats, EOL warnings |
| User Device Portfolio | `GET /users/{name}` | Flip the view: all devices assigned to a person |

**Sprint 3 — Advanced (high effort, high impact):**

| Feature | Endpoint | What It Does |
|---|---|---|
| Cluster Confidence Explorer | `GET /clusters/review` | Review low-confidence Splink matches, merge/split clusters, feed corrections back |
| Network Topology Explorer | `GET /topology` | Force-directed graph of FortiGate IP/MAC/VLAN data |
| NIST CSF Compliance Dashboard | `GET /compliance/nist` | Map device coverage to NIST Cybersecurity Framework pillars |
| Predictive Fleet Health | `GET /health/predict` | Zabbix time-series + SDP tickets → failure probability per device |

**Sprint 4 — Integrations:**

| Feature | What It Does |
|---|---|
| Grafana Data Source Plugin | Zentinull data overlays Zabbix dashboards |
| Slack/Teams Chatbot | Natural language device queries from chat |
| CMDB Reconciliation Reports | Weekly PDF/email for audit prep |
| QR Code Asset Labels | Printable labels linking to device digital passports |

**Estimated effort**: 40-60 hours total across sprints. **Payoff**: The system justifies its existence.

---

### Layer 5: Developer & Operator Experience

*Contributors can understand, modify, and deploy with confidence.*

| What | Why | Done When |
|---|---|---|
| Architecture Decision Records (ADRs) for key choices | Why Splink over dedupe? Why DuckDB over Postgres? | `docs/adr/` with 5-10 key decisions documented |
| CONTRIBUTING.md with dev setup, test running, PR process | New contributors shouldn't guess | Fork → `make dev-setup` → `make check` → PR works |
| API client SDKs (Python, TypeScript) from OpenAPI | Consumers shouldn't write raw HTTP | `pip install zentinull-client` / `npm install @moonlite/zentinull-client` |
| Staging environment (Docker Compose profile) | Test pipeline against real-ish data before prod | `docker compose --profile staging up` with synthetic data |
| Load testing script | Know the breaking point before production does | `make load-test` → req/sec, p99 latency, error rate |
| Documentation site (MkDocs or similar) | README can't hold everything | `docs.zentinull.dev` with quickstart, API reference, architecture |
| Schema migration tooling for DuckDB | Schema changes shouldn't require manual DDL | `make db-migrate` applies pending migrations |

**Estimated effort**: 20-30 hours. **Payoff**: The project is maintainable by humans other than the original author.

---

## Priority Order

```
Phase 1: Foundation (Layer 1)          — 4-6h   — STOP THE BLEEDING
Phase 2: Operations (Layer 2)          — 8-12h  — RUN IN PRODUCTION
Phase 3: Security (Layer 3)            — 12-16h — EXPOSE SAFELY
Phase 4: Quick Wins (Sprint 1)         — 8-10h  — DELIVER VALUE NOW
Phase 5: Intelligence (Sprint 2)       — 15-20h — BE INDISPENSABLE
Phase 6: DX (Layer 5)                  — 10-15h — BE MAINTAINABLE
Phase 7: Advanced (Sprint 3)           — 15-20h — BE SOPHISTICATED
Phase 8: Integrations (Sprint 4)       — 10-15h — BE EVERYWHERE
```

**Total**: ~80-115 hours of focused work to go from current state to production-ready IT asset intelligence platform.

---

## What "Done" Feels Like

An IT manager walks in Monday morning. They open the Zentinull dashboard. They see:

- **1,487 devices** resolved from 6 sources, **89% multi-source coverage**
- **3 unmanaged devices** flagged in red — no endpoint protection, need immediate attention
- **12 drift anomalies** — AD says one thing, FortiGate says another
- **Risk scores** for every device — top 5 are aging hardware with rising ticket volume
- **OS census** — 34% still on Windows 10 (EOL in 3 months)
- **Webhook fired** at 6 AM — 2 new devices appeared overnight, 1 disappeared

They click a device. The digital passport shows its entire history: first seen in AD 3 years ago, MDM enrolled 2 years ago, last ticket 2 weeks ago, Zabbix metrics normal. They scan the QR code on the physical device with their phone — same passport loads.

A Grafana dashboard overlays Zentinull device data on top of Zabbix monitoring panels. The Slack bot answers "who owns WS-28?" in 2 seconds.

The pipeline runs every 10 minutes for high-frequency sources, daily for Splink. It has never lost data. The Prometheus metrics have never lied. The logs have never been split-brained.

That's the endgame.
