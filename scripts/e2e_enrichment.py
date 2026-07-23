"""
End-to-end pipeline: Valentine schema matching → field registry → DuckDB views → API enrichment.

This script demonstrates the complete flow:
1. Load all source DataFrames from SQLite
2. Run Valentine to auto-discover column matches across all source pairs
3. Merge auto-discovered concepts with manual registry (for single-source concepts)
4. Create v_extra + v_device_enriched views in DuckDB
5. Run LLM hardware extraction on SP freeform descriptions
6. Output the final enriched device view

Usage:
    python scripts/e2e_enrichment.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd
import requests

# ── Config ─────────────────────────────────────────────────────────────────

DB_DIR = Path("C:/Users/jejo/Documents/GitHub/zentinull/data")
MESH_DB = DB_DIR / "mesh.duckdb"
LMSTUDIO_URL = "http://localhost:1234/v1/chat/completions"
VALENTINE_THRESHOLD = 0.3  # minimum match score to include in registry
LLM_MODEL = "lfm2.5-8b-a1b"
LLM_MAX_TOKENS = 2000
LLM_CONCURRENCY = 4

# ── Manual registry for single-source / low-signal concepts Valentine can't find ──

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

# ── 1. Load source DataFrames ──────────────────────────────────────────────


def load_source_df(db_path: Path, table: str, source_name: str) -> pd.DataFrame | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT raw_json FROM {table}").fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return None

    records = []
    for r in rows:
        try:
            raw = json.loads(r["raw_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        flat: dict[str, str] = {}

        def flatten(obj: object, prefix: str = "", _flat: dict[str, str] = flat) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        flatten(v, key, _flat)
                    elif isinstance(v, list):
                        _flat[key] = json.dumps(v)[:200]
                    elif v is not None and str(v).strip():
                        _flat[key] = str(v)

        flatten(raw)
        records.append(flat)

    conn.close()
    if not records:
        return None
    df = pd.DataFrame(records)
    df["_source"] = source_name
    print(f"  {source_name}: {len(df)} rows, {len(df.columns)} columns")
    return df


# ── 2. Valentine schema matching ───────────────────────────────────────────


def run_valentine(sources: dict[str, pd.DataFrame]) -> dict[str, list[tuple[str, str]]]:
    """Run Valentine COMA matcher across all source pairs.

    Returns auto-discovered concept registry: concept_name → [(source, column), ...]
    """
    from valentine import valentine_match
    from valentine.algorithms import Coma

    print("\nRunning Valentine COMA matcher (schema + instances)...")
    matcher = Coma(
        max_n=10,
        use_instances=True,
        use_schema=True,
        delta=0.15,
        threshold=VALENTINE_THRESHOLD,
    )

    df_list = list(sources.values())
    df_names = list(sources.keys())

    start = time.time()
    matches = valentine_match(df_list, matcher, df_names=df_names)
    elapsed = time.time() - start
    print(f"  Completed in {elapsed:.1f}s, {len(matches)} raw matches")

    # Build one-to-one matches
    try:
        one_to_one = matches.one_to_one_hungarian(threshold=VALENTINE_THRESHOLD)
    except Exception:
        one_to_one = matches
    print(f"  One-to-one matches: {len(one_to_one)}")

    # Build connected components (clusters of matching columns across sources)
    column_clusters: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for pair, score in one_to_one.items():
        if score < VALENTINE_THRESHOLD:
            continue
        src = (pair.source_table, pair.source_column)
        tgt = (pair.target_table, pair.target_column)
        column_clusters[src].add(tgt)
        column_clusters[tgt].add(src)

    visited: set[tuple[str, str]] = set()
    concepts: list[dict[str, list[tuple[str, str]]]] = []
    for node in column_clusters:
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
            for neighbor in column_clusters[n]:
                if neighbor not in visited:
                    queue.append(neighbor)

        if len(component) >= 2:
            by_source: dict[str, list[str]] = defaultdict(list)
            for src, col in component:
                by_source[src].append(col)
            if len(by_source) >= 2:
                # Name the concept after the most common column name
                all_cols = [col for cols in by_source.values() for col in cols]
                concept_name = max(set(all_cols), key=all_cols.count)
                # Clean up the name
                concept_name = re.sub(r"^fields\.", "", concept_name)
                concept_name = re.sub(r"^inventory\.", "", concept_name)
                concept_name = concept_name.lower().replace(" ", "_")
                concepts.append(
                    {
                        "name": concept_name,
                        "mappings": [(src, col) for src, cols in by_source.items() for col in cols],
                    }
                )

    # Deduplicate by concept name (keep the one with most sources)
    seen_names: dict[str, dict] = {}
    for c in concepts:
        name = c["name"]
        if name not in seen_names or len(c["mappings"]) > len(seen_names[name]["mappings"]):
            seen_names[name] = c

    registry: dict[str, list[tuple[str, str]]] = {}
    for name, c in seen_names.items():
        registry[name] = c["mappings"]

    print(f"  Auto-discovered {len(registry)} concepts")
    return registry


# ── 3. Merge auto-discovered + manual registry ────────────────────────────


def merge_registries(
    auto: dict[str, list[tuple[str, str]]],
    manual: dict[str, list[tuple[str, str]]],
) -> dict[str, list[tuple[str, str]]]:
    """Merge auto-discovered and manual registries.

    Manual entries win on conflict (they're curated).
    Auto entries add concepts manual missed.
    """
    merged = dict(manual)  # start with manual

    for concept, mappings in auto.items():
        if concept not in merged:
            # Auto-discovered concept not in manual — add it
            merged[concept] = mappings
        else:
            # Both have it — merge the source mappings, manual takes priority
            existing_sources = {s for s, _ in merged[concept]}
            for src, col in mappings:
                if src not in existing_sources:
                    merged[concept].append((src, col))

    return merged


# ── 4. Create DuckDB views ────────────────────────────────────────────────


def _safe_col_name(key: str) -> str:
    return key.replace(".", "_").replace("@", "").replace("-", "_").replace(":", "_")


def create_views(conn: duckdb.DuckDBPyConnection, registry: dict[str, list[tuple[str, str]]]) -> None:
    """Create v_extra + v_device_enriched views.

    v_extra: auto-discovers all JSON keys from extra_attributes.
    v_device_enriched: consolidates registry concepts across sources.

    Some registry concepts reference columns that are top-level in source_records
    (the named profile fields: os_version, serial_number, mac_address, etc.).
    Those are NOT in extra_attributes — the walker already extracted them.
    So v_device_enriched JOINs both source_records and v_extra.
    """

    # Get source_records column names (top-level profile fields)
    sr_cols = {
        r[0]
        for r in conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='source_records'
    """).fetchall()
    }

    # v_extra — auto-discover all JSON keys from extra_attributes
    keys = conn.execute("""
        SELECT DISTINCT unnest(json_keys(extra_attributes::JSON)) AS key
        FROM source_records
        WHERE extra_attributes != ''
        ORDER BY key
    """).fetchall()
    all_keys = [k[0] for k in keys]

    if all_keys:
        select_cols = ["cluster_id", "source"]
        seen: set[str] = set()
        for k in all_keys:
            safe = _safe_col_name(k)
            if safe in seen:
                continue
            seen.add(safe)
            select_cols.append(f"json_extract_string(extra_attributes::JSON, '$.{k}') AS \"{safe}\"")
        conn.execute(f"CREATE OR REPLACE VIEW v_extra AS SELECT {', '.join(select_cols)} FROM source_records")
    print(f"  v_extra: {len(seen)} columns")

    # v_device_enriched — consolidate registry concepts.
    # Build a combined source: source_records (top-level fields) + v_extra (JSON keys).
    # For each concept, COALESCE across all source×column mappings.
    # If the column is a top-level source_records field, use it directly.
    # If it's an extra_attributes key, use the v_extra view.
    concept_cols = []
    for concept, source_paths in registry.items():
        coalesce_parts = []
        for source, key_path in source_paths:
            safe_col = _safe_col_name(key_path)
            if key_path in sr_cols:
                # Top-level source_records column (e.g. os_version, serial_number)
                coalesce_parts.append(f"MAX(CASE WHEN sr.source = '{source}' THEN sr.{key_path} END)")
            elif safe_col in seen:
                # Column exists in v_extra (extra_attributes JSON key)
                coalesce_parts.append(f"MAX(CASE WHEN sr.source = '{source}' THEN e.\"{safe_col}\" END)")
            else:
                # Column not found in either — skip
                continue
        if coalesce_parts:
            concept_cols.append(f'COALESCE({", ".join(coalesce_parts)}) AS "{concept}"')

    if concept_cols:
        conn.execute(
            "CREATE OR REPLACE VIEW v_device_enriched AS "
            f"SELECT sr.cluster_id, {', '.join(concept_cols)} "
            "FROM source_records sr "
            "LEFT JOIN v_extra e ON sr.cluster_id = e.cluster_id AND sr.source = e.source "
            "GROUP BY sr.cluster_id"
        )
    print(f"  v_device_enriched: {len(concept_cols)} concepts")


