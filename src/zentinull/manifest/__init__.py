"""Manifest loader and validation.

Loads the manifest for the current project (ZENTINULL_PROJECT env var, default "default"),
validates all cross-references, and returns a typed Manifest instance.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from typing import Any

from ..logging_config import get_logger
from .transforms import REGISTRY as TRANSFORM_REGISTRY
from .types import Feed, FieldSpec, Manifest, Role

__all__ = [
    "Feed",
    "FieldSpec",
    "Manifest",
    "ManifestValidationError",
    "Role",
    "get_anchor_feeds",
    "get_feed_keys",
    "get_system_feeds",
    "load_manifest",
]

log = get_logger("manifest")


class ManifestValidationError(Exception):
    """Raised when manifest validation fails."""


def _normalize_spec(spec: Mapping[str, Any]) -> dict[str, FieldSpec]:
    """Normalize tuple specs to FieldSpec objects.

    Accepts:
    - FieldSpec objects (pass through)
    - Tuples: (path1, path2, ...) for fallback paths
    - Tuples: (path, transform_name) for single path with transform
    """
    normalized = {}
    for field_name, spec_value in spec.items():
        if isinstance(spec_value, FieldSpec):
            normalized[field_name] = spec_value
        elif isinstance(spec_value, tuple):
            # Check if it's (path, transform) format
            if len(spec_value) == 2 and isinstance(spec_value[1], str) and spec_value[1] in TRANSFORM_REGISTRY:
                normalized[field_name] = FieldSpec(paths=(spec_value[0],), transform=spec_value[1])
            else:
                # Multiple fallback paths
                normalized[field_name] = FieldSpec(paths=spec_value)
        else:
            raise TypeError(f"Invalid spec value for {field_name}: {type(spec_value)}")
    return normalized


def _normalize_feed(feed: Feed) -> Feed:
    """Normalize a feed's spec to FieldSpec objects."""
    if not feed.spec:
        return feed

    normalized_spec = _normalize_spec(feed.spec)
    return Feed(
        system=feed.system,
        endpoint=feed.endpoint,
        role=feed.role,
        profile=feed.profile,
        store=feed.store,
        id_path=feed.id_path,
        updated_path=feed.updated_path,
        spec=normalized_spec,
        links=feed.links,
    )


def load_manifest(project: str | None = None) -> Manifest:
    """Load and validate the manifest for the given project.

    Args:
        project: Project name. If None, reads ZENTINULL_PROJECT env var (default "default").

    Returns:
        Validated Manifest instance.

    Raises:
        ManifestValidationError: If validation fails.
        ImportError: If manifest module not found.
    """
    project_name = project or os.environ.get("ZENTINULL_PROJECT", "default")
    module_name = f"projects.{project_name}.manifest"

    # `projects/` lives at the repo root, which is not necessarily on sys.path
    # when invoked as a subprocess script (only the installed `zentinull`
    # package is). Ensure the root is importable before resolving the manifest.
    from ..config import ROOT

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Manifest module not found: {module_name}") from e

    # Manifest module must expose a MANIFEST constant
    if not hasattr(module, "MANIFEST"):
        raise AttributeError(f"Manifest module {module_name} must define MANIFEST constant")

    manifest: Manifest = module.MANIFEST

    # Normalize tuple specs to FieldSpec objects
    normalized_feeds = {key: _normalize_feed(feed) for key, feed in manifest.feeds.items()}
    manifest = Manifest(
        project=manifest.project,
        systems=manifest.systems,
        feeds=normalized_feeds,
        profiles=manifest.profiles,
    )

    # Validate
    _validate(manifest)

    log.info(
        {
            "event": "manifest_loaded",
            "project": project_name,
            "systems": len(manifest.systems),
            "feeds": len(manifest.feeds),
        }
    )
    return manifest


