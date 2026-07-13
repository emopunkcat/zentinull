"""Lightweight Prometheus-format metrics for the Zentinull API.

Usage:
    from .metrics import metrics

    # On each request:
    metrics.requests_total.labels(method="GET", endpoint="/health").inc()

    # The server exposes /metrics which calls metrics.generate()
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class _LabeledCounter:
    """A counter with fixed label values."""

    def __init__(self, parent: _Counter, label_values: tuple[str, ...]) -> None:
        self._parent = parent
        self._label_values = label_values

    def inc(self, amount: int = 1) -> None:
        with self._parent._lock:
            self._parent._values[self._label_values] += amount


class _LabeledHistogram:
    """A histogram with fixed label values."""

    def __init__(self, parent: _Histogram, label_values: tuple[str, ...]) -> None:
        self._parent = parent
        self._label_values = label_values

    def observe(self, value: float) -> None:
        with self._parent._lock:
            buckets = self._parent._values[self._label_values]
            for b in self._parent._buckets:
                if value <= b:
                    buckets[str(b)] += 1
            buckets["+Inf"] = buckets.get("+Inf", 0) + 1
            buckets["_sum"] = buckets.get("_sum", 0) + value
            buckets["_count"] = buckets.get("_count", 0) + 1


class _Counter:
    """A Prometheus-style counter."""

    def __init__(self, name: str, help_text: str, labels: list[str]) -> None:
        self._name = name
        self._help = help_text
        self._labels = labels
        self._values: dict[tuple[str, ...], int] = defaultdict(int)
        self._lock = Lock()

    def labels(self, **kwargs: str) -> _LabeledCounter:
        label_values = tuple(kwargs.get(k, "") for k in self._labels)
        return _LabeledCounter(self, label_values)

    def generate(self, label_values: tuple[str, ...]) -> str:
        labels_str = ",".join(f'{k}="{v}"' for k, v in zip(self._labels, label_values, strict=True))
        return f"{self._name}{{{labels_str}}} {self._values[label_values]}"


class _Histogram:
    """A Prometheus-style histogram with predefined buckets."""

    def __init__(self, name: str, help_text: str, labels: list[str], buckets: list[float]) -> None:
        self._name = name
        self._help = help_text
        self._labels = labels
        self._buckets = sorted(buckets)
        self._values: dict[tuple[str, ...], dict[str, int | float]] = defaultdict(
            lambda: {str(b): 0.0 for b in self._buckets}
        )
        self._lock = Lock()

    def labels(self, **kwargs: str) -> _LabeledHistogram:
        label_values = tuple(kwargs.get(k, "") for k in self._labels)
        return _LabeledHistogram(self, label_values)


class Metrics:
    """Simple Prometheus-format metrics collector.

    Usage:
        metrics = Metrics()
        c = metrics.requests_total.labels(method="GET", endpoint="/health")
        c.inc()
    """

    def __init__(self) -> None:
        self.requests_total = _Counter(
            "zentinull_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status"],
        )
        self.request_duration_seconds = _Histogram(
            "zentinull_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint"],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )
        self.db_errors_total = _Counter(
            "zentinull_db_errors_total",
            "Total database errors",
            ["operation"],
        )
        self.pipeline_runs_total = _Counter(
            "zentinull_pipeline_runs_total",
            "Total pipeline stage runs",
            ["stage", "status"],
        )

    def generate(self) -> str:
        """Generate Prometheus-format text output."""
        lines: list[str] = [
            "# HELP zentinull_requests_total Total HTTP requests",
            "# TYPE zentinull_requests_total counter",
        ]
        with self.requests_total._lock:
            for label_values, count in sorted(self.requests_total._values.items()):
                labels_str = ",".join(
                    f'{k}="{v}"' for k, v in zip(self.requests_total._labels, label_values, strict=True)
                )
                lines.append(f"zentinull_requests_total{{{labels_str}}} {count}")

        lines.extend(
            [
                "# HELP zentinull_request_duration_seconds HTTP request duration in seconds",
                "# TYPE zentinull_request_duration_seconds histogram",
            ]
        )
        with self.request_duration_seconds._lock:
            for label_values, buckets in sorted(self.request_duration_seconds._values.items()):
                labels_str = ",".join(
                    f'{k}="{v}"' for k, v in zip(self.request_duration_seconds._labels, label_values, strict=True)
                )
                for bucket, val in sorted(buckets.items()):
                    if bucket.startswith("_"):
                        continue
                    lines.append(f'zentinull_request_duration_seconds_bucket{{{labels_str},le="{bucket}"}} {int(val)}')

        lines.extend(
            [
                "# HELP zentinull_db_errors_total Total database errors",
                "# TYPE zentinull_db_errors_total counter",
            ]
        )
        with self.db_errors_total._lock:
            for label_values, count in sorted(self.db_errors_total._values.items()):
                labels_str = ",".join(
                    f'{k}="{v}"' for k, v in zip(self.db_errors_total._labels, label_values, strict=True)
                )
                lines.append(f"zentinull_db_errors_total{{{labels_str}}} {count}")

        lines.extend(
            [
                "# HELP zentinull_pipeline_runs_total Total pipeline stage runs",
                "# TYPE zentinull_pipeline_runs_total counter",
            ]
        )
        with self.pipeline_runs_total._lock:
            for label_values, count in sorted(self.pipeline_runs_total._values.items()):
                labels_str = ",".join(
                    f'{k}="{v}"' for k, v in zip(self.pipeline_runs_total._labels, label_values, strict=True)
                )
                lines.append(f"zentinull_pipeline_runs_total{{{labels_str}}} {count}")

        lines.append("")
        return "\n".join(lines)


# Global singleton
metrics = Metrics()
