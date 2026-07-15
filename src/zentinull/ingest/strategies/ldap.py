"""LDAP fetch strategy.

Extracted from AD ingest pattern. Binds to an LDAP server, runs a search,
and returns raw attribute dicts with DN.
"""

from __future__ import annotations

from typing import Any

from ...logging_config import get_logger
from ..strategies import register

log = get_logger("strategies.ldap")


@register("ldap")
def ldap_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch records via LDAP search.

    endpoint keys:
        - search_base: LDAP search base DN (required)
        - search_filter: LDAP filter string (required, e.g. "(objectClass=computer)")
        - attributes: list of attribute names to request (required)
        - size_limit: max results, default 5000

    auth: LDAPBindAuth instance with .bind() -> ldap3.Connection | None
    """
    search_base: str = endpoint["search_base"]
    search_filter: str = endpoint["search_filter"]
    attributes: list[str] = endpoint["attributes"]
    size_limit: int = endpoint.get("size_limit", 5000)

    try:
        if not hasattr(auth, "bind"):
            log.error({"event": "auth_failed", "source": "ldap"})
            return []
        ldap_conn = auth.bind()
        if ldap_conn is None:
            log.error({"event": "auth_failed", "source": "ldap"})
            return []

        ldap_conn.search(
            search_base=search_base,
            search_filter=search_filter,
            attributes=attributes,
            size_limit=size_limit,
        )

        result: list[dict[str, Any]] = [
            {"dn": str(e.entry_dn), **e.entry_attributes_as_dict} for e in ldap_conn.entries
        ]
        return result

    except Exception:
        log.exception({"event": "fetch_failed", "source": "ldap"})
        return []
