"""Transform registry — maps transform names to normalizer functions.

Transforms are applied during field extraction to normalize raw values
for Splink matching (e.g., MAC address normalization, serial number cleanup).
"""

from __future__ import annotations

from collections.abc import Callable

from ..normalizer import normalize_mac, normalize_name, normalize_serial

#: Registry of transform functions.
#: Keys are transform names used in FieldSpec.transform.
#: Values are callables: (raw_value: str) -> str
REGISTRY: dict[str, Callable[[str], str]] = {
    "mac": normalize_mac,
    "serial": normalize_serial,
    "name": normalize_name,
    "lower": str.lower,
    "first_of_list": lambda s: s.split(",")[0].strip() if "," in s else s.strip(),
    "join_list": lambda s: ", ".join(x.strip() for x in s.split(",") if x.strip()),
}
