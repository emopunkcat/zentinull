"""
AD ingest: LDAP query for computers.
Uses LDAPBindAuth from auth.py.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import AD_PASSWORD, AD_SEARCH_BASE, AD_SERVER, AD_USER
from ..logging_config import get_logger
from .auth import LDAPBindAuth
from .base import create_table, db, insert_raw

log = get_logger("ingest.ad")

COMPUTER_ATTRS = [
    "sAMAccountName",
    "dNSHostName",
    "operatingSystem",
    "operatingSystemVersion",
    "distinguishedName",
    "lastLogonTimestamp",
    "whenCreated",
    "whenChanged",
]


def _safe_attr(attrs: dict[str, list[str]], key: str, idx: int = 0, default: str = "") -> str:
    """Safely extract a value from an LDAP-style multi-valued attribute dict."""
    vals = attrs.get(key, [])
    return str(vals[idx]) if len(vals) > idx else default


def _transform_ad(attrs_list: list[dict[str, list[str]]], dns: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform raw AD LDAP attribute dicts into cleaned records.

    Args:
        attrs_list: Each element is ``entry.entry_attributes_as_dict``
            (a dict of str -> list of str).
        dns: Parallel list of distinguishedName strings.

    Returns (records, columns). Pure function — no I/O.
    """
    records = []
    for attrs, dn in zip(attrs_list, dns, strict=True):
        rec = {
            "sam_account_name": _safe_attr(attrs, "sAMAccountName"),
            "dns_host_name": _safe_attr(attrs, "dNSHostName"),
            "operating_system": _safe_attr(attrs, "operatingSystem"),
            "os_version": _safe_attr(attrs, "operatingSystemVersion"),
            "distinguished_name": dn,
            "description": _safe_attr(attrs, "description"),
            "location": _safe_attr(attrs, "location"),
            "created": _safe_attr(attrs, "whenCreated"),
            "last_logon": _safe_attr(attrs, "lastLogonTimestamp"),
            "when_changed": _safe_attr(attrs, "whenChanged"),
            "user_account_control": _safe_attr(attrs, "userAccountControl"),
            "managed_by": _safe_attr(attrs, "managedBy"),
            "raw_json": json.dumps({k: _safe_attr(attrs, k) for k in COMPUTER_ATTRS}),
        }
        records.append(rec)
    columns = [
        "sam_account_name",
        "dns_host_name",
        "operating_system",
        "os_version",
        "distinguished_name",
        "description",
        "location",
        "created",
        "last_logon",
        "when_changed",
        "user_account_control",
        "managed_by",
    ]
    return records, columns


def ingest() -> int:
    conn = db("ad")
    total = 0

    auth = LDAPBindAuth(AD_SERVER, AD_USER, AD_PASSWORD)
    ldap_conn = auth.bind()
    if not ldap_conn:
        log.error({"event": "auth_failed", "source": "ad"})
        conn.close()
        return 0

    try:
        ldap_conn.search(
            search_base=AD_SEARCH_BASE,
            search_filter="(objectClass=computer)",
            attributes=COMPUTER_ATTRS,
            size_limit=5000,
        )

        attrs_list = [entry.entry_attributes_as_dict for entry in ldap_conn.entries]
        dns = [str(entry.entry_dn) for entry in ldap_conn.entries]
        if attrs_list:
            records, columns = _transform_ad(attrs_list, dns)
            with conn:
                create_table(conn, "computers", columns)
                n = insert_raw(conn, "computers", records)
            log.info({"event": "inserted", "source": "ad", "table": "computers", "rows": n})
            total += n
    finally:
        conn.close()

    return total
