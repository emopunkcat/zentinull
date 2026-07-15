"""DuckDB query layer — typed, async-safe, single connection per request."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import duckdb

from ..logging_config import get_logger
from .models import ClusterInfo, DeviceStory, SourceRecord

log = get_logger("api.db")


def _safe(val: object, default: str = "") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s and s.lower() != "nan" else default


def _norm_mac(raw: str) -> str:
    h = re.sub(r"[^a-fA-F0-9]", "", raw).lower()
    return h if len(h) == 12 else ""


class MeshDB:
    """DuckDB-backed device mesh — all queries are SQL, all lookups indexed."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    def ping(self) -> bool:
        """Validate database connectivity."""
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(str(self._path), read_only=True)
        # Enable faster aggregate queries
        conn.execute("SET threads = 4")
        return conn

    # ── Lookups ──────────────────────────────────────────────────────────

    def batch_lookup(self, queries: list[str]) -> list[dict[str, Any] | None]:
        """Resolve multiple queries in a single DuckDB connection."""
        results: list[dict[str, Any] | None] = []
        conn = self._conn()
        try:
            for q in queries:
                try:
                    cid = self._resolve_cluster(conn, q)
                    if cid is None:
                        results.append(None)
                    else:
                        story = self._build_story(conn, cid, q)
                        results.append(story.model_dump())
                except Exception as e:
                    log.error({"event": "batch_lookup_error", "query": q, "error": str(e)})
                    results.append(None)
            return results
        finally:
            conn.close()

    def lookup(self, query: str) -> DeviceStory | None:
        """Find a device by any identifier. Returns full story or None."""
        q = query.strip()
        conn = self._conn()

        try:
            # Try each lookup strategy in order
            cluster_id = self._resolve_cluster(conn, q)
            if cluster_id is None:
                log.info({"event": "lookup_miss", "query": q})
                return None

            log.info({"event": "lookup_hit", "query": q, "cluster_id": cluster_id})
            return self._build_story(conn, cluster_id, q)
        finally:
            conn.close()

    @staticmethod
    def _first_col(row: Any | None) -> str | None:
        """Extract first column from a fetchone result, or None."""
        return cast(str, row[0]) if row else None

    def _resolve_cluster(self, conn: duckdb.DuckDBPyConnection, q: str) -> str | None:
        """Find the best-matching cluster_id for a query string."""
        ql = q.lower()

        # 1. Exact cluster_id
        row = conn.execute("SELECT cluster_id FROM devices WHERE cluster_id = ?", [ql]).fetchone()
        if row:
            return self._first_col(row)

        # 2. Exact device_name
        row = conn.execute(
            "SELECT cluster_id FROM devices WHERE lower(device_name) = ? ORDER BY source_count DESC LIMIT 1", [ql]
        ).fetchone()
        if row:
            return self._first_col(row)

        # 3. Exact serial (case-insensitive)
        row = conn.execute(
            "SELECT cluster_id FROM devices WHERE lower(serial_number) = ? ORDER BY source_count DESC LIMIT 1", [ql]
        ).fetchone()
        if row:
            return self._first_col(row)

        # 4. MAC (normalized)
        mac = _norm_mac(q)
        if mac:
            row = conn.execute(
                "SELECT cluster_id FROM devices WHERE mac_address = ? ORDER BY source_count DESC LIMIT 1", [mac]
            ).fetchone()
            if row:
                return self._first_col(row)
            # Also check source_records for MAC
            row = conn.execute(
                "SELECT cluster_id FROM source_records WHERE mac_clean = ? ORDER BY 1 LIMIT 1", [mac]
            ).fetchone()
            if row:
                return self._first_col(row)

        # 5. Exact IP
        row = conn.execute(
            "SELECT cluster_id FROM source_records WHERE ip_address LIKE ? ORDER BY 1 LIMIT 1", [f"%{q}%"]
        ).fetchone()
        if row:
            return self._first_col(row)

        # 6. User substring
        row = conn.execute(
            "SELECT cluster_id FROM devices WHERE lower(assigned_user) LIKE ? ORDER BY source_count DESC LIMIT 1",
            [f"%{ql}%"],
        ).fetchone()
        if row:
            return self._first_col(row)

        # 7. Full-text fallback across source_records
        row = conn.execute(
            """
            SELECT sr.cluster_id FROM source_records sr
            JOIN devices d ON d.cluster_id = sr.cluster_id
            WHERE lower(sr.name_clean) LIKE ?
               OR lower(sr.name) LIKE ?
               OR lower(sr.serial_number) LIKE ?
               OR lower(sr.manufacturer) LIKE ?
               OR lower(sr.model) LIKE ?
               OR lower(sr.os) LIKE ?
               OR lower(sr.assigned_user) LIKE ?
               OR lower(sr.ip_address) LIKE ?
               OR lower(sr.mac_address) LIKE ?
            ORDER BY d.source_count DESC
            LIMIT 1
        """,
            [f"%{ql}%"] * 9,
        ).fetchone()
        if row:
            return self._first_col(row)

        log.info({"event": "resolve_miss", "query": q})
        return None

    def _build_story(self, conn: duckdb.DuckDBPyConnection, cluster_id: str, query: str) -> DeviceStory:
        """Build a full DeviceStory from cluster_id."""
        # Device row
        dev = conn.execute("SELECT * FROM devices WHERE cluster_id = ?", [cluster_id]).fetchone()
        if dev is None:
            raise ValueError(f"Cluster {cluster_id} not found")
        cols = [d[0] for d in conn.description]
        dev_dict = dict(zip(cols, dev, strict=True))

        # Source records
        rows = conn.execute(
            "SELECT * FROM source_records WHERE cluster_id = ? ORDER BY source", [cluster_id]
        ).fetchall()
        sr_cols = [d[0] for d in conn.description]

        sources = sorted({r[sr_cols.index("source")] for r in rows})
        recs: list[SourceRecord] = []
        consolidated: dict[str, list[str]] = {}

        for r in rows:
            rd = dict(zip(sr_cols, r, strict=True))
            extra_raw = _safe(rd.get("extra_attributes"))
            extra_attrs: dict[str, Any] = {}
            if extra_raw:
                try:
                    parsed = json.loads(extra_raw)
                    if isinstance(parsed, dict):
                        extra_attrs = cast(dict[str, Any], parsed)
                except (json.JSONDecodeError, TypeError):
                    pass
            recs.append(
                SourceRecord(
                    source=_safe(rd.get("source")),
                    source_id=_safe(rd.get("source_id")),
                    name=_safe(rd.get("name")),
                    name_clean=_safe(rd.get("name_clean")),
                    serial_number=_safe(rd.get("serial_number")),
                    mac_address=_safe(rd.get("mac_address")),
                    mac_clean=_safe(rd.get("mac_clean")),
                    manufacturer=_safe(rd.get("manufacturer")),
                    model=_safe(rd.get("model")),
                    os_version=_safe(rd.get("os_version")),
                    asset_tag=_safe(rd.get("asset_tag")),
                    os=_safe(rd.get("os")),
                    assigned_user=_safe(rd.get("assigned_user")),
                    ip_address=_safe(rd.get("ip_address")),
                    imei=_safe(rd.get("imei")),
                    extra_attributes=extra_attrs,
                )
            )
        for field in [
            "serial_number",
            "mac_address",
            "manufacturer",
            "model",
            "os",
            "os_version",
            "asset_tag",
            "assigned_user",
            "ip_address",
            "imei",
        ]:
            val = _safe(dev_dict.get(field, ""))
            if val:
                consolidated[field] = [val]

        # Add name separately
        name = _safe(dev_dict.get("device_name", ""))
        if name:
            consolidated["name_clean"] = [name]
        for field in [
            "serial_number",
            "mac_clean",
            "manufacturer",
            "model",
            "os",
            "os_version",
            "asset_tag",
            "assigned_user",
            "ip_address",
        ]:
            # Merge in per-source values that differ (for display)
            if field == "mac_clean":
                field_src = "mac_clean"
                field_key = "mac_address"
            else:
                field_src = field
                field_key = field
            vals = []
            for r in rows:
                rd = dict(zip(sr_cols, r, strict=True))
                v = _safe(rd.get(field_src, ""))
                if v and v not in vals:
                    vals.append(v)
            existing = consolidated.get(field_key, [])
            for v in vals:
                if v not in existing:
                    existing.append(v)
            if existing:
                consolidated[field_key] = existing

        return DeviceStory(
            query=query,
            cluster_id=cluster_id,
            device_name=name,
            source_count=len(sources),
            sources=sources,
            record_count=len(rows),
            consolidated=consolidated,
            records=recs,
        )

    # ── Search ───────────────────────────────────────────────────────────

    def search(self, q: str, field: str = "", limit: int = 20) -> list[ClusterInfo]:
        """Search devices by any field or full-text.

        ``field`` is interpolated into SQL, so it MUST be validated against the
        whitelist below — an unknown value falls back to full-text search rather
        than reaching the query as raw text.
        """
        ql = f"%{q.lower()}%"
        searchable = {
            "device_name",
            "serial_number",
            "mac_clean",
            "ip_address",
            "assigned_user",
            "manufacturer",
            "model",
            "os",
            "os_version",
            "asset_tag",
            "imei",
        }
        if field == "name":
            field = "device_name"
        if field and field not in searchable:
            field = ""
        conn = self._conn()
        try:
            if field == "mac_clean":
                mac = _norm_mac(q)
                rows = conn.execute("SELECT * FROM devices WHERE mac_address = ? LIMIT ?", [mac, limit]).fetchall()
            elif field:
                rows = conn.execute(
                    f"SELECT * FROM devices WHERE lower({field}) LIKE ? ORDER BY source_count DESC LIMIT ?",
                    [ql, limit],
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT d.* FROM devices d
                    WHERE lower(d.device_name) LIKE ?
                       OR lower(d.serial_number) LIKE ?
                       OR lower(d.mac_address) LIKE ?
                       OR lower(d.manufacturer) LIKE ?
                       OR lower(d.model) LIKE ?
                       OR lower(d.os) LIKE ?
                       OR lower(d.os_version) LIKE ?
                       OR lower(d.assigned_user) LIKE ?
                       OR lower(d.ip_address) LIKE ?
                       OR lower(d.asset_tag) LIKE ?
                    ORDER BY d.source_count DESC
                    LIMIT ?
                """,
                    [ql, ql, ql, ql, ql, ql, ql, ql, ql, ql, limit],
                ).fetchall()

            cols = [d[0] for d in conn.description]
            return [self._row_to_cluster_info(dict(zip(cols, r, strict=True))) for r in rows]
        except Exception:
            log.error({"event": "search_error", "query": q, "field": field})
            raise
        finally:
            conn.close()

    # ── Aggregations ─────────────────────────────────────────────────────

    def dashboard(self) -> dict[str, Any]:
        """Dashboard stats."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM devices").fetchone()
            assert row is not None
            total_devices: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            assert row is not None
            total_records: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count > 1").fetchone()
            assert row is not None
            multi: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count = 1").fetchone()
            assert row is not None
            singles: int = row[0]

            # Source distribution
            source_counts = dict(
                conn.execute(
                    "SELECT source, COUNT(*) AS n FROM source_records GROUP BY source ORDER BY n DESC"
                ).fetchall()
            )

            # Coverage
            coverage: dict[str, str] = {}
            for field, label in [
                ("serial_number", "serial"),
                ("mac_address", "mac"),
                ("device_name", "name"),
                ("assigned_user", "assigned_user"),
            ]:
                row = conn.execute(f"SELECT COUNT(*) FROM devices WHERE {field} != ''").fetchone()
                assert row is not None
                n: int = row[0]
                coverage[label] = f"{n}/{total_devices} ({100 * n // max(total_devices, 1)}%)"

            # Top 10 clusters by source count
            top_rows = conn.execute(
                "SELECT * FROM devices ORDER BY source_count DESC, record_count DESC LIMIT 10"
            ).fetchall()
            cols = [d[0] for d in conn.description]
            top = [self._row_to_cluster_info(dict(zip(cols, r, strict=True))) for r in top_rows]
            # Source count distribution
            sc_dist = dict(
                conn.execute("""
                    SELECT source_count::VARCHAR, COUNT(*) AS n
                    FROM devices GROUP BY source_count ORDER BY source_count
                """).fetchall()
            )
            # Source combo breakdown
            combos = dict(
                conn.execute("""
                    SELECT list_sort(sources)::VARCHAR AS combo, COUNT(*) AS n
                    FROM devices GROUP BY combo ORDER BY n DESC LIMIT 20
                """).fetchall()
            )

            return {
                "clusters": total_devices,
                "records": total_records,
                "multi_source": multi,
                "singletons": singles,
                "sources": source_counts,
                "coverage": coverage,
                "top_clusters": [t.model_dump() for t in top],
                "source_count_dist": sc_dist,
                "source_combos": combos,
            }
        except Exception:
            log.error({"event": "dashboard_error"})
            raise
        finally:
            conn.close()

    def mesh_stats(self) -> dict[str, Any]:
        """Cross-source cluster statistics."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM devices").fetchone()
            assert row is not None
            total_devices: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            assert row is not None
            total_records: int = row[0]

            # Source count distribution
            sc_dist = dict(
                conn.execute("""
                SELECT source_count::VARCHAR, COUNT(*) AS n
                FROM devices GROUP BY source_count ORDER BY source_count
            """).fetchall()
            )

            # Source combo breakdown
            combos = dict(
                conn.execute("""
                SELECT list_sort(sources)::VARCHAR AS combo, COUNT(*) AS n
                FROM devices GROUP BY combo ORDER BY n DESC LIMIT 20
            """).fetchall()
            )

            records_per_source = dict(
                conn.execute(
                    "SELECT source, COUNT(*) AS n FROM source_records GROUP BY source ORDER BY n DESC"
                ).fetchall()
            )

            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count = 1").fetchone()
            assert row is not None
            singles: int = row[0]

            return {
                "total_clusters": total_devices,
                "total_records": total_records,
                "singletons": singles,
                "multi_source": total_devices - singles,
                "by_source_count": sc_dist,
                "by_source_combo": combos,
                "records_per_source": records_per_source,
            }
        finally:
            conn.close()

    def anomalies(self) -> dict[str, Any]:
        """Singletons, no-name, no-serial devices."""
        conn = self._conn()
        try:
            singletons = conn.execute(
                "SELECT * FROM devices WHERE source_count = 1 ORDER BY device_name LIMIT 50"
            ).fetchall()
            cols = [d[0] for d in conn.description]
            no_name = conn.execute("SELECT * FROM devices WHERE device_name = '(unnamed)' LIMIT 20").fetchall()
            no_serial = conn.execute(
                "SELECT * FROM devices WHERE serial_number = '' ORDER BY device_name LIMIT 30"
            ).fetchall()

            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count = 1").fetchone()
            assert row is not None
            singletons_total: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE device_name = '(unnamed)'").fetchone()
            assert row is not None
            no_name_total: int = row[0]
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE serial_number = ''").fetchone()
            assert row is not None
            no_serial_total: int = row[0]

            return {
                "singletons": singletons_total,
                "singleton_list": [
                    self._row_to_cluster_info(dict(zip(cols, r, strict=True))).model_dump() for r in singletons
                ],
                "no_name": no_name_total,
                "no_name_list": [
                    self._row_to_cluster_info(dict(zip(cols, r, strict=True))).model_dump() for r in no_name
                ],
                "no_serial": no_serial_total,
                "no_serial_list": [
                    self._row_to_cluster_info(dict(zip(cols, r, strict=True))).model_dump() for r in no_serial
                ],
            }
        except Exception:
            log.error({"event": "anomalies_error"})
            raise
        finally:
            conn.close()

    def list_clusters(
        self, min_sources: int = 1, source: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[int, list[ClusterInfo]]:
        """Paginated cluster list."""
        conn = self._conn()
        try:
            where = ["source_count >= ?"]
            params: list[object] = [min_sources]
            if source:
                where.append("list_contains(sources, ?)")
                params.append(source)
            clause = " AND ".join(where)

            row = conn.execute(f"SELECT COUNT(*) FROM devices WHERE {clause}", params).fetchone()
            assert row is not None
            total: int = row[0]
            rows = conn.execute(
                f"SELECT * FROM devices WHERE {clause} ORDER BY source_count DESC, device_name LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            cols = [d[0] for d in conn.description]
            return total, [self._row_to_cluster_info(dict(zip(cols, r, strict=True))) for r in rows]
        finally:
            conn.close()

    # ── Metrics & Events (time-series) ────────────────────────────────────

    def device_metrics(
        self,
        cluster_id: str,
        *,
        metric: str = "",
        source: str = "",
        hours: int = 24,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Time-series metrics for a device. Filterable by metric name, source, time range."""
        conn = self._conn()
        try:
            where = ["cluster_id = ?"]
            params: list[object] = [cluster_id]
            if metric:
                where.append("metric_name = ?")
                params.append(metric)
            if source:
                where.append("source = ?")
                params.append(source)
            if hours > 0:
                where.append(f"recorded_at >= now() - INTERVAL {int(hours)} HOURS")

            clause = " AND ".join(where)
            rows = conn.execute(
                f"SELECT * FROM metrics WHERE {clause} ORDER BY recorded_at DESC LIMIT ?", params + [limit]
            ).fetchall()
            cols = [d[0] for d in conn.description]
            return [dict(zip(cols, r, strict=True)) for r in rows]
        finally:
            conn.close()

    def device_metric_names(self, cluster_id: str) -> list[str]:
        """Distinct metric names available for a device."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT metric_name FROM metrics WHERE cluster_id = ? ORDER BY metric_name", [cluster_id]
            ).fetchall()
            return [cast(str, r[0]) for r in rows]
        finally:
            conn.close()

    def device_metric_summary(self, cluster_id: str, hours: int = 24) -> dict[str, dict[str, Any]]:
        """Latest value + avg/max/min per metric for a device."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"""
                SELECT
                    metric_name,
                    COUNT(*) AS count,
                    ROUND(AVG(value), 2) AS avg,
                    ROUND(MAX(value), 2) AS max,
                    ROUND(MIN(value), 2) AS min,
                    ROUND(LAST(value ORDER BY recorded_at), 2) AS latest
                FROM metrics
                WHERE cluster_id = ?
                  AND recorded_at >= now() - INTERVAL {int(hours)} HOURS
                  AND value IS NOT NULL
                GROUP BY metric_name
                ORDER BY metric_name
            """,
                [cluster_id],
            ).fetchall()
            results: dict[str, dict[str, Any]] = {}
            for r in rows:
                results[cast(str, r[0])] = {
                    "count": r[1],
                    "avg": r[2],
                    "max": r[3],
                    "min": r[4],
                    "latest": r[5],
                }
            return results
        finally:
            conn.close()

    def device_timeline(self, cluster_id: str, *, hours: int = 168, limit: int = 100) -> list[dict[str, Any]]:
        """Recent events for a device, ordered by time desc."""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM events
                WHERE cluster_id = ?
                  AND recorded_at >= now() - INTERVAL {int(hours)} HOURS
                ORDER BY recorded_at DESC
                LIMIT ?
            """,
                [cluster_id, limit],
            ).fetchall()
            cols = [d[0] for d in conn.description]
            return [dict(zip(cols, r, strict=True)) for r in rows]
        finally:
            conn.close()

    def device_attachments(self, cluster_id: str) -> list[dict[str, Any]]:
        """Get all attachment records linked to a cluster."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT feed_key, source_id, field, value, confidence, payload, linked_at "
                "FROM attachments WHERE cluster_id = ? ORDER BY linked_at DESC",
                [cluster_id],
            ).fetchall()
            cols = [d[0] for d in conn.description]
            return [dict(zip(cols, r, strict=True)) for r in rows]
        finally:
            conn.close()

    def device_stats(self, cluster_id: str) -> dict[str, Any]:
        """Current state: latest metric values + event counts."""
        conn = self._conn()
        try:
            # Latest per-metric values
            metric_rows = conn.execute(
                """
                SELECT DISTINCT ON (metric_name)
                    metric_name, value, text_value, source, recorded_at
                FROM metrics
                WHERE cluster_id = ? AND value IS NOT NULL
                ORDER BY metric_name, recorded_at DESC
            """,
                [cluster_id],
            ).fetchall()

            metrics: dict[str, Any] = {}
            for r in metric_rows:
                metrics[cast(str, r[0])] = {
                    "value": r[1],
                    "text": r[2],
                    "source": r[3],
                    "recorded_at": str(r[4]),
                }

            # Event counts by severity (last 7 days)
            event_counts = dict(
                conn.execute(
                    """
                SELECT severity, COUNT(*) FROM events
                WHERE cluster_id = ?
                  AND recorded_at >= now() - INTERVAL 168 HOURS
                GROUP BY severity
            """,
                    [cluster_id],
                ).fetchall()
            )

            return {"metrics": metrics, "event_counts": event_counts}
        finally:
            conn.close()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _row_to_cluster_info(self, row: dict[str, Any]) -> ClusterInfo:
        # sources is stored as a DuckDB LIST — convert
        raw_sources = row.get("sources", [])
        if isinstance(raw_sources, str):
            sources = [s.strip() for s in raw_sources.strip("[]").split(",") if s.strip()]
        else:
            sources = list(raw_sources) if raw_sources else []

        return ClusterInfo(
            cluster_id=_safe(row.get("cluster_id")),
            device_name=_safe(row.get("device_name")),
            source_count=len(sources),
            sources=sources,
            serial_number=_safe(row.get("serial_number")),
            mac_address=_safe(row.get("mac_address")),
            manufacturer=_safe(row.get("manufacturer")),
            model=_safe(row.get("model")),
            os=_safe(row.get("os")),
            os_version=_safe(row.get("os_version")),
            asset_tag=_safe(row.get("asset_tag")),
            assigned_user=_safe(row.get("assigned_user")),
            ip_address=_safe(row.get("ip_address")),
            imei=_safe(row.get("imei")),
            record_count=int(_safe(row.get("record_count", "0")) or 0),
        )
