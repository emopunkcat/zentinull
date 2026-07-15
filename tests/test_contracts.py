"""Contract alignment tests to prevent schema drift across export, splink, and models."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from zentinull.api.models import ClusterInfo, SourceRecord
from zentinull.api.schema import DEVICES_SQL
from zentinull.manifest import load_manifest


def _splink_fields() -> list[str]:
    """Derive field list from the manifest device profile (replaces deleted contracts.SPLINK_FIELDS)."""
    return list(load_manifest().profiles["device"].fields)


def test_manifest_specs_cover_core_splink_fields() -> None:
    """Verify that manifest ANCHOR feed specs map to all core SPLINK_FIELDS."""
    from zentinull.manifest import get_anchor_feeds

    manifest = load_manifest()
    anchor_feeds = get_anchor_feeds(manifest, profile="device")

    # Collect all spec target field names across all anchor feeds
    mapped: set[str] = set()
    for feed_key in anchor_feeds:
        feed = manifest.feeds[feed_key]
        mapped.update(feed.spec.keys())

    core_fields = {"serial_number", "mac_address", "manufacturer", "model", "os", "assigned_user", "ip_address"}
    missing = [f for f in core_fields if f not in mapped]
    assert not missing, f"Core fields missing from all manifest specs: {missing}"


def test_devices_sql_uses_contract_columns() -> None:
    """Verify that DEVICES_SQL references SPLINK_FIELDS columns."""
    for field in _splink_fields():
        if field in ("source", "source_id", "extra_attributes"):
            continue  # per-record fields not meaningful in consolidated devices table
        assert field in DEVICES_SQL, f"DEVICES_SQL does not reference contract field: {field}"


def test_source_record_covers_splink_fields() -> None:
    """Verify that the SourceRecord Pydantic model declares all unified Splink fields."""
    model_fields = set(SourceRecord.model_fields.keys())
    for field in _splink_fields():
        assert field in model_fields, f"SourceRecord model is missing contract field: {field}"


def test_splink_script_retains_all_fields() -> None:
    """Verify that splink_runner.py retains all unified columns during clustering."""
    script_path = Path(__file__).resolve().parent.parent / "src" / "zentinull" / "resolve" / "splink_runner.py"
    assert script_path.exists(), "splink_runner.py not found"

    content = script_path.read_text()

    # Verify additional_columns_to_retain uses profile.fields
    list_match = re.search(r"additional_columns_to_retain=list\(profile\.fields\)", content)
    assert list_match, "splink_runner.py must use additional_columns_to_retain=list(profile.fields)"


def test_duckdb_schema_retains_fields_and_models() -> None:
    """Verify that the devices schema defines fields matching ClusterInfo model attributes."""
    # Create an in-memory DuckDB to parse output column names
    conn = duckdb.connect(":memory:")
    try:
        # Create temp source_records with SPLINK_FIELDS
        cols_ddl = ", ".join(f"{f} TEXT" for f in _splink_fields())
        conn.execute(f"CREATE TABLE source_records (cluster_id TEXT, {cols_ddl})")
        conn.execute(DEVICES_SQL)

        # Get column list of devices table
        res = conn.execute("DESCRIBE devices").fetchall()
        db_cols = {row[0] for row in res}

        # Verify columns in devices table match/subset ClusterInfo model fields
        model_fields = set(ClusterInfo.model_fields.keys())

        for col in db_cols:
            # cluster_id, device_name, source_count, sources, record_count are calculated fields in model
            if col in ("cluster_id", "device_name", "source_count", "sources", "record_count"):
                continue
            assert col in model_fields, f"ClusterInfo model is missing matching column from devices table: {col}"
    finally:
        conn.close()
