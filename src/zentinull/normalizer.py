"""Export-time normalization for Splink match quality and display hygiene.

Only normalizes fields that affect blocking (serial_number) and strips junk
sentinel values from all fields so the API doesn't show garbage.
Everything else — OS, manufacturer, assigned_user — is left raw for Splink
to learn match weights from real data.
"""

from __future__ import annotations

import re

# ── Sentinel values treated as "no data" ────────────────────────────────
NULL_SENTINELS = frozenset({"", "null", "None", "none", "n/a", "N/A", "--", "-", " "})

# ── Serial number patterns ──────────────────────────────────────────────
# ManageEngine returns this fake serial when no real serial is available
_ME_FAKE_SERIAL_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{4}-\d{2}$")
# Common prefixes to strip for blocking consistency
_SERIAL_PREFIX_RE = re.compile(r"^(SN[-:]|S/N[:]?|SVC[-]|PC[-]|CND[-]|CN[-])\s*", re.IGNORECASE)


def _is_sentinel(val: object) -> bool:
    """True if val is None, empty, or a known null placeholder."""
    if val is None:
        return True
    s = str(val).strip()
    return not s or s in NULL_SENTINELS


def strip_sentinels(val: object) -> str:
    """Return empty string for sentinel/None values, otherwise stripped string."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if not s or s in NULL_SENTINELS else s


# ── MAC address patterns ────────────────────────────────────────────────
# Junk MAC fill-in values that show up when the source system doesn't know
# the real address. These collide with every other record on the same
# source and cause transitive connected-component chains in Splink when
# used as a blocking predicate. Treat as "no data".
_NULL_MAC_RE = re.compile(r"^[0:\-,\s]+$")  # all zeros (and variants)
_ME_FAKE_MAC_RE = re.compile(r"^\d{4}-\d{4}-\d{4}$")


def normalize_name(val: object) -> str:
    """Normalize hostname / device name for Splink blocking.

    1. Strip sentinel values → ""
    2. Lowercase, trim leading/trailing whitespace
    3. Drop domain suffix (everything after first '.')

    Domain suffix stripping prevents 'host.example.com' vs 'host.local'
    from blocking as different names when they represent the same asset.
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s in NULL_SENTINELS:
        return ""
    s = s.lower()
    # Drop domain suffix — keep the first label only
    s = s.split(".")[0]
    return s


def _validate_single_mac(s: str) -> str:
    """Validate and normalize a single MAC address candidate.

    Returns normalized 12-char hex string, or empty string if invalid/junk.
    """
    if not s:
        return ""
    # Lowercase + strip standard separators
    s = re.sub(r"[:\-,.\s]", "", s).lower()
    if not s:
        return ""
    # Junk MACs: all zero (or only separators), ManageEngine fill-ins
    if _NULL_MAC_RE.match(s):
        return ""
    if _ME_FAKE_MAC_RE.match(s):
        return ""
    # A real MAC is exactly 12 hex chars. Anything else is suspicious.
    if len(s) != 12 or not all(c in "0123456789abcdef" for c in s):
        return ""
    return s


def normalize_mac(val: object) -> str:
    """Normalize MAC address for Splink blocking.

    1. Strip sentinel values ("--", "null", "n/a") → ""
    2. If comma-separated list, extract first valid MAC
    3. Lowercase, strip separators (":", "-", "."), strip whitespace
    4. Reject junk MACs (all-zeros, ManageEngine fake patterns) → ""

    Junk-MAC rejection is the critical part. Without it, a single source
    exporting `"00:00:00:00:00:00"` for unknown devices creates one giant
    connected component in Splink (every record matches every other record
    on ExactMatch(mac_clean)). Once one device in that source links to
    its real counterpart in another source, the rest cascade in
    transitively.

    Multi-MAC handling: ManageEngine EC aggregates all NIC MACs into a
    comma-separated list. Extract the first valid MAC for blocking.
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s in NULL_SENTINELS:
        return ""
    # Handle comma-separated MAC lists (e.g., from ManageEngine EC)
    if "," in s:
        for candidate in s.split(","):
            result = _validate_single_mac(candidate.strip())
            if result:
                return result
        return ""
    return _validate_single_mac(s)


def normalize_serial(val: object) -> str:
    """Normalize serial number for Splink blocking.

    1. Strip sentinel values → ""
    2. Reject ManageEngine fake serials → ""
    3. Strip common prefixes (SN-, S/N:, PC-, CND-, etc.)
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s in NULL_SENTINELS:
        return ""
    # Reject ME fake serials
    if _ME_FAKE_SERIAL_RE.match(s):
        return ""
    # Strip common prefixes
    s = _SERIAL_PREFIX_RE.sub("", s)
    return s.strip()
