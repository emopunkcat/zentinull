"""Contract alignment tests to prevent schema drift across export, splink, and models."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from zentinull.api.models import ClusterInfo, SourceRecord
from zentinull.api.schema import DEVICES_SQL
from zentinull.contracts import SPLINK_FIELDS


def test_export_field_map_covers_splink_fields() -> None:
    """Verify that export FIELD_MAP maps to all core SPLINK_FIELDS."""
    from zentinull.export_for_splink import FIELD_MAP

    mapped: set[str] = set()
    for _table_key, mapping in FIELD_MAP.items():
        mapped.update(mapping.values())

    core_fields = {"serial_number", "mac_address", "manufacturer", "model", "os", "assigned_user", "ip_address"}
    missing = [f for f in core_fields if f not in mapped]
    assert not missing, f"Core fields missing from FIELD_MAP: {missing}"


def test_devices_sql_uses_contract_columns() -> None:
    """Verify that DEVICES_SQL references SPLINK_FIELDS columns."""
    for field in SPLINK_FIELDS:
        if field in ("source", "source_id", "extra_attributes"):
            continue  # per-record fields not meaningful in consolidated devices table
        assert field in DEVICES_SQL, f"DEVICES_SQL does not reference contract field: {field}"


def test_source_record_covers_splink_fields() -> None:
    """Verify that the SourceRecord Pydantic model declares all unified Splink fields."""
    model_fields = set(SourceRecord.model_fields.keys())
    for field in SPLINK_FIELDS:
        assert field in model_fields, f"SourceRecord model is missing contract field: {field}"


def test_splink_script_retains_all_fields() -> None:
    """Verify that run_splink.py retains all unified columns during clustering."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_splink.py"
    assert script_path.exists(), "run_splink.py not found"

    content = script_path.read_text()
    match = re.search(r"additional_columns_to_retain=\[\s*([\s\S]*?)\]", content)
    assert match is not None, "Could not locate additional_columns_to_retain in run_splink.py"

    retained = {c.strip(" \n'\",") for c in match.group(1).split(",")}
    retained = {c for c in retained if c}

    for field in SPLINK_FIELDS:
        assert field in retained, f"run_splink.py fails to retain contract column: {field}"


def test_duckdb_schema_retains_fields_and_models() -> None:
    """Verify that the devices schema defines fields matching ClusterInfo model attributes."""
    # Create an in-memory DuckDB to parse output column names
    conn = duckdb.connect(":memory:")
    try:
        # Create temp source_records with SPLINK_FIELDS
        cols_ddl = ", ".join(f"{f} TEXT" for f in SPLINK_FIELDS)
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
