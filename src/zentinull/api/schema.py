"""Shared DuckDB schema definitions for the mesh database.

This is the single source of truth for the mesh database schema.
Both pipeline.py and cli/pipeline.py use these definitions to avoid drift.

Usage:
    conn = duckdb.connect(str(db_path))
    conn.execute(SOURCE_RECORDS_SQL, [csv_path])
    conn.execute(DEVICES_SQL)
    conn.execute(METRICS_SQL)
    conn.execute(EVENTS_SQL)
    conn.execute(ATTACHMENTS_SQL)
    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
"""

from __future__ import annotations

import duckdb

#: SQL to create source_records from a clusters CSV file (takes one ? param for file path).
#: all_varchar forces every column to VARCHAR — the contract fields are all textual
#: (see manifest device profile fields / api.models.SourceRecord). Without it, read_csv_auto
#: infers BIGINT for all-numeric columns (serial_number, imei, cluster_id), which breaks
#: DEVICES_SQL's `!= ''` comparisons and the API's lower(...) LIKE lookup cascade.
SOURCE_RECORDS_SQL = """
CREATE OR REPLACE TABLE source_records AS
SELECT * FROM read_csv_auto(?, all_varchar=true)
"""

#: Build the consolidated devices table from source_records
DEVICES_SQL = """
CREATE OR REPLACE TABLE devices AS
SELECT
    cluster_id,
    COALESCE(
        NULLIF(MIN(CASE WHEN name_clean != '' THEN name_clean END), ''),
        NULLIF(MIN(CASE WHEN name != '' THEN name END), ''),
        '(unnamed)'
    ) AS device_name,
    COUNT(DISTINCT source) AS source_count,
    LIST(DISTINCT source ORDER BY source) AS sources,
    COALESCE(NULLIF(MIN(CASE WHEN serial_number != '' THEN serial_number END), ''), '') AS serial_number,
    COALESCE(NULLIF(MIN(CASE WHEN mac_clean != '' THEN mac_clean END), ''), '') AS mac_address,
    COALESCE(NULLIF(MIN(CASE WHEN manufacturer != '' THEN manufacturer END), ''), '') AS manufacturer,
    COALESCE(NULLIF(MIN(CASE WHEN model != '' THEN model END), ''), '') AS model,
    COALESCE(NULLIF(MIN(CASE WHEN os != '' THEN os END), ''), '') AS os,
    COALESCE(NULLIF(MIN(CASE WHEN os_version != '' THEN os_version END), ''), '') AS os_version,
    COALESCE(NULLIF(MIN(CASE WHEN asset_tag != '' THEN asset_tag END), ''), '') AS asset_tag,
    COALESCE(NULLIF(MIN(CASE WHEN assigned_user != '' THEN assigned_user END), ''), '') AS assigned_user,
    COALESCE(NULLIF(MIN(CASE WHEN ip_address != '' THEN ip_address END), ''), '') AS ip_address,
    COALESCE(NULLIF(MIN(CASE WHEN imei != '' THEN imei END), ''), '') AS imei,
    COUNT(*) AS record_count
FROM source_records
GROUP BY cluster_id
"""

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


def create_mesh_tables(conn: duckdb.DuckDBPyConnection, csv_path: str) -> None:
    """Create all mesh schema objects and load data from CSV.

    Args:
        conn: Open writable DuckDB connection.
        csv_path: Path to clusters.csv for source_records.
    """
    conn.execute(SOURCE_RECORDS_SQL, [csv_path])
    conn.execute(DEVICES_SQL)
    conn.execute(METRICS_SQL)
    conn.execute(EVENTS_SQL)
    conn.execute(ATTACHMENTS_SQL)
    conn.execute(INDEXES_SQL)
    conn.execute("CHECKPOINT")
