"""Fetch strategy registry.

Each strategy is a callable with signature:

    (endpoint: dict, auth: object) -> list[dict[str, Any]]

It fetches raw records from a single endpoint and returns them as a list of
dicts. Transform is handled downstream by the manifest walker — strategies
are pure fetch, no field mapping.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Registry mapping System.strategy → fetch function.
#: Populated as strategy modules are imported.
REGISTRY: dict[str, Callable[..., list[dict[str, Any]]]] = {}


def register(name: str) -> Callable[..., Any]:
    """Decorator to register a fetch strategy."""

    def wrapper(fn: Callable[..., list[dict[str, Any]]]) -> Callable[..., list[dict[str, Any]]]:
        REGISTRY[name] = fn
        return fn

    return wrapper


def _load_strategies() -> None:
    """Import all strategy modules so their @register decorators fire."""
    from . import json_rpc, ldap, paged_json, rest_json, sdp_cursor  # noqa: F401


_load_strategies()
