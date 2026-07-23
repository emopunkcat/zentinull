"""Manifest type system — typed frozen dataclasses for declarative pipeline config.

The manifest is data. Every system, feed, field mapping, and resolution profile
is expressed as a frozen dataclass instance, validated at load time, and consumed
by the engine without hardcoding source names or field paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(Enum):
    """Feed role in entity resolution.

    ANCHOR: merge candidates (Splink entity resolution)
    ATTACHMENT: link to anchors (never merge, e.g. zbx items → device)
    CONTEXT: stored but not resolved (e.g. sp_employees, fg_policies)
    """

    ANCHOR = "anchor"
    ATTACHMENT = "attachment"
    CONTEXT = "context"


@dataclass(frozen=True)
class Auth:
    """Authentication config for a system.

    kind: "api_key" | "oauth_refresh" | "ldap" | "none"
    options: mapping of env-var NAMES (never values) consumed by the strategy.
    """

    kind: str
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class System:
    """External system (SharePoint, ManageEngine, etc.).

    auth: authentication config
    strategy: fetch strategy id (registered in ingest/strategies/)
    label: display label (dashboard)
    schedule: default sync interval seconds (worker)
    options: strategy-specific config (base URLs, timeouts, etc.)
    coverage: probability this system reports a given device (0.0-1.0)
    fields: list of field names this system reliably provides
    """

    auth: Auth
    strategy: str
    label: str = ""
    schedule: int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    coverage: float = 0.5
    fields: tuple[str, ...] = ("name",)


@dataclass(frozen=True)
class FieldSpec:
    """Field extraction spec.

    paths: ordered fallback of dotted raw paths; first non-empty wins
    transform: key into transforms.REGISTRY (e.g. "mac", "serial", "name")
    """

    paths: tuple[str, ...]
    transform: str | None = None


@dataclass(frozen=True)
class Link:
    """Attachment link spec.

    field: dotted raw path in the attachment record
    to: target profile name
    on: anchor field to join against
    strategy: "exact" | "normalized" | "extract_fuzzy"
    transform: normalizer for strategy="normalized"
    multi: if True, one attachment row per distinct resolved cluster
    scope: anchor feed keys to join against. () => exact/normalized: anchor feeds
           of the SAME system; extract_fuzzy: all anchor feeds of target profile.
    """

    field: str
    to: str
    on: str
    strategy: str = "exact"
    transform: str | None = None
    multi: bool = False
    scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feed:
    """Data feed from a system.

    system: parent system key
    endpoint: strategy-specific endpoint config (path, method, pagination, etc.)
    role: ANCHOR / ATTACHMENT / CONTEXT
    store: table name in data/<project>/<system>.sqlite
    id_path: dotted path of the record's stable source id (required for ANCHOR/ATTACHMENT)
    updated_path: dotted path of remote-modified timestamp, if any (for incremental sync)
    profile: resolution profile name (ANCHOR feeds only)
    spec: mapping of target field name → FieldSpec
    links: attachment link specs (ATTACHMENT feeds only)
    """

    system: str
    endpoint: Mapping[str, Any]
    role: Role
    store: str
    id_path: str
    updated_path: str | None = None
    profile: str | None = None
    spec: Mapping[str, FieldSpec] = field(default_factory=dict)
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class Comparison:
    """Splink comparison spec.

    kind: "exact" | "levenshtein" | "jaro_winkler"
    column: target field name
    thresholds: comparison thresholds (translator maps to per-kind param name)
    term_frequency_adjustments: if True, applied via .configure() method chaining
    """

    kind: str
    column: str
    thresholds: tuple[float, ...] = ()
    term_frequency_adjustments: bool = False


@dataclass(frozen=True)
class ResolutionProfile:
    """Entity resolution profile (Splink config).

    name: profile identifier
    fields: anchor fields to include in Splink CSV
    derived: mapping of derived field name → (source field, transform)
    comparisons: Splink comparison specs
    blocking: blocking rule column names
    deterministic: deterministic rule column names
    em_passes: EM training pass column names
    predict_threshold: match weight threshold for prediction
    cluster_threshold: match weight threshold for clustering
    sweep_thresholds: thresholds to sweep during clustering report
    u_max_pairs: max pairs for u-probability estimation
    lambda_recall: lambda recall parameter for EM training
    """

    name: str
    fields: tuple[str, ...]
    derived: Mapping[str, tuple[str, str]]
    comparisons: tuple[Comparison, ...]
    blocking: tuple[str, ...]
    deterministic: tuple[str, ...]
    em_passes: tuple[str, ...]
    predict_threshold: float
    cluster_threshold: float
    sweep_thresholds: tuple[float, ...] = ()
    u_max_pairs: int | None = None
    lambda_recall: float = 0.5  # MUST equal config.SPLINK_LAMBDA_RECALL default
    sot: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    # per-field (primary_source, secondary_source).
    # e.g. {"name": ("sp", ""), "serial_number": ("me", "sp")}


@dataclass(frozen=True)
class Manifest:
    """Top-level manifest.

    project: project identifier
    systems: mapping of system key → System
    feeds: mapping of feed key → Feed
    profiles: mapping of profile name → ResolutionProfile
    """

    project: str
    systems: Mapping[str, System]
    feeds: Mapping[str, Feed]
    profiles: Mapping[str, ResolutionProfile]
