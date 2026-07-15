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
    source_count_dist: dict[str, int] = Field(default_factory=dict)
    source_combos: dict[str, int] = Field(default_factory=dict)


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


class DeviceMetricsResponse(BaseModel):
    """Metrics response for a device — typed with MetricRecord."""

    query: str
    cluster_id: str
    metric_names: list[str] = Field(default_factory=list)
    count: int = 0
    metrics: list[MetricRecord] = Field(default_factory=list)


class DeviceTimelineResponse(BaseModel):
    """Timeline response for a device — typed with EventRecord."""

    query: str
    cluster_id: str
    hours: int = 168
    count: int = 0
    events: list[EventRecord] = Field(default_factory=list)


class AttachmentRecord(BaseModel):
    """A linked attachment record (ticket, metric context, note)."""

    feed_key: str = ""
    source_id: str = ""
    field: str = ""
    value: str = ""
    confidence: float = 0.5
    payload: dict[str, Any] = Field(default_factory=dict)
    linked_at: str = ""


class DeviceAttachmentsResponse(BaseModel):
    """GET /device/{query}/attachments — linked attachment records."""

    cluster_id: str = ""
    attachments: list[AttachmentRecord] = Field(default_factory=list)


class ClusterListResponse(BaseModel):
    """Paginated cluster listing — GET /clusters."""

    total: int = 0
    offset: int = 0
    items: list[ClusterInfo] = Field(default_factory=list)


class MetricLatest(BaseModel):
    """Latest observed value for a single metric name."""

    value: float | None = None
    text: str | None = None
    source: str = ""
    recorded_at: str = ""


class MetricAggregate(BaseModel):
    """avg/max/min/latest/count summary for a single metric name over a window."""

    count: int = 0
    avg: float | None = None
    max: float | None = None
    min: float | None = None
    latest: float | None = None


class DeviceStatsBlock(BaseModel):
    """Current-state block: latest per-metric values + event severity counts."""

    metrics: dict[str, MetricLatest] = Field(default_factory=dict)
    event_counts: dict[str, int] = Field(default_factory=dict)


class DeviceStatsResponse(BaseModel):
    """GET /device/{query}/stats — current state + 24h metric summary."""

    query: str
    cluster_id: str
    stats: DeviceStatsBlock = Field(default_factory=DeviceStatsBlock)
    metric_summary_24h: dict[str, MetricAggregate] = Field(default_factory=dict)


class DeviceMetricSummaryResponse(BaseModel):
    """GET /device/{query}/metric-summary — aggregated metric stats over a window."""

    query: str
    cluster_id: str
    hours: int = 24
    metrics: dict[str, MetricAggregate] = Field(default_factory=dict)
