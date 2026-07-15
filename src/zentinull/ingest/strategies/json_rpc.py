"""JSON-RPC fetch strategy.

Extracted from Zabbix _zbx_call pattern. Calls a JSON-RPC endpoint with
an auth token, returns a list of dict records.
"""

from __future__ import annotations

from typing import Any

import requests

from ...logging_config import get_logger
from ..strategies import register

log = get_logger("strategies.json_rpc")


@register("json_rpc")
def json_rpc_fetch(endpoint: dict[str, Any], auth: object) -> list[dict[str, Any]]:
    """Fetch data via JSON-RPC call.

    endpoint keys:
        - url: JSON-RPC endpoint URL (required, from config ZBX_URL)
        - method: JSON-RPC method name (required)
        - params: JSON-RPC params dict (required)
        - timeout: (connect, read) tuple, default (10, 90)
        - result_wrapper: optional key to extract list from result dict

    auth: object with .get_headers() that returns
          {"Authorization": "Bearer <token>"}
    """
    url: str = endpoint["url"]
    method: str = endpoint["method"]
    params: dict[str, Any] = endpoint["params"]
    timeout: tuple[int, int] = endpoint.get("timeout", (10, 90))
    result_wrapper: str | None = endpoint.get("result_wrapper")

    # Extract raw token from auth headers — APIKeyAuth produces
    # {"Authorization": "Bearer <token>"}
    headers: dict[str, str] = {}
    if hasattr(auth, "get_headers"):
        headers = auth.get_headers()
    auth_header: str = headers.get("Authorization", "")
    token: str = auth_header.split(" ", 1)[-1] if " " in auth_header else ""

    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "auth": token,
        "id": 1,
    }

    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        resp: dict[str, Any] = r.json()

        if "error" in resp:
            log.error(
                {
                    "event": "api_error",
                    "source": "json_rpc",
                    "method": method,
                    "message": str(resp["error"]),
                }
            )
            return []

        result: Any = resp.get("result")

        if result_wrapper and isinstance(result, dict):
            result = result.get(result_wrapper)

        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    except Exception:
        log.exception({"event": "fetch_failed", "source": "json_rpc", "method": method})
        return []
