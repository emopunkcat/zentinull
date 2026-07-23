"""Shared DuckDB schema definitions for the mesh database.

This is the single source of truth for the mesh database schema.
Both pipeline.py and cli/pipeline.py use these definitions to avoid drift.

Usage:
    conn = duckdb.connect(str(db_path))
    conn.execute(SOURCE_RECORDS_SQL, [csv_path])
    conn.execute(build_devices_sql(profile))
    conn.execute(METRICS_SQL)
    conn.execute(EVENTS_SQL)
    conn.execute(ATTACHMENTS_SQL)
    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from ..manifest.types import ResolutionProfile

#: SQL to create source_records from a clusters CSV file (takes one ? param for file path).
#: all_varchar forces every column to VARCHAR — the contract fields are all textual
#: (see manifest device profile fields / api.models.SourceRecord). Without it, read_csv_auto
#: infers BIGINT for all-numeric columns (serial_number, imei, cluster_id), which breaks
#: DEVICES_SQL's `!= ''` comparisons and the API's lower(...) LIKE lookup cascade.
SOURCE_RECORDS_SQL = """
CREATE OR REPLACE TABLE source_records AS
SELECT * FROM read_csv_auto(?, all_varchar=true)
"""


def build_devices_sql(profile: ResolutionProfile) -> str:
    """Generate CREATE OR REPLACE TABLE devices SQL from profile config.

    Fields with SOT entries use priority COALESCE chains (primary source,
    secondary source, best-effort MIN, empty string). Fields without SOT
    use the MIN() fallback shape. device_name/source_count/sources/
    record_count are special computed aggregates.
    """
    # Fields that should not appear as individual columns in devices
    skip_cols = frozenset(
        {
            "source",
            "source_id",
            "extra_attributes",
            "name_clean",
            "mac_clean",
            "name_fallback",
        }
    )
    # Name feeds into device_name — no separate column
    no_col = frozenset({"name"})

    sot = profile.sot
    data_cols: list[str] = []

    for field in profile.fields:
        if field in skip_cols or field in no_col:
            continue

        if field in sot:
            primary, secondary = sot[field]
            parts: list[str] = []
            if primary:
                parts.append(f"NULLIF(MAX(CASE WHEN source = '{primary}' AND {field} != '' THEN {field} END), '')")
            if secondary:
                parts.append(f"NULLIF(MAX(CASE WHEN source = '{secondary}' AND {field} != '' THEN {field} END), '')")
            parts.append(f"NULLIF(MIN(CASE WHEN {field} != '' THEN {field} END), '')")
            parts.append("''")
            data_cols.append(f"    COALESCE(\n        {',\n        '.join(parts)}\n    ) AS {field}")
        else:
            data_cols.append(f"    COALESCE(NULLIF(MIN(CASE WHEN {field} != '' THEN {field} END), ''), '') AS {field}")

    data_body = ",\n".join(data_cols)

    sql = (
        "CREATE OR REPLACE TABLE devices AS\n"
        "SELECT\n"
        "    cluster_id,\n"
        "    COALESCE(\n"
        "        NULLIF(MIN(CASE WHEN name_clean != '' THEN name_clean END), ''),\n"
        "        NULLIF(MIN(CASE WHEN name != '' THEN name END), ''),\n"
        "        '(unnamed)'\n"
        "    ) AS device_name,\n"
        "    COUNT(DISTINCT source) AS source_count,\n"
        "    LIST(DISTINCT source ORDER BY source) AS sources,\n"
        f"{data_body},\n"
        "    COUNT(*) AS record_count\n"
        "FROM source_records\n"
        "GROUP BY cluster_id\n"
    )
    return sql


#: Append-only metrics table
METRICS_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    cluster_id TEXT NOT NULL, source TEXT NOT NULL,
    metric_name TEXT NOT NULL, value DOUBLE, text_value TEXT,
    tags TEXT[], recorded_at TIMESTAMP NOT NULL,
    ingested_at TIMESTAMP DEFAULT now()
)
"""

