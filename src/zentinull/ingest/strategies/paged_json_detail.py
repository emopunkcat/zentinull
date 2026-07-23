"""Fetch strategy: paginated list + per-record detail enrichment.

Two-phase fetch:
1. Paginated list (reuses paged_json pagination logic)
2. Per-record detail call, merging response keys into each list item
3. Optional secondary detail call (e.g. MDM locations)

The detail response keys flow into raw_json → extra_attributes → v_extra.
No spec or model changes needed — auto-surfaced by Valentine.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from ...logging_config import get_logger
from ..strategies import register
from .paged_json import _fetch_page_param, _fetch_paging_next

log = get_logger("strategies.paged_json_detail")


@register("paged_json_detail")
def paged_json_detail_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch paginated list, then enrich each record with a detail call.

    Endpoint keys (in addition to paged_json's):
        detail_url_template: template with {id} and {base} placeholders
        detail_id_field: field name in list items containing the ID (default: resource_id)
        detail_delay: seconds between detail calls (default: 0.2)
        detail_max: optional max detail calls per run (rate limiting)
        secondary_detail_url_template: optional second detail call (e.g. MDM GPS)
        secondary_response_key: key to extract records from secondary response (default: locations)
        secondary_fields: {target_field: source_field} mapping from secondary response
    """
    url = endpoint["url"]
    pagination = endpoint.get("pagination", "page_param")
    response_path = endpoint.get("response_path")
    detail_template = endpoint.get("detail_url_template", "")
    detail_id_field = endpoint.get("detail_id_field", "resource_id")
    detail_delay = float(endpoint.get("detail_delay", 0.2))
    detail_max = endpoint.get("detail_max")
    secondary_template = endpoint.get("secondary_detail_url_template", "")
    secondary_response_key = endpoint.get("secondary_response_key", "locations")
    secondary_fields: dict[str, str] = endpoint.get("secondary_fields", {})
    resolved_base = endpoint.get("resolved_base", "")

    headers = {"Accept": "application/json"}
    try:
        if hasattr(auth, "get_headers"):
            headers.update(auth.get_headers())
    except Exception:
        log.exception({"event": "auth_headers_failed"})
        return []

    # Phase 1: list fetch (reuse paged_json pagination)
    try:
        if pagination == "paging.next":
            items = _fetch_paging_next(url, headers)
        else:
            items = _fetch_page_param(url, headers, response_path)
    except Exception:
        log.exception({"event": "list_fetch_failed", "url": url})
        return []

    if not items or not detail_template:
        return items

    # Phase 2: per-record detail enrichment
    enriched: list[dict[str, Any]] = []
    detail_count = 0
    for item in items:
        record_id = item.get(detail_id_field)
        if not record_id:
            enriched.append(item)
            continue

        detail_url = detail_template.format(id=record_id, base=resolved_base)
        try:
            r = requests.get(detail_url, headers=headers, timeout=(10, 60))
            if r.status_code == 200:
                detail = r.json()
                if isinstance(detail, dict):
                    # Flatten top-level + known nested sub-objects
                    for sub_key in ("", "computer_system", "hardware", "system_info", "base_board", "motherboard"):
                        source = detail if sub_key == "" else detail.get(sub_key)
                        if isinstance(source, dict):
                            for k, v in source.items():
                                if not isinstance(v, (dict, list)):
                                    lk = k.lower()
                                    if lk not in item:
                                        item[lk] = v
                    detail_count += 1
                    time.sleep(detail_delay)
        except (requests.RequestException, ValueError):
            log.debug({"event": "detail_fetch_failed", "id": record_id})

        if detail_max and detail_count >= detail_max:
            log.info({"event": "detail_max_reached", "count": detail_count})
            enriched.append(item)
            continue

        # Phase 3: optional secondary detail (e.g. MDM GPS)
        if secondary_template and record_id:
            sec_url = secondary_template.format(id=record_id, base=resolved_base)
            try:
                sr = requests.get(sec_url, headers=headers, timeout=(10, 30))
                if sr.status_code == 200:
                    sec_data = sr.json()
                    records_list = sec_data.get(secondary_response_key, [])
                    if records_list and isinstance(records_list, list):
                        latest = max(records_list, key=lambda x: int(x.get("added_time", 0))) if records_list else {}
                        for target_field, source_field in secondary_fields.items():
                            if source_field in latest:
                                item[target_field] = str(latest[source_field])
                    time.sleep(detail_delay)
            except (requests.RequestException, ValueError, TypeError):
                pass

        enriched.append(item)

    log.info({"event": "detail_enriched", "total": len(enriched), "details_fetched": detail_count})
    return enriched
