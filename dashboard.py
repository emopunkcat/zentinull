"""Zentinull Dashboard — Streamlit app for pipeline monitoring and device mesh exploration.

Usage:
    streamlit run dashboard.py
    streamlit run dashboard.py --server.port 8501
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import streamlit as st

_HERE = Path(__file__).resolve().parent
_API_BASE = os.environ.get("DASHBOARD_API_URL", "http://localhost:8001")

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Zentinull Dashboard",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Data loaders ───────────────────────────────────────────────────────────────


@st.cache_data(ttl=30)
def _load_status() -> dict[str, Any]:
    """Load pipeline status from data/status.json."""
    from zentinull.cli.status import get_status

    return get_status()


@st.cache_resource(ttl=30)
def _load_mesh_data() -> dict[str, Any] | None:
    """Load mesh stats via API."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{_API_BASE}/dashboard")
            resp.raise_for_status()
            return resp.json()
    except httpx.RequestError:
        logging.exception("Dashboard API request failed")
        return None
    except httpx.HTTPStatusError:
        logging.exception("Dashboard API returned error status")
        return None


@st.cache_data(ttl=10, show_spinner=False)
def _search_devices(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Search devices via API."""
    if not query:
        return []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{_API_BASE}/search",
                params={"q": query, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.RequestError:
        logging.exception("Search API request failed")
        return []
    except httpx.HTTPStatusError:
        logging.exception("Search API returned error status")
        return []


# ── Sidebar: Pipeline controls ─────────────────────────────────────────────────


def _run_serve(args: list[str]) -> SimpleNamespace:
    """Trigger the pipeline via the API; individual stages fall back to the serve CLI."""
    if args == ["pipeline"]:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{_API_BASE}/pipeline/run")
                resp.raise_for_status()
            return SimpleNamespace(returncode=0, stderr="", stdout=resp.text, args=args)
        except httpx.RequestError as e:
            logging.exception("Pipeline API request failed")
            return SimpleNamespace(returncode=1, stderr=str(e), stdout="", args=args)
        except httpx.HTTPStatusError as e:
            logging.exception("Pipeline API returned error status")
            return SimpleNamespace(returncode=1, stderr=str(e), stdout="", args=args)

    result = subprocess.run(
        [sys.executable, str(_HERE / "serve.py"), *args],
        cwd=str(_HERE),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return SimpleNamespace(
        returncode=result.returncode,
        stderr=result.stderr,
        stdout=result.stdout,
        args=args,
    )


with st.sidebar:
    st.subheader(":material/rocket_launch: Pipeline controls")

    if st.button(":material/refresh: Run full pipeline", use_container_width=True):
        with st.spinner("Triggering pipeline..."):
            result = _run_serve(["pipeline"])
            if result.returncode == 0:
                st.success("Pipeline triggered — running in the background")
                _load_status.clear()
                _load_mesh_data.clear()
            else:
                st.error(f"Pipeline trigger failed: {result.stderr[-500:]}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(":material/download: Ingest", use_container_width=True):
            with st.spinner("Running ingest..."):
                result = _run_serve(["ingest"])
                if result.returncode == 0:
                    st.success("Ingest done")
                    _load_status.clear()
                else:
                    st.error("Ingest failed")

        if st.button(":material/table: Export", use_container_width=True):
            with st.spinner("Running export..."):
                result = _run_serve(["export"])
                if result.returncode == 0:
                    st.success("Export done")
                    _load_status.clear()
                else:
                    st.error("Export failed")

    with col2:
        if st.button(":material/hub: Splink", use_container_width=True):
            with st.spinner("Running Splink..."):
                result = _run_serve(["splink"])
                if result.returncode == 0:
                    st.success("Splink done")
                    _load_status.clear()
                else:
                    st.error("Splink failed")

        if st.button(":material/database: Load", use_container_width=True):
            with st.spinner("Loading to DuckDB..."):
                result = _run_serve(["load"])
                if result.returncode == 0:
                    st.success("Load done")
                    _load_mesh_data.clear()
                    _load_status.clear()
                else:
                    st.error("Load failed")

    st.divider()
    st.caption("Auto-refresh every 30s")

# ── Main content ──────────────────────────────────────────────────────────────

st.title(":material/monitoring: Zentinull Dashboard")

# Load data
status = _load_status()
mesh = _load_mesh_data()

stages = status.get("stages", {})
freshness = status.get("freshness", {})


# ── Pipeline status KPI row ────────────────────────────────────────────────────


def _status_icon(s: str) -> str:
    icons = {
        "ok": ":material/check_circle:",
        "fail": ":material/error:",
        "running": ":material/hourglass_top:",
        "": ":material/radio_button_unchecked:",
    }
    return icons.get(s, ":material/help:")


def _fmt_dur(ms: int) -> str:
    if ms <= 0:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


st.subheader("Pipeline status")

pipeline_cols = st.columns(5)
stage_order = ["ingest", "export", "splink", "load"]
stage_labels = {"ingest": "Ingest", "export": "Export", "splink": "Splink", "load": "Mesh load"}

for i, sname in enumerate(stage_order):
    sd = stages.get(sname, {})
    status_val = sd.get("status", "")
    delta_str = _fmt_dur(sd.get("duration_ms", 0))
    with pipeline_cols[i]:
        st.metric(
            f"{_status_icon(status_val)} {stage_labels[sname]}",
            status_val.upper() if status_val else "—",
            delta=delta_str if status_val == "ok" else None,
            border=True,
        )

# ── Data freshness ─────────────────────────────────────────────────────────────

st.subheader("Data freshness")

if freshness:
    fcols = st.columns(min(len(freshness), 6))
    src_labels = {
        "sp": "SharePoint",
        "me": "ManageEngine",
        "fg": "FortiGate",
        "zbx": "Zabbix",
        "ad": "AD",
        "sdp": "SDP",
    }
    for i, (skey, sf) in enumerate(sorted(freshness.items())):
        with fcols[i]:
            label = src_labels.get(skey, skey)
            row_count = sf.get("row_count", 0)
            newest_raw = sf.get("newest_record", "")
            try:
                newest = datetime.fromisoformat(newest_raw).strftime("%m/%d %H:%M") if newest_raw else "—"
            except (ValueError, TypeError):
                newest = "—"
            st.metric(label, f"{row_count} records", delta=newest, border=True)
else:
    st.info("No freshness data yet. Run `python serve.py ingest` to populate.")

# ── Mesh stats ─────────────────────────────────────────────────────────────────

st.subheader("Device mesh")

if mesh:
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.metric("Total clusters", mesh["clusters"], border=True)
    with mc2:
        st.metric("Total records", mesh["records"], border=True)
    with mc3:
        st.metric("Multi-source", mesh["multi_source"], border=True)
    with mc4:
        st.metric("Singletons", mesh["singletons"], border=True)

    # Source distribution chart + coverage table
    chart_col, cov_col = st.columns([2, 1])

    with chart_col:
        st.caption("Records per source")
        src_data = mesh["sources"]
        st.bar_chart(
            {"source": list(src_data.keys()), "records": list(src_data.values())},
            x="source",
            y="records",
            horizontal=False,
        )

    with cov_col:
        st.caption("Field coverage")
        cov_rows = [{"field": f, "coverage": v} for f, v in mesh["coverage"].items()]
        st.dataframe(cov_rows, use_container_width=True, hide_index=True)

    # Source count distribution
    with st.expander("Source count distribution"):
        scd = mesh["source_count_dist"]
        scd_data = {"sources": list(scd.keys()), "clusters": list(scd.values())}
        st.bar_chart(scd_data, x="sources", y="clusters", horizontal=False)

    # Top source combos
    with st.expander("Top source combinations"):
        if mesh["source_combos"]:
            combo_rows = [{"combination": k, "clusters": v} for k, v in mesh["source_combos"].items()]
            st.dataframe(combo_rows, use_container_width=True, hide_index=True)

else:
    st.info("No mesh data yet. Run the pipeline to build the mesh.")


# ── Cluster explorer ───────────────────────────────────────────────────────────

st.subheader("Cluster explorer")

search_query = st.text_input(
    "Search devices by name, serial, MAC, IP, or assigned user",
    placeholder="e.g. ws28, MZ015CF2, 192.168.20.35",
    label_visibility="collapsed",
)

if search_query:
    results = _search_devices(search_query)
    if results:
        st.caption(f'Found {len(results)} device(s) matching "{search_query}"')

        # Build display table
        display_cols = ["device_name", "source_count", "sources", "serial_number", "mac_address", "manufacturer"]
        display_data = []
        for r in results:
            display_data.append(
                {
                    "Device": r["device_name"],
                    "Sources": r["source_count"],
                    "Source list": ", ".join(r["sources"]) if isinstance(r["sources"], list) else str(r["sources"]),
                    "Serial": r["serial_number"] or "—",
                    "MAC": r["mac_address"] or "—",
                    "Manufacturer": r["manufacturer"] or "—",
                }
            )

        st.dataframe(display_data, use_container_width=True, hide_index=True)

        # Selected device detail
        selected = st.selectbox(
            "Select device to view details",
            options=[r["device_name"] for r in results],
            label_visibility="collapsed",
        )
        if selected:
            dev = next(r for r in results if r["device_name"] == selected)
            with st.container(border=True):
                detail_col1, detail_col2 = st.columns(2)
                with detail_col1:
                    st.markdown(f"**Device:** {dev['device_name']}")
                    st.markdown(f"**Cluster ID:** `{dev['cluster_id']}`")
                    st.markdown(f"**Source count:** {dev['source_count']}")
                    st.markdown(
                        f"**Sources:** {', '.join(dev['sources']) if isinstance(dev['sources'], list) else dev['sources']}"
                    )
                    st.markdown(f"**Record count:** {dev['record_count']}")
                with detail_col2:
                    st.markdown(f"**Serial:** {dev['serial_number'] or '—'}")
                    st.markdown(f"**MAC:** {dev['mac_address'] or '—'}")
                    st.markdown(f"**Manufacturer:** {dev['manufacturer'] or '—'}")
                    st.markdown(f"**Model:** {dev['model'] or '—'}")
                    st.markdown(f"**OS:** {dev['os'] or '—'}")
                    st.markdown(f"**Assigned user:** {dev['assigned_user'] or '—'}")
                    st.markdown(f"**IP address:** {dev['ip_address'] or '—'}")
    else:
        st.info(f'No devices found matching "{search_query}"')
