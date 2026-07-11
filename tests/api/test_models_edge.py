"""Edge-case tests for api.models — defaults, empty collections, populated round-trips."""

from __future__ import annotations


def test_source_record_all_empty_defaults():
    from zentinull.api.models import SourceRecord

    rec = SourceRecord(source="x")
    assert rec.source == "x"
    assert rec.source_id == ""
    assert rec.name == ""
    assert rec.serial_number == ""
    assert rec.mac_address == ""
    assert rec.mac_clean == ""
    assert rec.manufacturer == ""
    assert rec.model == ""
    assert rec.os == ""
    assert rec.assigned_user == ""
    assert rec.ip_address == ""
    assert rec.imei == ""


def test_source_record_with_raw_none():
    from zentinull.api.models import SourceRecordWithRaw

    rec = SourceRecordWithRaw(source="sp", raw_json=None)
    assert rec.raw_json is None


def test_source_record_with_raw_dict():
    from zentinull.api.models import SourceRecordWithRaw

    rec = SourceRecordWithRaw(source="sp", raw_json={"a": 1})
    assert rec.raw_json == {"a": 1}
    d = rec.model_dump()
    assert d["raw_json"] == {"a": 1}


def test_cluster_info_sources_with_items():
    from zentinull.api.models import ClusterInfo

    c = ClusterInfo(cluster_id="c1", sources=["sp", "me"])
    assert c.sources == ["sp", "me"]
    d = c.model_dump()
    assert d["sources"] == ["sp", "me"]


def test_cluster_info_record_count_default_zero():
    from zentinull.api.models import ClusterInfo

    c = ClusterInfo(cluster_id="c1")
    assert c.record_count == 0


def test_device_story_consolidated_empty():
    from zentinull.api.models import DeviceStory

    story = DeviceStory(query="q", cluster_id="c1")
    assert story.consolidated == {}


def test_device_story_records_empty():
    from zentinull.api.models import DeviceStory

    story = DeviceStory(query="q", cluster_id="c1")
    assert story.records == []


def test_anomalies_report_with_populated_lists():
    from zentinull.api.models import AnomaliesReport, ClusterInfo

    c = ClusterInfo(cluster_id="c1")
    a = AnomaliesReport(singletons=5, singleton_list=[c], no_name_list=[c])
    d = a.model_dump()
    assert d["singletons"] == 5
    assert len(d["singleton_list"]) == 1
    assert len(d["no_name_list"]) == 1
    assert d["singleton_list"][0]["cluster_id"] == "c1"


def test_mesh_stats_by_source_count_roundtrip():
    from zentinull.api.models import MeshStats

    s = MeshStats(by_source_count={"sp": 10, "me": 5})
    d = s.model_dump()
    assert d["by_source_count"] == {"sp": 10, "me": 5}
