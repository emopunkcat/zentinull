"""Transform registry — maps transform names to normalizer functions.

Transforms are applied during field extraction to normalize raw values
for Splink matching (e.g., MAC address normalization, serial number cleanup).
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable

from ..normalizer import normalize_mac, normalize_name, normalize_os_family, normalize_serial

#: Matches the employee folder segment in SharePoint agreement webUrls.
#: e.g. ``/Agreements/Rick%20Ahmed__10/foo.pdf`` → captures "Rick Ahmed"
_RE_EMP_URL = re.compile(r"/Agreements/([^/]+)__\d+/")


def _employee_name_from_url(web_url: str) -> str:
    """Parse a SharePoint agreement ``webUrl`` and return the employee name.

    The n8n webhook at ``/webhook/sp_employeedocs`` returns records with a
    ``webUrl`` shaped like ``/Agreements/Rick%20Ahmed__10/...pdf`` — the
    employee's full name (URL-encoded) followed by ``__<sp_employee_id>`` is
    the folder name. Returns the URL-decoded name (e.g. ``"Rick Ahmed"``)
    or empty string if the URL doesn't match the expected pattern.
    """
    if not web_url:
        return ""
    m = _RE_EMP_URL.search(web_url)
    if not m:
        return ""
    return urllib.parse.unquote(m.group(1)).strip()


#: Registry of transform functions.
#: Keys are transform names used in FieldSpec.transform.
#: Values are callables: (raw_value: str) -> str
REGISTRY: dict[str, Callable[[str], str]] = {
    "mac": normalize_mac,
    "serial": normalize_serial,
    "name": normalize_name,
    "lower": str.lower,
    "os_family": normalize_os_family,
    "first_of_list": lambda s: s.split(",")[0].strip() if s else "",
    "join_list": lambda s: ", ".join(x.strip() for x in s.split(",") if x.strip()),
    "employee_name_from_url": _employee_name_from_url,
}
