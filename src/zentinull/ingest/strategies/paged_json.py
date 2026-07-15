"""Fetch strategy for paginated JSON endpoints.

Handles two pagination modes:
- "page_param": increment ``?page=N`` and extract via dotted response_path
  (ME EC API pattern).
- "paging.next": follow ``body["paging"]["next"]`` URLs, auto-detect wrapper
  keys for item extraction (ME MDM API pattern).
"""

from __future__ import annotations

from typing import Any

import requests

from ...logging_config import get_logger
from ..strategies import register

log = get_logger("strategies.paged_json")


def _fetch_page_param(
    url: str,
    headers: dict[str, str],
    response_path: str | None,
) -> list[dict[str, Any]]:
    """Paginate by incrementing ``?page=N``."""
    all_items: list[dict[str, Any]] = []
    page = 1
    while True:
        page_url = f"{url}?page={page}"
        log.info({"event": "fetching", "url": page_url, "page": page})
        try:
            r = requests.get(page_url, headers=headers, timeout=(15, 60))
        except requests.RequestException:
            log.exception({"event": "request_failed", "url": page_url, "page": page})
            return all_items  # return what we have so far

        if r.status_code == 204 or not r.text.strip():
            break
        r.raise_for_status()
        data = r.json()

        items: Any = data
        if response_path:
            for part in response_path.split("."):
                if isinstance(items, dict):
                    items = items.get(part, [])
                else:
                    items = []
                    break

        if not items:
            break

        all_items.extend(items)
        page += 1

    log.info({"event": "fetch_complete", "pagination": "page_param", "pages": page - 1, "items": len(all_items)})
    return all_items


def _fetch_paging_next(
    url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Paginate by following ``body["paging"]["next"]`` URLs."""
    all_items: list[dict[str, Any]] = []
    next_url: str | None = url
    page = 0

    while next_url:
        page += 1
        log.info({"event": "fetching", "url": next_url, "page": page})
        try:
            r = requests.get(next_url, headers=headers, timeout=(15, 90))
        except requests.RequestException:
            log.exception({"event": "request_failed", "url": next_url, "page": page})
            return all_items  # return what we have so far

        r.raise_for_status()
        body = r.json()

        # Extract item list — handle list body or dict with known wrapper keys
        devices: list[Any] = []
        if isinstance(body, list):
            devices = body
        else:
            for key in ("devices", "response", "data", "results", "items"):
                val = body.get(key)
                if isinstance(val, list):
                    devices = val
                    break

        if not devices:
            break

        all_items.extend(devices)

        # Follow paging.next for subsequent pages
        if isinstance(body, dict):
            paging = body.get("paging", {})
            next_url = paging.get("next") if isinstance(paging, dict) else None
        else:
            next_url = None

    log.info({"event": "fetch_complete", "pagination": "paging.next", "pages": page, "items": len(all_items)})
    return all_items


@register("paged_json")
def paged_json_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch all pages from a paginated JSON endpoint.

    Args:
        endpoint: dict with keys:
            - url: full URL of the first page.
            - response_path: dotted path to extract items from wrapper dict
              (required for ``page_param`` mode).
            - pagination: ``"page_param"`` (increment ?page=N) or
              ``"paging.next"`` (follow body["paging"]["next"] URLs).
              Defaults to ``"page_param"``.
        auth: object with ``.get_headers() -> dict[str, str]`` method.

    Returns:
        List of raw record dicts, or [] on error.
    """
    url: str = endpoint["url"]
    pagination: str = endpoint.get("pagination", "page_param")
    response_path: str | None = endpoint.get("response_path")

    headers = {"Accept": "application/json"}
    try:
        if hasattr(auth, "get_headers"):
            headers.update(auth.get_headers())
    except Exception:
        log.exception({"event": "auth_headers_failed", "url": url})
        return []

    try:
        if pagination == "paging.next":
            return _fetch_paging_next(url, headers)
        return _fetch_page_param(url, headers, response_path)
    except Exception:
        log.exception({"event": "paged_json_fetch_failed", "url": url, "pagination": pagination})
        return []
