"""Comprehensive behavioral tests for all 12 API router endpoints.

Uses mock MeshDB to test routing, parameter parsing, error handling,
and response shape without needing a real DuckDB file.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


class TestDeviceLookup:
    """GET /device/{query} — single-device lookup."""

    def test_hit_returns_device_story(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """A found device returns 200 with the full story dict."""
        mock_meshdb.lookup.return_value = MagicMock(
            model_dump=lambda: {
                "query": "ws28",
                "cluster_id": "c1",
                "device_name": "ws28",
                "source_count": 2,
                "sources": ["me", "zbx"],
                "record_count": 2,
                "consolidated": {"name": ["ws28"]},
                "records": [],
            }
        )
        resp = client_with_db.get("/device/ws28")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cluster_id"] == "c1"
        assert body["device_name"] == "ws28"
        assert body["source_count"] == 2

    def test_miss_returns_404(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """A missing device returns 404."""
        mock_meshdb.lookup.return_value = None
        resp = client_with_db.get("/device/ghost")
        assert resp.status_code == 404
        assert "ghost" in resp.text

    def test_miss_on_db_unavailable(self, client: TestClient) -> None:
        """When app.state.db is None, return 503."""
        resp = client.get("/device/test")
        assert resp.status_code == 503


class TestBatchLookup:
    """POST /batch — multi-device batch resolution."""

    def test_all_hits(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """All queries resolve successfully."""
        mock_meshdb.batch_lookup.return_value = [
            {"cluster_id": "c1", "query": "ws28"},
            {"cluster_id": "c2", "query": "dc01"},
        ]
        resp = client_with_db.post("/batch", json=["ws28", "dc01"])
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["cluster_id"] == "c1"

    def test_some_misses(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Partial misses return None for unfound queries."""
        mock_meshdb.batch_lookup.return_value = [
            {"cluster_id": "c1", "query": "ws28"},
            None,
        ]
        resp = client_with_db.post("/batch", json=["ws28", "ghost"])
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["cluster_id"] == "c1"
        assert body[1] is None

    def test_empty_array(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Empty array returns empty array."""
        mock_meshdb.batch_lookup.return_value = []
        resp = client_with_db.post("/batch", json=[])
        assert resp.status_code == 200
        assert resp.json() == []

    def test_non_array_body_returns_400(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """A non-array JSON body returns 400."""
        resp = client_with_db.post("/batch", json="not_an_array")
        assert resp.status_code == 400

    def test_db_unavailable_returns_503(self, client: TestClient) -> None:
        """When app.state.db is None, return 503."""
        resp = client.post("/batch", json=["test"])
        assert resp.status_code == 503


class TestSearch:
    """GET /search — full-text and field-scoped search."""

    def test_search_by_name(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Search with q and field=device_name returns matching clusters."""
        mock_meshdb.search.return_value = [
            MagicMock(
                model_dump=lambda: {"cluster_id": "c1", "device_name": "ws28", "source_count": 1, "sources": ["me"]}
            )
        ]
        resp = client_with_db.get("/search?q=ws28&field=device_name")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["cluster_id"] == "c1"

    def test_search_fulltext(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Search without field performs full-text search."""
        mock_meshdb.search.return_value = [
            MagicMock(model_dump=lambda: {"cluster_id": "c1", "device_name": "ws28"}),
            MagicMock(model_dump=lambda: {"cluster_id": "c2", "device_name": "ws29"}),
        ]
        resp = client_with_db.get("/search?q=ws")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_search_no_results(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """No matches returns empty list."""
        mock_meshdb.search.return_value = []
        resp = client_with_db.get("/search?q=zzz_nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_enforces_limit(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """The limit query param (max 100) is passed to db.search()."""
        mock_meshdb.search.return_value = []
        client_with_db.get("/search?q=test&limit=50")
        mock_meshdb.search.assert_called_once_with("test", field="", limit=50)

    def test_search_rejects_limit_over_100(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """limit > 100 is rejected by FastAPI validation."""
        resp = client_with_db.get("/search?q=test&limit=200")
        assert resp.status_code == 422

    def test_search_empty_q_returns_422(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """q parameter is required and must have min_length=1."""
        resp = client_with_db.get("/search?q=")
        assert resp.status_code == 422

    def test_search_db_unavailable_returns_503(self, client: TestClient) -> None:
        """When app.state.db is None, return 503."""
        resp = client.get("/search?q=test")
        assert resp.status_code == 503


class TestDashboard:
    """GET /dashboard — KPI summary."""

    def test_dashboard_returns_stats(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Dashboard returns populated stats dict."""
        mock_meshdb.dashboard.return_value = {
            "clusters": 150,
            "records": 1200,
            "multi_source": 45,
            "singletons": 105,
            "sources": {"me": 400, "zbx": 300, "fg": 500},
            "coverage": {"me": "95%", "zbx": "88%"},
            "top_clusters": [],
        }
        resp = client_with_db.get("/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["clusters"] == 150
        assert body["records"] == 1200

    def test_dashboard_returns_503_when_no_db(self, client: TestClient) -> None:
        """When app.state.db is None, return 503."""
        resp = client.get("/dashboard")
        assert resp.status_code == 503
        assert "Mesh database not loaded" in resp.text


class TestMesh:
    """GET /mesh — cross-source cluster statistics."""

    def test_mesh_stats(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Mesh endpoint returns stats dict."""
        mock_meshdb.mesh_stats.return_value = {
            "total_clusters": 150,
            "total_records": 1200,
            "singletons": 105,
            "multi_source": 45,
            "by_source_count": {"1": 105, "2": 30, "3": 15},
            "by_source_combo": {"me,zbx": 20, "me,fg,zbx": 15},
            "records_per_source": {"me": 400, "zbx": 300, "fg": 500},
        }
        resp = client_with_db.get("/mesh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_clusters"] == 150
        assert body["multi_source"] == 45


class TestListClusters:
    """GET /clusters — paginated cluster list with filters."""

    def test_default_pagination(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Default params return first page."""
        mock_meshdb.list_clusters.return_value = (
            1,
            [MagicMock(model_dump=lambda: {"cluster_id": "c1", "device_name": "ws28", "source_count": 1})],
        )
        resp = client_with_db.get("/clusters")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["offset"] == 0
        assert len(body["items"]) == 1

    def test_min_sources_filter(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """min_sources and source params forwarded to db."""
        mock_meshdb.list_clusters.return_value = (0, [])
        client_with_db.get("/clusters?min_sources=2&source=zbx")
        mock_meshdb.list_clusters.assert_called_once_with(2, "zbx", 50, 0)

    def test_limit_capped_at_200(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """limit > 200 returns 422."""
        resp = client_with_db.get("/clusters?limit=300")
        assert resp.status_code == 422

    def test_pagination_offset(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """offset and limit forwarded correctly."""
        mock_meshdb.list_clusters.return_value = (0, [])
        client_with_db.get("/clusters?offset=20&limit=30")
        mock_meshdb.list_clusters.assert_called_once_with(1, "", 30, 20)


class TestAnomalies:
    """GET /anomalies — singletons, unnamed, no-serial devices."""

    def test_anomalies_report(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Anomalies endpoint returns report dict."""
        mock_meshdb.anomalies.return_value = {
            "singletons": 10,
            "singleton_list": [],
            "no_name": 2,
            "no_name_list": [],
            "no_serial": 5,
            "no_serial_list": [],
        }
        resp = client_with_db.get("/anomalies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["singletons"] == 10
        assert body["no_name"] == 2
        assert body["no_serial"] == 5


class TestDeviceMetrics:
    """GET /device/{query}/metrics — time-series metrics."""

    @pytest.fixture
    def _setup_mock(self, mock_meshdb: MagicMock) -> MagicMock:
        """Shared mock setup for device metrics tests."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_metrics.return_value = [
            {"metric_name": "cpu_pct", "value": 42.5, "source": "zbx"},
            {"metric_name": "memory_pct", "value": 67.0, "source": "zbx"},
        ]
        mock_meshdb.device_metric_names.return_value = ["cpu_pct", "memory_pct"]
        return mock_meshdb

    def test_metrics_returned(self, client_with_db: TestClient, _setup_mock: MagicMock) -> None:
        """Returns metrics, names, and count."""
        resp = client_with_db.get("/device/ws28/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "ws28"
        assert body["cluster_id"] == "c1"
        assert body["count"] == 2
        assert body["metric_names"] == ["cpu_pct", "memory_pct"]
        assert len(body["metrics"]) == 2

    def test_metrics_empty(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Device with no metrics returns empty list."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_metrics.return_value = []
        mock_meshdb.device_metric_names.return_value = []
        resp = client_with_db.get("/device/ws28/metrics")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_metrics_with_filters(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """metric and hours query params are forwarded."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_metrics.return_value = []
        mock_meshdb.device_metric_names.return_value = []
        client_with_db.get("/device/ws28/metrics?metric=cpu_pct&hours=168&limit=10")
        mock_meshdb.device_metrics.assert_called_once_with("c1", metric="cpu_pct", source="", hours=168, limit=10)

    def test_metrics_miss_returns_404(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Unknown device returns 404 before querying metrics."""
        mock_meshdb.lookup.return_value = None
        resp = client_with_db.get("/device/ghost/metrics")
        assert resp.status_code == 404


class TestDeviceTimeline:
    """GET /device/{query}/timeline — recent events."""

    def test_timeline_returns_events(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Returns timeline events."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_timeline.return_value = [
            {"event_type": "cpu_alert", "severity": "warning"},
            {"event_type": "disk_full", "severity": "critical"},
        ]
        resp = client_with_db.get("/device/ws28/timeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "ws28"
        assert body["cluster_id"] == "c1"
        assert body["count"] == 2
        assert len(body["events"]) == 2

    def test_timeline_empty(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Device with no events returns empty list."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_timeline.return_value = []
        resp = client_with_db.get("/device/ws28/timeline")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_timeline_hours_param(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """hours and limit params forwarded to db."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_timeline.return_value = []
        client_with_db.get("/device/ws28/timeline?hours=1&limit=5")
        mock_meshdb.device_timeline.assert_called_once_with("c1", hours=1, limit=5)

    def test_timeline_miss_returns_404(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Unknown device returns 404."""
        mock_meshdb.lookup.return_value = None
        resp = client_with_db.get("/device/ghost/timeline")
        assert resp.status_code == 404


class TestDeviceStats:
    """GET /device/{query}/stats — latest metric values + event severity counts."""

    def test_stats_returned(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Returns stats plus 24h metric summary."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_stats.return_value = {
            "latest_metrics": [{"metric_name": "cpu_pct", "value": 42.0}],
            "event_counts": {"warning": 3, "critical": 1},
        }
        mock_meshdb.device_metric_summary.return_value = {
            "cpu_pct": {"avg": 45.0, "max": 90.0, "min": 10.0, "latest": 42.0, "count": 10}
        }
        resp = client_with_db.get("/device/ws28/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "ws28"
        assert body["cluster_id"] == "c1"
        assert body["stats"]["event_counts"]["critical"] == 1
        assert body["metric_summary_24h"]["cpu_pct"]["avg"] == 45.0

    def test_stats_miss_returns_404(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Unknown device returns 404."""
        mock_meshdb.lookup.return_value = None
        resp = client_with_db.get("/device/ghost/stats")
        assert resp.status_code == 404


class TestDeviceMetricSummary:
    """GET /device/{query}/metric-summary — aggregated metric stats."""

    def test_metric_summary(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Returns aggregated metrics."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_metric_summary.return_value = {
            "cpu_pct": {"avg": 45.0, "max": 90.0, "min": 10.0, "latest": 42.0, "count": 10},
            "memory_pct": {"avg": 55.0, "max": 85.0, "min": 20.0, "latest": 55.0, "count": 10},
        }
        resp = client_with_db.get("/device/ws28/metric-summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "ws28"
        assert body["cluster_id"] == "c1"
        assert body["hours"] == 24
        assert "cpu_pct" in body["metrics"]

    def test_metric_summary_with_hours(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """hours param forwarded to db."""
        story_mock = MagicMock()
        story_mock.cluster_id = "c1"
        mock_meshdb.lookup.return_value = story_mock
        mock_meshdb.device_metric_summary.return_value = {}
        client_with_db.get("/device/ws28/metric-summary?hours=168")
        mock_meshdb.device_metric_summary.assert_called_once_with("c1", hours=168)

    def test_metric_summary_miss_returns_404(self, client_with_db: TestClient, mock_meshdb: MagicMock) -> None:
        """Unknown device returns 404."""
        mock_meshdb.lookup.return_value = None
        resp = client_with_db.get("/device/ghost/metric-summary")
        assert resp.status_code == 404


class TestHealth:
    """GET /health — health check."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        """Returns 200 with status and dependency checks, even when DB is unavailable."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["mesh_db"] == "unavailable"
        assert body["mesh_file"] == "missing"


class TestDeviceView:
    """GET /device-view — HTML device dashboard page."""

    def test_returns_html(self, client: TestClient) -> None:
        """Returns HTML response with default query param."""
        resp = client.get("/device-view")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_custom_query_param_accepted(self, client: TestClient) -> None:
        """The _q query param is accepted by the endpoint (for OpenAPI docs)."""
        resp = client.get("/device-view?_q=dc01")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
