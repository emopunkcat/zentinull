"""Zentinull API — models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceRecord(BaseModel):
    """A single record from one source system within a cluster."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str = ""
    name: str = ""
    name_clean: str = ""
    serial_number: str = ""
    mac_address: str = ""
    os_version: str = ""
    asset_tag: str = ""
    mac_clean: str = ""
    manufacturer: str = ""
    model: str = ""
    os: str = ""
    assigned_user: str = ""
    ip_address: str = ""
    imei: str = ""
    extra_attributes: dict[str, Any] = Field(default_factory=dict)


class ClusterInfo(BaseModel):
    """Summary of a cluster — one per unique device."""

    cluster_id: str
    device_name: str = ""
    source_count: int = 0
    sources: list[str] = Field(default_factory=list)
    serial_number: str = ""
    mac_address: str = ""
    manufacturer: str = ""
    os_version: str = ""
    asset_tag: str = ""
    model: str = ""
    os: str = ""
    assigned_user: str = ""
    ip_address: str = ""
    imei: str = ""
    record_count: int = 0


class DeviceStory(BaseModel):
    """Full device story — consolidated view + per-source records."""

    query: str
    cluster_id: str
    device_name: str = ""
    source_count: int = 0
    sources: list[str] = Field(default_factory=list)
    record_count: int = 0
    consolidated: dict[str, list[str]] = Field(default_factory=dict)
    records: list[SourceRecord] = Field(default_factory=list)


class MeshStats(BaseModel):
    """Cross-source cluster statistics."""

    total_clusters: int = 0
    total_records: int = 0
    singletons: int = 0
    multi_source: int = 0
    by_source_count: dict[str, int] = Field(default_factory=dict)
    by_source_combo: dict[str, int] = Field(default_factory=dict)
    records_per_source: dict[str, int] = Field(default_factory=dict)


class DashboardStats(BaseModel):
    """Quick dashboard view."""

    clusters: int = 0
    records: int = 0
    multi_source: int = 0
    singletons: int = 0
    sources: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, str] = Field(default_factory=dict)
    top_clusters: list[ClusterInfo] = Field(default_factory=list)


class AnomaliesReport(BaseModel):
    """Singletons, unnamed, and no-serial device clusters."""

    singletons: int = 0
    singleton_list: list[ClusterInfo] = Field(default_factory=list)
    no_name: int = 0
    no_name_list: list[ClusterInfo] = Field(default_factory=list)
    no_serial: int = 0
    no_serial_list: list[ClusterInfo] = Field(default_factory=list)


class MetricRecord(BaseModel):
    """A single metrics data point from a source system."""

    model_config = ConfigDict(frozen=True)

    cluster_id: str = ""
    source: str = ""
    metric_name: str = ""
    value: float | None = None
    text_value: str = ""
    tags: list[str] = Field(default_factory=list)
    recorded_at: str = ""
    ingested_at: str = ""


class EventRecord(BaseModel):
    """A single event from a source system."""

    model_config = ConfigDict(frozen=True)

    cluster_id: str = ""
    source: str = ""
    event_type: str = ""
    detail: str = ""
    severity: str = "info"
    recorded_at: str = ""
    ingested_at: str = ""