# ── 5. LLM hardware extraction ────────────────────────────────────────────


_HW_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_hardware",
        "description": "Extract hardware specs from device description. Only include fields explicitly stated. Empty string if not mentioned.",
        "parameters": {
            "type": "object",
            "properties": {
                "hw_cpu": {"type": "string"},
                "hw_ram": {"type": "string"},
                "hw_storage": {"type": "string"},
                "hw_gpu": {"type": "string"},
                "hw_psu": {"type": "string"},
                "hw_motherboard": {"type": "string"},
                "hw_screen": {"type": "string"},
                "hw_network": {"type": "string"},
                "hw_other": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HW_LABEL_RE = re.compile(
    r"(Processor|CPU|GPU|Graphics|Memory|Storage|Hard Drive|"
    r"AC Adapter\s*/\s*Power Supply|AC Adapter|Power Supply|"
    r"WIFI|Wireless Network|Motherboard)\s*:?\s*",
    re.IGNORECASE,
)
_HW_FIELD_MAP = {
    "processor": "hw_cpu",
    "cpu": "hw_cpu",
    "gpu": "hw_gpu",
    "graphics": "hw_gpu",
    "memory": "hw_ram",
    "storage": "hw_storage",
    "hard drive": "hw_storage",
    "ac adapter / power supply": "hw_psu",
    "ac adapter": "hw_psu",
    "power supply": "hw_psu",
    "wifi": "hw_wifi",
    "wireless network": "hw_wifi",
    "motherboard": "hw_motherboard",
}


def parse_hardware_regex(html_text: str) -> dict[str, str]:
    """Regex pre-pass for labeled descriptions."""
    import html as _html

    if not html_text or not isinstance(html_text, str):
        return {}
    text = _HTML_TAG_RE.sub(" ", html_text)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {}
    matches = list(_HW_LABEL_RE.finditer(text))
    if not matches:
        return {}
    result: dict[str, str] = {}
    for i, m in enumerate(matches):
        label = m.group(1).strip().lower()
        unified = _HW_FIELD_MAP.get(label)
        if unified is None or unified in result:
            continue
        start = m.end()
        end = len(text)
        for j in range(i + 1, len(matches)):
            next_label = matches[j].group(1).strip().lower()
            next_unified = _HW_FIELD_MAP.get(next_label)
            if next_unified != unified:
                end = matches[j].start()
                break
        value = text[start:end].strip().rstrip("&;,")
        if value:
            result[unified] = value
    return result


def extract_hardware_llm(desc: str) -> dict[str, str]:
    """Single device LLM extraction via tool call."""
    try:
        resp = requests.post(
            LMSTUDIO_URL,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": f"Extract hardware specs from:\n{desc}"}],
                "tools": [_HW_TOOL],
                "tool_choice": "required",
                "temperature": 0.1,
                "max_tokens": LLM_MAX_TOKENS,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        if msg.get("tool_calls"):
            args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
            return {k: v for k, v in args.items() if v and str(v).strip()}
    except Exception:
        pass
    return {}


def run_hw_extraction(sp_db: Path, cache_path: Path) -> dict[str, dict[str, str]]:
    """Two-tier: regex pre-pass + LLM batch for freeform descriptions."""
    import concurrent.futures

    conn = sqlite3.connect(str(sp_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT raw_json FROM sp_devices").fetchall()
    conn.close()

    # Load cache
    cache: dict[str, dict[str, str]] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())

    devices: list[tuple[str, str]] = []
    for r in rows:
        raw = json.loads(r["raw_json"])
        fields = raw.get("fields", {})
        desc = fields.get("ProductDescription", "")
        title = fields.get("Title", "")
        if desc and desc.strip():
            clean = _HTML_TAG_RE.sub(" ", desc)
            clean = re.sub(r"\s+", " ", clean).strip()
            devices.append((title, clean[:400]))

    # Phase 1: regex pre-pass
    regex_results: dict[str, dict[str, str]] = {}
    for title, desc in devices:
        hw = parse_hardware_regex(desc)
        if hw:
            regex_results[title] = hw

    print(f"  Regex pre-pass: {len(regex_results)} devices extracted")

    # Phase 2: LLM for devices where regex found nothing AND not in cache
    to_extract = [(t, d) for t, d in devices if t not in regex_results and t not in cache]
    print(f"  LLM batch: {len(to_extract)} devices to extract")

    if to_extract:
        with concurrent.futures.ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as pool:
            futures = {pool.submit(extract_hardware_llm, d): t for t, d in to_extract}
            for future in concurrent.futures.as_completed(futures):
                title = futures[future]
                hw = future.result()
                if hw:
                    cache[title] = hw

        cache_path.write_text(json.dumps(cache, indent=2))

    # Merge regex + LLM
    all_hw = dict(regex_results)
    all_hw.update(cache)  # LLM results (cache includes all LLM extractions)
    # Don't let LLM overwrite regex results
    for title in regex_results:
        if title in cache:
            for k, v in regex_results[title].items():
                cache[title][k] = v
            all_hw[title] = cache[title]

    print(f"  Total hardware extracted: {len(all_hw)} devices")
    return all_hw


# ── 6. Main pipeline ──────────────────────────────────────────────────────


def main() -> None:
    print("=" * 70)
    print("ZENTINULL END-TO-END ENRICHMENT PIPELINE")
    print("=" * 70)

    # Step 1: Load sources
    print("\n[1/5] Loading source DataFrames...")
    sources: dict[str, pd.DataFrame] = {}
    for name, db, table in [
        ("sp", DB_DIR / "sp.sqlite", "sp_devices"),
        ("me_ec", DB_DIR / "me.sqlite", "computers"),
        ("me_mdm", DB_DIR / "me.sqlite", "mdm_devices"),
        ("fg", DB_DIR / "fg.sqlite", "clients"),
        ("zbx", DB_DIR / "zbx.sqlite", "hosts"),
        ("sdp", DB_DIR / "sdp.sqlite", "assets"),
    ]:
        df = load_source_df(DB_DIR / db if not str(db).startswith(str(DB_DIR)) else db, table, name)
        if df is not None:
            sources[name] = df
    print(f"  Loaded {len(sources)} sources")

    # Step 2: Valentine schema matching
    print("\n[2/5] Running Valentine schema matching...")
    auto_registry = run_valentine(sources)
    print(f"  Auto-discovered concepts: {len(auto_registry)}")

    # Step 3: Merge registries
    print("\n[3/5] Merging auto-discovered + manual registry...")
    final_registry = merge_registries(auto_registry, MANUAL_REGISTRY)
    print(f"  Final registry: {len(final_registry)} concepts")
    for concept, mappings in sorted(final_registry.items()):
        sources_str = ", ".join(f"{s}:{c}" for s, c in mappings)
        print(f"    {concept:25s} → {sources_str}")

    # Save registry
    registry_path = DB_DIR / "field_registry_auto.json"
    registry_serializable = {concept: [[s, c] for s, c in mappings] for concept, mappings in final_registry.items()}
    registry_path.write_text(json.dumps(registry_serializable, indent=2))
    print(f"  Saved to {registry_path}")

    # Step 4: Create DuckDB views
    print("\n[4/5] Creating DuckDB views...")
    conn = duckdb.connect(str(MESH_DB), read_only=False)
    create_views(conn, final_registry)

    # Test the views
    result = conn.execute("""
        SELECT d.device_name, d.source_count, e.*
        FROM devices d
        JOIN v_device_enriched e ON d.cluster_id = e.cluster_id
        WHERE d.source_count >= 3
        LIMIT 3
    """).fetchall()
    print("\n  Sample enriched devices (3+ sources):")
    for r in result:
        print(f"    {r}")

    conn.close()

    # Step 5: LLM hardware extraction
    print("\n[5/5] SP hardware extraction (regex + LLM)...")
    hw_cache = DB_DIR / "hw_extract_cache.json"
    hw_results = run_hw_extraction(DB_DIR / "sp.sqlite", hw_cache)

    # Show sample hardware extractions
    print("\n  Sample hardware extractions:")
    for title, hw in list(hw_results.items())[:5]:
        fields_str = ", ".join(f"{k}={v}" for k, v in hw.items())
        print(f"    {title:12s} {fields_str}")

    # Summary
    print(f"\n{'=' * 70}")
    print("PIPELINE COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Sources loaded: {len(sources)}")
    print(f"  Valentine concepts: {len(auto_registry)}")
    print(f"  Manual concepts: {len(MANUAL_REGISTRY)}")
    print(f"  Final registry: {len(final_registry)} concepts")
    print(f"  Hardware extracted: {len(hw_results)} devices")
    print(f"  Registry saved: {registry_path}")
    print(f"  HW cache: {hw_cache}")
    print("\n  Next: re-export + re-load to surface hardware in v_device_enriched")


if __name__ == "__main__":
    main()
