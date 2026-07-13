"""Tests for api.models — Pydantic serialization round-trips."""

from __future__ import annotations


def test_source_record_roundtrip():
    from zentinull.api.models import SourceRecord

    rec = SourceRecord(source="sp", name="ws28", serial_number="SN001")
    d = rec.model_dump()
    assert d["source"] == "sp"
    assert d["name"] == "ws28"
    assert d["serial_number"] == "SN001"


def test_source_record_defaults():
    from zentinull.api.models import SourceRecord

    rec = SourceRecord(source="fg")
    assert rec.source_id == ""


def test_source_record_extra_attributes():
    from zentinull.api.models import SourceRecord

    rec = SourceRecord(source="sp", extra_attributes={"ssid": "corp-wifi", "vlan": "100"})
    assert rec.extra_attributes == {"ssid": "corp-wifi", "vlan": "100"}
    # Default is empty dict
    rec2 = SourceRecord(source="fg")
    assert rec2.extra_attributes == {}


def test_cluster_info_roundtrip():
    from zentinull.api.models import ClusterInfo

    c = ClusterInfo(cluster_id="c1", source_count=3, sources=["sp", "me", "fg"])
    d = c.model_dump()
    assert d["cluster_id"] == "c1"
    assert len(d["sources"]) == 3


def test_device_story_roundtrip():
    from zentinull.api.models import DeviceStory, SourceRecord

    rec = SourceRecord(source="sp", name="ws28")
    story = DeviceStory(query="ws28", cluster_id="c1", records=[rec])
    d = story.model_dump()
    assert d["query"] == "ws28"
    assert len(d["records"]) == 1


def test_mesh_stats_defaults():
    from zentinull.api.models import MeshStats

    s = MeshStats()
    assert s.total_clusters == 0
    assert s.total_records == 0


def test_dashboard_stats_defaults():
    from zentinull.api.models import DashboardStats

    d = DashboardStats()
    assert d.clusters == 0


def test_anomalies_report_defaults():
    from zentinull.api.models import AnomaliesReport

    a = AnomaliesReport()
    assert a.singletons == 0
    assert a.no_name == 0
