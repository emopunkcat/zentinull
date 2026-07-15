"""Tier-1 structural field classifier.

Deterministic regex-based classification of raw values into unified field types.
Used by the audit-mapping --propose mode to suggest field mappings for unmapped keys.

Patterns ordered by priority (MAC > IP > IMEI > email > serial > hostname).
"""

from __future__ import annotations

import re

from ..normalizer import NULL_SENTINELS

# ── Structural signature patterns (Tier-1) ────────────────────────────

patterns: dict[str, re.Pattern[str]] = {
    "mac_address": re.compile(r"^([0-9A-Fa-f]{2}[:.\s\-]){5}[0-9A-Fa-f]{2}$"),
    "ip_address": re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
    "imei": re.compile(r"^\d{15}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "serial_number": re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{6,20}$"),
    "hostname": re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{1,14}[a-zA-Z0-9]$"),
}


def classify_value(value: str) -> str | None:
    """Classify a single value against Tier-1 structural patterns.

    Returns the target field name (e.g., "mac_address", "serial_number") or None.
    Pattern priority: MAC > IP > IMEI > email > serial > hostname.
    """
    if not value or not value.strip() or value in NULL_SENTINELS:
        return None

    val = value.strip()

    for pattern_name, pattern in patterns.items():
        if pattern.fullmatch(val):
            return pattern_name

    return None


def classify_key_value(value: str) -> list[tuple[str, str, float]]:
    """Classify a value. Returns list of (target_field, type_name, confidence)."""
    result = classify_value(value)
    if result:
        return [(result, result, 1.0)]
    return []