def _validate(manifest: Manifest) -> None:
    """Validate all cross-references in the manifest.

    Raises ManifestValidationError on first error found.
    """
    errors: list[str] = []

    # 1. Every Feed.system ∈ systems
    for feed_key, feed in manifest.feeds.items():
        if feed.system not in manifest.systems:
            errors.append(f"Feed '{feed_key}' references unknown system '{feed.system}'")

    # 2. Feed.profile (when ANCHOR) ∈ profiles
    for feed_key, feed in manifest.feeds.items():
        if feed.role == Role.ANCHOR:
            if not feed.profile:
                errors.append(f"ANCHOR feed '{feed_key}' must have a profile")
            elif feed.profile not in manifest.profiles:
                errors.append(f"Feed '{feed_key}' references unknown profile '{feed.profile}'")

    # 3. Link.to ∈ profiles
    for feed_key, feed in manifest.feeds.items():
        for link in feed.links:
            if link.to not in manifest.profiles:
                errors.append(f"Feed '{feed_key}' link references unknown profile '{link.to}'")

    # 4. spec keys ⊆ profile.fields (for ANCHOR feeds)
    for feed_key, feed in manifest.feeds.items():
        if feed.role == Role.ANCHOR and feed.profile:
            # Skip if profile doesn't exist (already caught in step 2)
            if feed.profile not in manifest.profiles:
                continue
            profile = manifest.profiles[feed.profile]
            profile_fields = set(profile.fields) | set(profile.derived)
            for spec_key in feed.spec:
                if spec_key not in profile_fields:
                    errors.append(f"Feed '{feed_key}' spec key '{spec_key}' not in profile '{feed.profile}'")

    # 5. Link.on ∈ target profile fields ∪ derived
    for feed_key, feed in manifest.feeds.items():
        for link in feed.links:
            if link.to in manifest.profiles:
                target_profile = manifest.profiles[link.to]
                target_fields = set(target_profile.fields) | set(target_profile.derived)
                if link.on not in target_fields:
                    errors.append(f"Feed '{feed_key}' link.on '{link.on}' not in target profile '{link.to}'")

    # 6. Link.scope entries are existing ANCHOR feed keys of the target profile
    for feed_key, feed in manifest.feeds.items():
        for link in feed.links:
            if link.scope:
                if link.to not in manifest.profiles:
                    continue  # already caught above
                target_profile = manifest.profiles[link.to]
                # Find all ANCHOR feeds for this profile
                anchor_feeds = {
                    fk for fk, f in manifest.feeds.items() if f.role == Role.ANCHOR and f.profile == target_profile.name
                }
                for scope_key in link.scope:
                    if scope_key not in anchor_feeds:
                        errors.append(
                            f"Feed '{feed_key}' link.scope '{scope_key}' is not an ANCHOR feed of profile '{link.to}'"
                        )

    # 7. every System.strategy ∈ strategy registry
    from ..ingest.strategies import REGISTRY as STRATEGY_REGISTRY

    for sys_key, system in manifest.systems.items():
        if system.strategy not in STRATEGY_REGISTRY:
            errors.append(f"System '{sys_key}' strategy '{system.strategy}' not in strategy registry")

    # 8. every transform ∈ transforms.REGISTRY
    for feed_key, feed in manifest.feeds.items():
        for spec_key, spec in feed.spec.items():
            if spec.transform and spec.transform not in TRANSFORM_REGISTRY:
                errors.append(f"Feed '{feed_key}' spec '{spec_key}' references unknown transform '{spec.transform}'")
        for link in feed.links:
            if link.transform and link.transform not in TRANSFORM_REGISTRY:
                errors.append(f"Feed '{feed_key}' link references unknown transform '{link.transform}'")

    # 9. every ANCHOR/ATTACHMENT feed has non-empty id_path
    for feed_key, feed in manifest.feeds.items():
        if feed.role in (Role.ANCHOR, Role.ATTACHMENT) and not feed.id_path:
            errors.append(f"Feed '{feed_key}' (role={feed.role.value}) must have non-empty id_path")

    # 10. profile blocking/deterministic/em_passes columns ∈ fields ∪ derived
    for profile_name, profile in manifest.profiles.items():
        profile_fields = set(profile.fields) | set(profile.derived)
        for col in profile.blocking:
            if col not in profile_fields:
                errors.append(f"Profile '{profile_name}' blocking column '{col}' not in fields")
        for col in profile.deterministic:
            if col not in profile_fields:
                errors.append(f"Profile '{profile_name}' deterministic column '{col}' not in fields")
        for col in profile.em_passes:
            if col not in profile_fields:
                errors.append(f"Profile '{profile_name}' em_passes column '{col}' not in fields")

    if errors:
        raise ManifestValidationError("Manifest validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def get_feed_keys(manifest: Manifest, role: Role | None = None) -> list[str]:
    """Get feed keys, optionally filtered by role."""
    if role is None:
        return list(manifest.feeds.keys())
    return [k for k, v in manifest.feeds.items() if v.role == role]


def get_anchor_feeds(manifest: Manifest, profile: str | None = None) -> list[str]:
    """Get ANCHOR feed keys, optionally filtered by profile."""
    feeds = [k for k, v in manifest.feeds.items() if v.role == Role.ANCHOR]
    if profile:
        feeds = [k for k in feeds if manifest.feeds[k].profile == profile]
    return feeds


def get_system_feeds(manifest: Manifest, system: str) -> list[str]:
    """Get feed keys for a given system."""
    return [k for k, v in manifest.feeds.items() if v.system == system]
