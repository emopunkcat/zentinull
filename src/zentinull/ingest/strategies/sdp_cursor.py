"""Fetch strategy for SDP cursor-paginated endpoints.

Uses input_data/list_info pagination with has_more_rows cursor tracking.
Supports dotted response_path for nested wrapper extraction.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from ...logging_config import get_logger
from ..strategies import register

log = get_logger("strategies.sdp_cursor")

SDP_ACCEPT = "application/vnd.manageengine.sdp.v3+json"
MAX_START_INDEX = 10_000_000


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


@register("sdp_cursor")
def sdp_cursor_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch all rows via SDP cursor pagination (input_data/list_info).

    Args:
        endpoint: dict with keys:
            - url: full URL (base + path)
            - response_path: dotted path to extract items from wrapper (required)
            - pagination: optional dict with row_count (default 100),
              sort_field, sort_order (default "asc")
        auth: object with .get_headers() -> dict[str, str] method

    Returns:
        List of raw record dicts, or [] on error.
    """
    url = endpoint.get("url", "")
    response_path = endpoint.get("response_path")
    pagination = endpoint.get("pagination", {})

    headers: dict[str, str] = {}
    if hasattr(auth, "get_headers"):
        headers = auth.get_headers()
    headers["Accept"] = SDP_ACCEPT

    page_size = pagination.get("row_count", 100)
    sort_field = pagination.get("sort_field")
    sort_order = pagination.get("sort_order", "asc")

    start_index = 1
    all_items: list[dict[str, Any]] = []
    page = 0

    try:
        while True:
            page += 1
            list_info: dict[str, Any] = {
                "row_count": page_size,
                "start_index": start_index,
            }
            if sort_field:
                list_info["sort_field"] = sort_field
                list_info["sort_order"] = sort_order

            params = {"input_data": json.dumps({"list_info": list_info})}
            log.info(
                {
                    "event": "fetching",
                    "url": url,
                    "page": page,
                    "start_index": start_index,
                }
            )

            r = requests.get(url, headers=headers, params=params, timeout=(15, 60))
            r.raise_for_status()
            data = r.json()

            if not isinstance(data, dict):
                break

            items = _drill_path(data, response_path) if response_path else data
            if items is None:
                items = []
            elif not isinstance(items, list):
                items = [items]

            all_items.extend(items)

            li = data.get("list_info", {}) or {}
            if not li.get("has_more_rows"):
                break

            # Safety: server returned less than full page
            if isinstance(items, list) and len(items) < page_size:
                break

            start_index += page_size
            if start_index > MAX_START_INDEX:
                log.warning(
                    {
                        "event": "sdp_pagination_aborted",
                        "url": url,
                        "reason": "cursor_exceeded_cap",
                    }
                )
                break

    except Exception as e:
        log.error(
            {
                "event": "get_failed",
                "url": url,
                "response_path": response_path,
                "error": str(e),
            }
        )
        return []

    log.info(
        {
            "event": "sdp_fetch_complete",
            "url": url,
            "pages": page,
            "items": len(all_items),
        }
    )
    return all_items