#: Append-only events table
EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS events (
    cluster_id TEXT NOT NULL, source TEXT NOT NULL,
    event_type TEXT NOT NULL, detail TEXT,
    severity TEXT DEFAULT 'info', recorded_at TIMESTAMP NOT NULL,
    ingested_at TIMESTAMP DEFAULT now()
)
"""

#: Append-only attachments table — linked records (never merged into anchors)
ATTACHMENTS_SQL = """
CREATE TABLE IF NOT EXISTS attachments (
    cluster_id TEXT NOT NULL,
    feed_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    payload TEXT DEFAULT '{}',
    linked_at TIMESTAMP DEFAULT now()
)
"""

#: All indexes for the mesh schema
INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_devices_name ON devices(device_name);
CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number);
CREATE INDEX IF NOT EXISTS idx_records_cluster ON source_records(cluster_id);
CREATE INDEX IF NOT EXISTS idx_records_mac ON source_records(mac_clean);
CREATE INDEX IF NOT EXISTS idx_metrics_cluster_time ON metrics(cluster_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name, recorded_at);
CREATE INDEX IF NOT EXISTS idx_events_cluster_time ON events(cluster_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_attachments_cluster ON attachments(cluster_id)
"""


def _safe_col_name(key: str) -> str:
    """Sanitize a JSON key into a safe SQL column name."""
    return key.replace(".", "_").replace("@", "").replace("-", "_").replace(":", "_")


def create_extra_view(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Auto-generate v_extra view from all extra_attributes JSON keys.

    Discovers every JSON key across source_records and creates a view with
    one virtual column per key via json_extract_string. Returns the set of
    safe column names created.
    """
    keys = conn.execute(
        "SELECT DISTINCT unnest(json_keys(extra_attributes::JSON)) AS key "
        "FROM source_records WHERE extra_attributes != '' ORDER BY key"
    ).fetchall()
    all_keys = [k[0] for k in keys]
    if not all_keys:
        conn.execute("CREATE OR REPLACE VIEW v_extra AS SELECT cluster_id, source FROM source_records")
        return set()

    select_cols = ["cluster_id", "source"]
    seen: set[str] = set()
    for k in all_keys:
        safe = _safe_col_name(k)
        if safe in seen:
            continue
        seen.add(safe)
        select_cols.append(f"json_extract_string(extra_attributes::JSON, '$.{k}') AS \"{safe}\"")
    conn.execute(f"CREATE OR REPLACE VIEW v_extra AS SELECT {', '.join(select_cols)} FROM source_records")
    return seen


def create_enriched_view(
    conn: duckdb.DuckDBPyConnection,
    registry: dict[str, list[tuple[str, str]]],
) -> None:
    """Auto-generate v_device_enriched from the field registry.

    For each concept, COALESCE across all source mappings. Pulls from
    source_records top-level columns AND v_extra JSON keys.
    """
    sr_cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='source_records'"
        ).fetchall()
    }
    extra_cols: set[str] = set()
    with contextlib.suppress(duckdb.Error):
        extra_cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='v_extra'"
            ).fetchall()
        }

    concept_cols: list[str] = []
    for concept, source_paths in registry.items():
        coalesce_parts: list[str] = []
        for source, key_path in source_paths:
            safe = _safe_col_name(key_path)
            if key_path in sr_cols:
                coalesce_parts.append(f"MAX(CASE WHEN sr.source = '{source}' THEN sr.{key_path} END)")
            elif safe in extra_cols:
                coalesce_parts.append(f"MAX(CASE WHEN sr.source = '{source}' THEN e.\"{safe}\" END)")
            else:
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


def create_mesh_tables(
    conn: duckdb.DuckDBPyConnection, csv_path: str, profile: ResolutionProfile | None = None
) -> None:
    """Create all mesh schema objects and load data from CSV.

    Args:
        conn: Open writable DuckDB connection.
        csv_path: Path to clusters.csv for source_records.
        profile: Resolution profile for devices SQL generation.
            Defaults to manifest's device profile when None.
    """
    if profile is None:
        from ..manifest import load_manifest

        profile = load_manifest().profiles["device"]
    conn.execute(SOURCE_RECORDS_SQL, [csv_path])
    conn.execute(build_devices_sql(profile))
    conn.execute(METRICS_SQL)
    conn.execute(EVENTS_SQL)
    conn.execute(ATTACHMENTS_SQL)
    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
