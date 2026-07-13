"""Shared data contract constants — single source of truth for field names.

This file is the authoritative list of column names used across the Splink
pipeline, DuckDB schema, and API. Every layer reads from here to prevent drift.
"""

#: Unified column names for the Splink CSV export and DuckDB mesh schema.
#: Every field here appears as a column in clusters.csv, a column in the
#: source_records table, and (where applicable) in the consolidated devices table.
SPLINK_FIELDS = [
    "source",  # which source system
    "source_id",  # original ID in that system
    "name",  # device name / hostname / title (raw)
    "name_clean",  # normalized: lowercase, strip domain suffix
    "serial_number",  # matches across SP, ME, MDM, SDP, ZBX
    "mac_address",  # matches across SP, FG, ME, ZBX, MDM (raw)
    "mac_clean",  # normalized: lowercase, strip :-.,
    "asset_tag",  # SP + SDP only
    "manufacturer",  # hardware vendor
    "model",  # device model
    "os",  # operating system
    "os_version",  # OS version
    "assigned_user",  # who uses this device
    "ip_address",  # network location
    "imei",  # MDM mobile identifier
    "extra_attributes",  # JSON-serialized dict of unmapped source columns + raw_json extras
]
