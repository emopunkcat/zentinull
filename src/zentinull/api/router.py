# mypy: ignore-errors

"""FastAPI router — async endpoints backed by DuckDB MeshDB."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..logging_config import get_logger
from .db import MeshDB

log = get_logger("api.router")
router = APIRouter()


def _db(request: Request) -> MeshDB:
    db = request.app.state.db
    if db is None:
        raise HTTPException(503, "Mesh database not loaded — run pipeline.py first")
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# Device Lookup
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/device/{query}")
async def device(query: str, request: Request) -> dict:
    """
    Device lookup by any identifier — name, serial, MAC, IP, user.
    Falls back to full-text substring search.
    """
    log.info({"event": "request", "endpoint": "/device/{query}", "query": query})
    db = _db(request)
    result = db.lookup(query)
    if result is None:
        raise HTTPException(404, f"'{query}' not found")
    return result.model_dump()


@router.post("/batch")
async def batch(request: Request) -> list[dict | None]:
    """
    Resolve multiple device queries in a single connection.
    POST /batch  body: ["ws28", "dc01", "MZ015CF2", "192.168.20.35"]
    Returns array of device stories (null where not found).
    """
    import json as _json

    body = await request.body()
    queries: list[str] = _json.loads(body)
    if not isinstance(queries, list):
        raise HTTPException(400, "Body must be a JSON array of query strings")
    log.info({"event": "request", "endpoint": "/batch", "count": len(queries)})
    db = _db(request)
    return db.batch_lookup(queries)


# ═══════════════════════════════════════════════════════════════════════════════
# Search
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    field: str = "",
    limit: int = Query(20, le=100),
) -> list[dict]:
    """
    Search by any field or full-text across all columns.

    Fields: device_name, serial_number, mac_address, ip_address,
            assigned_user, manufacturer, model, os, imei
    """
    log.info({"event": "request", "endpoint": "/search", "q": q, "field": field})
    db = _db(request)
    results = db.search(q, field=field, limit=limit)
    return [r.model_dump() for r in results]


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard & Stats
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/dashboard")
async def dashboard(request: Request) -> dict:
    """KPI dashboard — counts, coverage, top clusters."""
    log.info({"event": "request", "endpoint": "/dashboard"})
    return _db(request).dashboard()


@router.get("/mesh")
async def mesh(request: Request) -> dict:
    """Cross-source cluster statistics."""
    log.info({"event": "request", "endpoint": "/mesh"})
    return _db(request).mesh_stats()


@router.get("/clusters")
async def list_clusters(
    request: Request,
    min_sources: int = 1,
    source: str = "",
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> dict:
    """Paginated cluster list, filterable by source count or system."""
    log.info({"event": "request", "endpoint": "/clusters", "min_sources": min_sources, "source": source})
    db = _db(request)
    total, items = db.list_clusters(min_sources, source, limit, offset)
    return {"total": total, "offset": offset, "items": [i.model_dump() for i in items]}


@router.get("/anomalies")
async def anomalies(request: Request) -> dict:
    """Singletons, unnamed devices, missing serials."""
    log.info({"event": "request", "endpoint": "/anomalies"})
    return _db(request).anomalies()


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics & Events (time-series)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/device/{query}/metrics")
async def device_metrics(
    query: str,
    request: Request,
    metric: str = "",
    source: str = "",
    hours: int = 24,
    limit: int = Query(500, le=5000),
) -> dict:
    """
    Time-series metrics for a device.
    /device/ws28/metrics                          → all metrics last 24h
    /device/ws28/metrics?metric=cpu_pct&hours=168 → CPU for last week
    /device/ws28/metrics?source=zabbix             → Zabbix-only metrics
    """
    log.info({"event": "request", "endpoint": "/device/{query}/metrics", "query": query, "hours": hours})
    db = _db(request)
    cid = _resolve_cluster(db, query)
    metrics = db.device_metrics(cid, metric=metric, source=source, hours=hours, limit=limit)
    names = db.device_metric_names(cid)
    return {
        "query": query,
        "cluster_id": cid,
        "metric_names": names,
        "count": len(metrics),
        "metrics": metrics,
    }


@router.get("/device/{query}/timeline")
async def device_timeline(
    query: str,
    request: Request,
    hours: int = 168,
    limit: int = Query(100, le=1000),
) -> dict:
    """
    Recent events for a device.
    /device/ws28/timeline          → last 7 days
    /device/ws28/timeline?hours=1  → last hour
    """
    log.info({"event": "request", "endpoint": "/device/{query}/timeline", "query": query, "hours": hours})
    db = _db(request)
    cid = _resolve_cluster(db, query)
    events = db.device_timeline(cid, hours=hours, limit=limit)
    return {
        "query": query,
        "cluster_id": cid,
        "hours": hours,
        "count": len(events),
        "events": events,
    }


@router.get("/device/{query}/stats")
async def device_stats(query: str, request: Request) -> dict:
    """
    Current state: latest metric values + event severity counts.
    /device/ws28/stats
    """
    log.info({"event": "request", "endpoint": "/device/{query}/stats", "query": query, "hours": 24})
    db = _db(request)
    cid = _resolve_cluster(db, query)
    stats = db.device_stats(cid)
    summary = db.device_metric_summary(cid, hours=24)
    return {
        "query": query,
        "cluster_id": cid,
        "stats": stats,
        "metric_summary_24h": summary,
    }


@router.get("/device/{query}/metric-summary")
async def device_metric_summary(
    query: str,
    request: Request,
    hours: int = 24,
) -> dict:
    """
    Aggregated metrics: avg/max/min/latest per metric.
    /device/ws28/metric-summary?hours=168
    """
    log.info({"event": "request", "endpoint": "/device/{query}/metric-summary", "query": query, "hours": hours})
    db = _db(request)
    cid = _resolve_cluster(db, query)
    return {
        "query": query,
        "cluster_id": cid,
        "hours": hours,
        "metrics": db.device_metric_summary(cid, hours=hours),
    }


def _resolve_cluster(db, query: str) -> str:
    """Resolve a query string to a cluster_id, or 404."""
    result = db.lookup(query)
    if result is None:
        raise HTTPException(404, f"'{query}' not found")
    return result.cluster_id


# ═══════════════════════════════════════════════════════════════════════════════
# HTML Device View
# ═══════════════════════════════════════════════════════════════════════════════

_DEVICE_VIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Zentinull — Device View</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.header h1{font-size:24px;color:#58a6ff}
.badges{display:flex;gap:8px}
.badge{padding:4px 12px;border-radius:12px;font-size:12px;font-weight:600}
.badge-good{background:#1a3a1a;color:#3fb950}
.badge-warn{background:#3a2a1a;color:#d2991d}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:16px}
.card h2{font-size:14px;color:#8b949e;margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px}
.kv{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #21262d;font-size:13px}
.kv:last-child{border-bottom:none}
.kv .key{color:#8b949e}
.kv .val{color:#c9d1d9;max-width:60%;text-align:right;word-break:break-all}
.val-multi{color:#d2991d!important}
.source-tag{padding:2px 8px;border-radius:4px;font-weight:600;font-size:11px}
.src-sp{background:#1a3a5c;color:#58a6ff}
.src-me_ec{background:#1a3a1a;color:#3fb950}
.src-me_mdm{background:#1a3a2a;color:#56d364}
.src-fg{background:#3a1a1a;color:#f85149}
.src-zbx{background:#3a2a1a;color:#d2991d}
.src-ad{background:#2a1a3a;color:#bc8cff}
.src-sdp{background:#3a3a1a;color:#e3b341}
.consolidated{grid-column:1/-1}
.json-block{background:#0d1117;border:1px solid #21262d;border-radius:4px;padding:12px;font-family:'SF Mono','Fira Code',monospace;font-size:11px;white-space:pre-wrap;max-height:300px;overflow-y:auto}
</style>
</head>
<body>
<div class="header"><h1 id="dn">Loading...</h1><div class="badges" id="badges"></div></div>
<div class="grid" id="grid"></div>
<script>
const API=window.location.origin;
const q=new URLSearchParams(window.location.search).get('q')||'ws28';
(async()=>{
const r=await fetch(API+'/device/'+encodeURIComponent(q));
if(!r.ok){document.body.innerHTML='<h1 style=color:#f85149>404 — '+q+' not found</h1>';return}
const d=await r.json();
document.title=d.device_name.toUpperCase()+' — Zentinull';
document.getElementById('dn').textContent=d.device_name.toUpperCase();
document.getElementById('badges').innerHTML='<span class="badge badge-good">'+d.sources.length+' sources</span>'+d.sources.map(s=>'<span class="badge badge-warn">'+s+'</span>').join('');
let h='<div class="card consolidated"><h2>Consolidated View</h2>';
for(const[k,vals]of Object.entries(d.consolidated)){if(!vals.length)continue;const multi=vals.length>1?' val-multi':'';h+='<div class="kv"><span class="key">'+k+'</span><span class="val'+multi+'">'+vals[0]+(vals.length>1?' (+'+(vals.length-1)+' more)':'')+'</span></div>'}
h+='</div>';let grid=document.getElementById('grid');grid.innerHTML=h;
for(const rec of d.records){
let c='<div class="card"><h2><span class="source-tag src-'+rec.source+'">'+rec.source+'</span> '+(rec.name||rec.source_id||'(unnamed)')+'</h2>';
for(const f of['source_id','name','serial_number','mac_address','mac_clean','manufacturer','model','os','assigned_user','ip_address','imei']){if(rec[f])c+='<div class="kv"><span class="key">'+f+'</span><span class="val">'+rec[f]+'</span></div>'}
c+='</div>';grid.innerHTML+=c}
})();
</script>
</body>
</html>"""


@router.get("/device-view", response_class=HTMLResponse)
async def device_view(_q: str = "ws28") -> HTMLResponse:
    """HTML device dashboard. /device-view?q=ws28"""
    return HTMLResponse(_DEVICE_VIEW_HTML)
