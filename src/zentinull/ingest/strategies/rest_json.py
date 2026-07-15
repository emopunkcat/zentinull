"""Fetch strategy for plain REST JSON endpoints.

Handles GET requests with auth headers, optional dotted-path extraction from
response wrappers, and single-object dict wrapping.
"""

from __future__ import annotations

from typing import Any

import requests

from ...logging_config import get_logger
from ..strategies import register

log = get_logger("strategies.rest_json")


def _drill_path(data: dict[str, Any], path: str) -> Any:
    """Drill into a nested dict by a dotted key path."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            log.warning({"event": "path_drill_failed", "path": path, "part": part, "type": type(current).__name__})
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


@register("rest_json")
def rest_json_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch raw records from a REST JSON endpoint.

    Args:
        endpoint: dict with keys:
            - url: full URL to GET
            - response_path: optional dotted path to extract results from wrapper dict
        auth: object with .get_headers() -> dict[str, str] method

    Returns:
        List of raw record dicts, or [] on error.
    """
    url = endpoint["url"]
    response_path = endpoint.get("response_path")
    headers = auth.get_headers() if hasattr(auth, "get_headers") else {}

    try:
        r = requests.get(url, headers=headers, verify=False, timeout=(10, 30))
        r.raise_for_status()
        data = r.json()

        if response_path:
            data = _drill_path(data, response_path)
            if data is None:
                log.warning({"event": "empty_path", "url": url, "response_path": response_path})
                return []

        if isinstance(data, dict):
            return [data]

        if isinstance(data, list):
            return data

        log.warning({"event": "unexpected_type", "url": url, "type": type(data).__name__})
        return []

    except Exception as e:
        log.error({"event": "get_failed", "url": url, "response_path": response_path, "error": str(e)})
        return []
