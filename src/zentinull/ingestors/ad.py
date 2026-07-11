"""
AD ingest: LDAP query for computers.
Uses LDAPBindAuth from auth.py.
"""

from __future__ import annotations

import json
import os

from ..logging_config import get_logger
from .auth import LDAPBindAuth
from .base import create_table, db, insert_raw

log = get_logger("ingest.ad")

AD_SERVER = os.environ.get("AD_SERVER", "ldap://192.168.20.11:389")
AD_USER = os.environ.get("AD_USER", "MOONLITE\\jejo")
AD_PASSWORD = os.environ.get("AD_PASSWORD", "")
SEARCH_BASE = os.environ.get("AD_SEARCH_BASE", "DC=moonlite,DC=local")
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
            search_base=SEARCH_BASE,
            search_filter="(objectClass=computer)",
            attributes=COMPUTER_ATTRS,
            size_limit=5000,
        )

        records = []
        for entry in ldap_conn.entries:
            attrs = entry.entry_attributes_as_dict

            def _safe(key: str, idx: int = 0, default: str = "") -> str:
                vals = attrs.get(key, [])  # noqa: B023
                return str(vals[idx]) if len(vals) > idx else default

            rec = {
                "sam_account_name": _safe("sAMAccountName"),
                "dns_host_name": _safe("dNSHostName"),
                "operating_system": _safe("operatingSystem"),
                "os_version": _safe("operatingSystemVersion"),
                "distinguished_name": str(entry.entry_dn),
                "description": _safe("description"),
                "location": _safe("location"),
                "created": _safe("whenCreated"),
                "last_logon": _safe("lastLogonTimestamp"),
                "when_changed": _safe("whenChanged"),
                "user_account_control": _safe("userAccountControl"),
                "managed_by": _safe("managedBy"),
                "raw_json": json.dumps({k: _safe(k) for k in COMPUTER_ATTRS}),
            }
            records.append(rec)

        create_table(
            conn,
            "computers",
            [
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
            ],
        )
        n = insert_raw(conn, "computers", records)
        log.info({"event": "inserted", "source": "ad", "table": "computers", "rows": n})
        total += n
    finally:
        conn.close()

    return total
