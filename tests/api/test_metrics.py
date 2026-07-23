"""Unit tests for src/zentinull/api/metrics.py."""

from __future__ import annotations

from zentinull.api.metrics import Metrics


def test_metrics_counter_and_histogram():
    m = Metrics()

    # Counter test
    m.requests_total.labels(method="GET", endpoint="/health", status="200").inc()
    m.requests_total.labels(method="GET", endpoint="/health", status="200").inc(2)

    output = m.generate()
    assert "zentinull_requests_total" in output
    assert 'method="GET",endpoint="/health",status="200"} 3' in output

    # Histogram test
    m.request_duration_seconds.labels(method="POST", endpoint="/pipeline/run").observe(0.5)
    output_hist = m.generate()
    assert "zentinull_request_duration_seconds_bucket" in output_hist
    assert 'le="1.0"}' in output_hist
