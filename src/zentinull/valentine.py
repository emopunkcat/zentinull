"""Valentine schema matching — auto-discovers cross-source column matches.

Runs COMA with use_instances=True across all source DataFrames,
builds a field registry from connected components, merges with manual
single-source concepts, and saves to data/field_registry_auto.json.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from .config import get_paths
from .ingestors.base import validate_identifier
from .logging_config import get_logger

log = get_logger("valentine")

#: Manual registry for single-source / low-signal concepts Valentine can't discover.
#: These are fields that exist in only one source, or where Valentine's column-name
#: matching produces noise (e.g. procurement fields with no cross-source equivalent).
MANUAL_REGISTRY: dict[str, list[tuple[str, str]]] = {
    "purchase_cost": [("sp", "fields.Cost"), ("sdp", "purchase_cost")],
    "purchase_order": [("sp", "fields.PONumber"), ("sp", "fields.MLPONumber")],
    "purchase_date": [("sp", "fields.PurchaseDate")],
    "total_cost": [("sp", "fields.TotalofAllPurchasesforDevice"), ("sdp", "total_cost")],
    "component_cost": [("sp", "fields.TotalofComponentPurchases")],
    "product_number": [("sp", "fields.ProductNumber")],
    "phone_number": [("sp", "fields.MobileNumber")],
    "iccid": [("sp", "fields.ICCID")],
    "mobile_service": [("sp", "fields.MobileService")],
    "sim_type": [("sp", "fields.SIMType")],
    "category": [("sp", "fields.ViewCategory"), ("sdp", "category.name")],
    "form_factor": [("sp", "fields.ViewType"), ("sdp", "product.product_type.display_name")],
    "tpm_version": [("sp", "fields.TPMVersion")],
    "branch_office": [("me_ec", "branch_office_name")],
    "agent_version": [("me_ec", "agent_version")],
    "agent_status": [("me_ec", "installation_status")],
    "live_status": [("me_ec", "computer_live_status")],
    "last_scan_time": [("me_ec", "last_successful_scan")],
    "domain": [("me_ec", "domain_netbios_name")],
    "os_build": [("me_ec", "build_number")],
    "os_service_pack": [("me_ec", "service_pack")],
    "battery_level": [("me_mdm", "battery_level")],
    "udid": [("me_mdm", "udid")],
    "owned_by": [("me_mdm", "owned_by")],
    "is_supervised": [("me_mdm", "is_supervised")],
    "vlan": [("fg", "detected_interface")],
    "is_online": [("fg", "is_online")],
    "vdom": [("fg", "vdom")],
    "location": [("zbx", "location")],
}

_SOURCES_CONFIG = [
    ("sp", "sp.sqlite", "sp_devices", "fields"),
    ("me_ec", "me.sqlite", "computers", None),
    ("me_mdm", "me.sqlite", "mdm_devices", None),
    ("fg", "fg.sqlite", "clients", None),
    ("zbx", "zbx.sqlite", "hosts", None),
    ("sdp", "sdp.sqlite", "assets", None),
]


def _flatten(obj: Any, prefix: str = "", out: dict[str, str] | None = None) -> dict[str, str]:
    """Flatten a nested dict into dotted-key → string pairs."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _flatten(v, key, out)
            elif isinstance(v, list):
                out[key] = json.dumps(v)[:200]
            elif v is not None and str(v).strip():
                out[key] = str(v)
    return out


def _load_source_dfs() -> dict[str, pd.DataFrame]:
    """Load all source raw_json as DataFrames for Valentine."""
    sources: dict[str, pd.DataFrame] = {}
    for name, db_file, table, wrapper in _SOURCES_CONFIG:
        paths = get_paths()
        db_path = paths.data_dir / db_file
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            valid_table = validate_identifier(table)
            rows = conn.execute(f"SELECT raw_json FROM {valid_table}").fetchall()
        except sqlite3.OperationalError:
            conn.close()
            continue
        records: list[dict[str, str]] = []
        for r in rows:
            try:
                raw = json.loads(r["raw_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if wrapper:
                raw = raw.get(wrapper, raw)
            records.append(_flatten(raw))
        conn.close()
        if records:
            df = pd.DataFrame(records)
            df["_source"] = name
            sources[name] = df
    return sources


def _save_registry(registry: dict[str, list[tuple[str, str]]]) -> None:
    """Write the field registry to ``data/field_registry_auto.json``."""
    paths = get_paths()
    registry_path = paths.data_dir / "field_registry_auto.json"
    registry_path.write_text(json.dumps({k: [[s, c] for s, c in v] for k, v in registry.items()}, indent=2))


def run_valentine() -> dict[str, list[tuple[str, str]]]:
    """Run Valentine matching, return merged field registry.

    Saves registry to data/field_registry_auto.json.
    Falls back to MANUAL_REGISTRY if the ``valentine`` library is not installed.
    """
    try:
        from valentine import valentine_match  # type: ignore[import-not-found]
        from valentine.algorithms import Coma  # type: ignore[import-not-found]
    except ImportError:
        log.warning({"event": "valentine_skip", "reason": "valentine_library_not_installed"})
        _save_registry(MANUAL_REGISTRY)
        return MANUAL_REGISTRY

    sources = _load_source_dfs()
    if len(sources) < 2:
        log.warning({"event": "valentine_skip", "reason": "need 2+ sources", "sources": len(sources)})
        _save_registry(MANUAL_REGISTRY)
        return MANUAL_REGISTRY
    matcher = Coma(max_n=10, use_instances=True, use_schema=True, delta=0.15, threshold=0.3)
    matches = valentine_match(list(sources.values()), matcher, df_names=list(sources.keys()))

    try:
        one_to_one = matches.one_to_one_hungarian(threshold=0.3)
    except Exception:
        one_to_one = matches

    # Build connected components
    clusters: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for pair, score in one_to_one.items():
        if score < 0.3:
            continue
        src = (pair.source_table, pair.source_column)
        tgt = (pair.target_table, pair.target_column)
        clusters[src].add(tgt)
        clusters[tgt].add(src)

    visited: set[tuple[str, str]] = set()
    auto_registry: dict[str, list[tuple[str, str]]] = {}
    for node in clusters:
        if node in visited:
            continue
        queue = [node]
        component: set[tuple[str, str]] = set()
        while queue:
            n = queue.pop(0)
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            for neighbor in clusters[n]:
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(component) < 2:
            continue
        by_source: dict[str, list[str]] = defaultdict(list)
        for src_name, col in component:
            by_source[src_name].append(col)
        if len(by_source) < 2:
            continue
        all_cols = [col for cols in by_source.values() for col in cols]
        concept_name = re.sub(r"^fields\.", "", max(set(all_cols), key=all_cols.count))
        concept_name = re.sub(r"^inventory\.", "", concept_name)
        concept_name = concept_name.lower().replace(" ", "_")
        auto_registry[concept_name] = [(s, c) for s, cols in by_source.items() for c in cols]

    # Merge: manual wins on conflict, auto adds new concepts
    merged = dict(MANUAL_REGISTRY)
    for concept, mappings in auto_registry.items():
        if concept not in merged:
            merged[concept] = mappings
        else:
            existing = {s for s, _ in merged[concept]}
            for src_name, col in mappings:
                if src_name not in existing:
                    merged[concept].append((src_name, col))

    _save_registry(merged)
    log.info({"event": "valentine_done", "auto": len(auto_registry), "merged": len(merged)})
    return merged
