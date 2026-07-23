"""DuckDB query layer — typed, async-safe, single connection per request."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

import duckdb

from ..config import get_paths
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

    def __init__(self, db_path: Path | None = None) -> None:
        paths = get_paths()
        self._path = db_path or paths.mesh_path

    def ping(self) -> bool:
        """Validate database connectivity."""
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _conn(self, read_only: bool = True) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(str(self._path), read_only=read_only)
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

    def device_story(self, query: str) -> DeviceStory | None:
        """Convenience alias for ``lookup`` — resolve a query to a full DeviceStory."""
        return self.lookup(query)

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
                    mdm_latitude=_safe(rd.get("mdm_latitude")),
                    mdm_longitude=_safe(rd.get("mdm_longitude")),
                    mdm_horizontal_accuracy=_safe(rd.get("mdm_horizontal_accuracy")),
                    mdm_location_address=_safe(rd.get("mdm_location_address")),
                    mdm_located_time=_safe(rd.get("mdm_located_time")),
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
            "mdm_latitude",
            "mdm_longitude",
            "mdm_horizontal_accuracy",
            "mdm_location_address",
            "mdm_located_time",
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
            "os",
            "os_family",
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

        # ── SOT resolution + drift audit ──────────────────────────────────
        from ..manifest import load_manifest
        from ..resolve.sot import sot_resolve

        profile = load_manifest().profiles["device"]
        meta = {"source", "source_id", "extra_attributes"}
        data_fields = [f for f in profile.fields if f not in meta and f not in profile.derived]

        # Build per-source record maps for SOT resolution
        src_records: dict[str, dict[str, Any]] = {}
        for r in rows:
            rd = dict(zip(sr_cols, r, strict=True))
            src = _safe(rd.get("source"))
            if src not in src_records:
                src_records[src] = {}
            for field in data_fields:
                val = _safe(rd.get(field))
                if val:
                    src_records[src][field] = val

        coverage = {k: s.coverage for k, s in load_manifest().systems.items()}
        sot_result = sot_resolve(profile, src_records, coverage=coverage)
        device_sot: dict[str, dict[str, str]] = {}
        for field, (value, source, priority) in sot_result.items():
            device_sot[field] = {
                "value": value or "",
                "source": source or "",
                "priority": priority,
            }

        # Build drift audit — per-field cross-source comparison
        drift_audit: list[dict[str, Any]] = []
        for field in data_fields:
            values: dict[str, str] = {}
            for src in src_records:
                val = _safe(src_records[src].get(field, ""))
                if val:
                    values[src] = val
            if len(values) >= 2:
                unique = set(v.lower() for v in values.values())
                verdict = "MATCH" if len(unique) == 1 else "MISMATCH"
            elif len(values) == 1:
                verdict = "SINGLE_SOURCE"
            else:
                continue
            drift_audit.append(
                {
                    "field": field,
                    "label": field.replace("_", " ").title(),
                    "sources": values,
                    "verdict": verdict,
                }
            )

        return DeviceStory(
            query=query,
            cluster_id=cluster_id,
            device_name=name,
            source_count=len(sources),
            sources=sources,
            record_count=len(rows),
            consolidated=consolidated,
            enriched=self._query_enriched(conn, cluster_id),
            records=recs,
            sot=device_sot,
            drift_audit=drift_audit,
        )

    def _query_enriched(self, conn: duckdb.DuckDBPyConnection, cluster_id: str) -> dict[str, str]:
        """Query v_device_enriched for this cluster, return non-empty concepts."""
        try:
            row = conn.execute("SELECT * FROM v_device_enriched WHERE cluster_id = ?", [cluster_id]).fetchone()
        except duckdb.Error:
            return {}
        if row is None:
            return {}
        cols = [d[0] for d in conn.description]
        return {
            c: str(v) for c, v in zip(cols, row, strict=True) if c != "cluster_id" and v is not None and str(v).strip()
        }

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
            total_devices: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            total_records: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count > 1").fetchone()
            multi: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE source_count = 1").fetchone()
            singles: int = row[0] if row else 0

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
                n: int = row[0] if row else 0
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
            total_devices: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM source_records").fetchone()
            total_records: int = row[0] if row else 0

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
            singles: int = row[0] if row else 0

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
            singletons_total: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE device_name = '(unnamed)'").fetchone()
            no_name_total: int = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM devices WHERE serial_number = ''").fetchone()
            no_serial_total: int = row[0] if row else 0
            # ── Zombie detection ──────────────────────────────────────────────
            from ..config import ZOMBIE_STALE_DAYS

            zombies_total: int = 0
            zombie_rows: list[dict[str, Any]] = []
            try:
                zombie_rows_raw = conn.execute(
                    "SELECT * FROM devices WHERE cluster_id IN ("
                    "  SELECT cluster_id FROM record_freshness"
                    "  GROUP BY cluster_id"
                    "  HAVING MAX(fetched_at) < NOW() - INTERVAL ? DAY"
                    ")"
                    " ORDER BY device_name LIMIT 20",
                    [ZOMBIE_STALE_DAYS],
                ).fetchall()
                zombie_cols = [d[0] for d in conn.description]
                zombie_rows = [
                    self._row_to_cluster_info(dict(zip(zombie_cols, r, strict=True))).model_dump()
                    for r in zombie_rows_raw
                ]
                row = conn.execute(
                    "SELECT COUNT(*) FROM devices WHERE cluster_id IN ("
                    "  SELECT cluster_id FROM record_freshness"
                    "  GROUP BY cluster_id"
                    "  HAVING MAX(fetched_at) < NOW() - INTERVAL ? DAY"
                    ")",
                    [ZOMBIE_STALE_DAYS],
                ).fetchone()
                if row is not None:
                    zombies_total = row[0]
            except Exception:
                log.info({"event": "zombie_query_failed", "detail": "record_freshness may not exist yet"})
                pass

            # ── Hardware drift (serial conflicts within clusters) ─────────────
            drift_count: int = 0
            drift_list: list[dict[str, Any]] = []
            try:
                drift_rows = conn.execute("""
                    SELECT cluster_id, COUNT(DISTINCT serial_number) AS serials,
                           LIST(DISTINCT serial_number) AS serial_values,
                           LIST(DISTINCT source) AS sources
                    FROM source_records
                    WHERE serial_number != ''
                    GROUP BY cluster_id
                    HAVING COUNT(DISTINCT serial_number) > 1
                    ORDER BY serials DESC
                    LIMIT 30
                """).fetchall()
                drift_list = [
                    {"cluster_id": r[0], "serial_count": r[1], "serial_values": r[2], "sources": r[3]}
                    for r in drift_rows
                ]
                row = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT cluster_id FROM source_records
                        WHERE serial_number != ''
                        GROUP BY cluster_id
                        HAVING COUNT(DISTINCT serial_number) > 1
                    )
                """).fetchone()
                if row is not None:
                    drift_count = row[0]
            except Exception:
                log.info({"event": "drift_query_failed"})
                pass

            # ── Review annotations from cluster_annotations ────────────────────
            review_count: int = 0
            review_list: list[dict[str, Any]] = []
            try:
                review_rows = conn.execute(
                    "SELECT cluster_id, kind, field, values, detail"
                    " FROM cluster_annotations ORDER BY cluster_id LIMIT 50"
                ).fetchall()
                review_list = [
                    {"cluster_id": r[0], "kind": r[1], "field": r[2], "values": r[3], "detail": r[4]}
                    for r in review_rows
                ]
                row = conn.execute("SELECT COUNT(*) FROM cluster_annotations").fetchone()
                if row is not None:
                    review_count = row[0]
            except Exception:
                log.info({"event": "review_query_failed", "detail": "cluster_annotations may not exist yet"})
                pass

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
                "zombies": zombies_total,
                "zombie_list": zombie_rows,
                "hardware_drift": drift_count,
                "hardware_drift_list": drift_list,
                "review_total": review_count,
                "review_list": review_list,
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
            total: int = row[0] if row else 0
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
            results: list[dict[str, Any]] = []
            for r in rows:
                d = dict(zip(cols, r, strict=True))
                # Convert datetime → ISO string for Pydantic str fields
                if d.get("recorded_at"):
                    d["recorded_at"] = str(d["recorded_at"])
                if d.get("ingested_at"):
                    d["ingested_at"] = str(d["ingested_at"])
                results.append(d)
            return results
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
            results: list[dict[str, Any]] = []
            for r in rows:
                d = dict(zip(cols, r, strict=True))
                if d.get("recorded_at"):
                    d["recorded_at"] = str(d["recorded_at"])
                if d.get("ingested_at"):
                    d["ingested_at"] = str(d["ingested_at"])
                results.append(d)
            return results
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
            results: list[dict[str, Any]] = []
            for r in rows:
                d = dict(zip(cols, r, strict=True))
                # payload is stored as JSON string in DuckDB; parse to dict for Pydantic
                payload = d.get("payload")
                if isinstance(payload, str):
                    with contextlib.suppress(json.JSONDecodeError):
                        d["payload"] = json.loads(payload)
                if not isinstance(d.get("payload"), dict):
                    d["payload"] = {}
                # linked_at datetime → ISO string
                if d.get("linked_at"):
                    d["linked_at"] = str(d["linked_at"])
                results.append(d)
            return results
        finally:
            conn.close()

    def device_vlans(self, cluster_id: str) -> list[dict[str, Any]]:
        """Return SharePoint VLANs whose IP range contains any of the cluster's IPs.

        VLANs are stored in sp.sqlite as CONTEXT; this is a runtime CIDR join.
        """
        paths = get_paths()
        conn = self._conn()
        try:
            ip_rows = conn.execute(
                "SELECT DISTINCT ip_address FROM source_records WHERE cluster_id = ? AND ip_address != ''",
                [cluster_id],
            ).fetchall()
        finally:
            conn.close()

        device_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for (ip,) in ip_rows:
            with contextlib.suppress(ValueError):
                device_ips.add(ipaddress.ip_address(ip))

        if not device_ips:
            return []

        sp_db = paths.data_dir / "sp.sqlite"
        if not sp_db.exists():
            return []

        results: list[dict[str, Any]] = []
        sconn = sqlite3.connect(str(sp_db))
        sconn.row_factory = sqlite3.Row
        try:
            rows = sconn.execute("SELECT raw_json FROM sp_vlans").fetchall()
            for r in rows:
                try:
                    fields = json.loads(r["raw_json"]).get("fields", {})
                except (json.JSONDecodeError, TypeError):
                    continue
                network_id = fields.get("NetworkID", "")
                starting_ip = fields.get("StartingIP", "")
                ending_ip = fields.get("EndingIP", "")
                if not network_id or not starting_ip or not ending_ip:
                    continue
                try:
                    vlan_net = ipaddress.ip_network(network_id, strict=False)
                    start = ipaddress.ip_address(starting_ip)
                    end = ipaddress.ip_address(ending_ip)
                except ValueError:
                    continue
                # A VLAN matches if any device IP is in the network and between
                # start/end. Compare as ints after a version guard — mixing v4/v6
                # raises TypeError on ordered comparison.
                for dev_ip in device_ips:
                    if not (dev_ip.version == vlan_net.version == start.version == end.version):
                        continue
                    in_network = int(vlan_net.network_address) <= int(dev_ip) <= int(vlan_net.broadcast_address)
                    if in_network and int(start) <= int(dev_ip) <= int(end):
                        results.append(
                            {
                                "id": fields.get("ID"),
                                "vlan_name": fields.get("VlanName"),
                                "network_id": network_id,
                                "network": fields.get("Network", ""),
                                "starting_ip": starting_ip,
                                "ending_ip": ending_ip,
                                "description": fields.get("Description", ""),
                            }
                        )
                        break
        finally:
            sconn.close()

        return results

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

    def device_trace(self, cluster_id: str) -> dict[str, Any]:
        """Full mesh trace from a cluster — like sentinull's identity trace.

        Returns:
            - The device itself (consolidated + sources)
            - Other devices sharing the same assigned_user
            - All attachments (account info, device notes, purchases, employees)
            - VLAN membership
            - Linked devices discovered via attachments (e.g., same purchase, same account)
        """
        conn = self._conn()
        try:
            # 1. Get the anchor device
            dev = conn.execute("SELECT * FROM devices WHERE cluster_id = ?", [cluster_id]).fetchone()
            if not dev:
                return {}
            cols = [d[0] for d in conn.description]
            device = dict(zip(cols, dev, strict=True))

            # 2. Get source records for this cluster
            src_rows = conn.execute(
                "SELECT source, source_id, name, name_clean, serial_number, mac_address, "
                "mac_clean, manufacturer, model, os, os_version, asset_tag, assigned_user, "
                "ip_address, imei, extra_attributes "
                "FROM source_records WHERE cluster_id = ?",
                [cluster_id],
            ).fetchall()
            src_cols = [d[0] for d in conn.description]
            sources = [dict(zip(src_cols, r, strict=True)) for r in src_rows]
        finally:
            conn.close()

        # 3. Find other devices sharing the same assigned_user
        assigned_user = device.get("assigned_user", "")
        linked_by_user: list[dict[str, Any]] = []
        if assigned_user and assigned_user not in ("", "null", "None"):
            conn = self._conn()
            try:
                user_rows = conn.execute(
                    "SELECT cluster_id, device_name, source_count, sources, serial_number, "
                    "mac_address, manufacturer, model, os, ip_address "
                    "FROM devices WHERE assigned_user = ? AND cluster_id != ? "
                    "ORDER BY source_count DESC",
                    [assigned_user, cluster_id],
                ).fetchall()
                ucols = [d[0] for d in conn.description]
                for r in user_rows:
                    rd = dict(zip(ucols, r, strict=True))
                    rd["link_type"] = "shared_user"
                    rd["via"] = assigned_user
                    linked_by_user.append(rd)
            finally:
                conn.close()

        # 4. Get attachments for this cluster
        attachments = self.device_attachments(cluster_id)

        # 5. Find linked devices via shared attachment (e.g., same purchase order, same account)
        linked_by_attachment: list[dict[str, Any]] = []
        attachment_values = {a["value"] for a in attachments if a.get("value")}
        if attachment_values:
            conn = self._conn()
            try:
                placeholders = ", ".join(["?"] * len(attachment_values))
                link_rows = conn.execute(
                    f"SELECT DISTINCT a2.cluster_id, d.device_name, d.source_count, d.sources, "
                    f"d.serial_number, d.mac_address, d.manufacturer, d.model, "
                    f"a2.feed_key, a2.field, a2.value "
                    f"FROM attachments a2 JOIN devices d ON d.cluster_id = a2.cluster_id "
                    f"WHERE a2.value IN ({placeholders}) AND a2.cluster_id != ? "
                    f"LIMIT 20",
                    [*list(attachment_values), cluster_id],
                ).fetchall()
                lcols = [d[0] for d in conn.description]
                for r in link_rows:
                    rd = dict(zip(lcols, r, strict=True))
                    rd["link_type"] = f"shared_{rd.get('feed_key', 'attachment')}"
                    rd["via"] = rd.get("value", "")
                    linked_by_attachment.append(rd)
            finally:
                conn.close()

        # 6. VLAN membership
        vlans = self.device_vlans(cluster_id)

        # 7. Build node/edge graph
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # Anchor device node
        nodes.append(
            {
                "id": cluster_id,
                "type": "device",
                "label": device.get("device_name", cluster_id),
                "data": {
                    "serial_number": device.get("serial_number", ""),
                    "mac_address": device.get("mac_address", ""),
                    "manufacturer": device.get("manufacturer", ""),
                    "model": device.get("model", ""),
                    "assigned_user": assigned_user,
                    "sources": device.get("sources", []),
                    "source_count": device.get("source_count", 0),
                },
            }
        )

        # Linked-by-user nodes + edges
        for ld in linked_by_user:
            cid = ld.get("cluster_id", "")
            nodes.append(
                {
                    "id": cid,
                    "type": "device",
                    "label": ld.get("device_name", cid),
                    "data": {k: v for k, v in ld.items() if k not in ("link_type", "via")},
                }
            )
            edges.append(
                {
                    "source": cluster_id,
                    "target": cid,
                    "type": "shared_user",
                    "label": f"user: {assigned_user}",
                }
            )

        # Linked-by-attachment nodes + edges
        for ld in linked_by_attachment:
            cid = ld.get("cluster_id", "")
            if not any(n["id"] == cid for n in nodes):
                nodes.append(
                    {
                        "id": cid,
                        "type": "device",
                        "label": ld.get("device_name", cid),
                        "data": {
                            k: v for k, v in ld.items() if k not in ("link_type", "via", "feed_key", "field", "value")
                        },
                    }
                )
            edges.append(
                {
                    "source": cluster_id,
                    "target": cid,
                    "type": ld.get("link_type", "shared_attachment"),
                    "label": f"{ld.get('feed_key', '')}: {ld.get('via', '')}",
                }
            )

        # User node
        if assigned_user and assigned_user not in ("", "null", "None"):
            nodes.append(
                {
                    "id": f"user:{assigned_user}",
                    "type": "user",
                    "label": assigned_user,
                }
            )
            edges.append(
                {
                    "source": cluster_id,
                    "target": f"user:{assigned_user}",
                    "type": "assigned_to",
                }
            )

        # VLAN nodes
        for vlan in vlans:
            vid = f"vlan:{vlan.get('vlan_name', '')}"
            nodes.append(
                {
                    "id": vid,
                    "type": "vlan",
                    "label": vlan.get("vlan_name", ""),
                    "data": vlan,
                }
            )
            edges.append(
                {
                    "source": cluster_id,
                    "target": vid,
                    "type": "on_vlan",
                }
            )

        return {
            "query_cluster_id": cluster_id,
            "device": device,
            "sources": sources,
            "attachments": attachments,
            "linked_devices": linked_by_user + linked_by_attachment,
            "vlans": vlans,
            "graph": {
                "nodes": nodes,
                "edges": edges,
            },
        }

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
            mdm_latitude=_safe(row.get("mdm_latitude")),
            mdm_longitude=_safe(row.get("mdm_longitude")),
            mdm_horizontal_accuracy=_safe(row.get("mdm_horizontal_accuracy")),
            mdm_location_address=_safe(row.get("mdm_location_address")),
            mdm_located_time=_safe(row.get("mdm_located_time")),
            record_count=int(_safe(row.get("record_count", "0")) or 0),
        )

    def unmapped_fields(self, limit: int = 100) -> list[dict[str, Any]]:
        """Top unmapped raw fields per source, from extra_attributes JSON."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT source, je.key AS field, COUNT(*) AS occurrences
                FROM source_records,
                     json_each(CASE WHEN extra_attributes IN ('', '{}')
                                    THEN '{}' ELSE extra_attributes END) je
                GROUP BY source, je.key
                ORDER BY occurrences DESC
                LIMIT ?
            """,
                [limit],
            ).fetchall()
            return [{"source": r[0], "field": r[1], "occurrences": r[2]} for r in rows]
        finally:
            conn.close()
