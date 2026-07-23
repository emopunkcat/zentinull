"""Splink cluster validation — flag suspicious merge/split decisions.

Checks are read-only annotations over the Splink clusters CSV. Never modifies
cluster assignments — discovery as a service for human review.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from ..config import get_paths
from ..normalizer import normalize_serial

# Sentinel values also caught by normalizer (NULL_SENTINELS), but keep an
# explicit local set for field values we compare here.
_EMPTY = frozenset({"", "null", "none", "n/a", "--", "-"})


def _is_empty(val: str) -> bool:
    return not val or val in _EMPTY


def validate_clusters(clusters_csv: Path) -> list[dict[str, str]]:
    """Flag suspicious Splink decisions. Read-only — never modifies clusters.

    Checks (per cluster / cross-cluster, using normalized values):
    1. SERIAL_CONFLICT   — one cluster has >1 distinct non-empty serial_number
                           (possible false-positive merge).
    2. SPLIT_IDENTITY    — the same non-empty serial_number appears in >1
                           cluster (possible false-negative split; Splink
                           threshold was too aggressive).

    Output row shape:
        cluster_id  — the cluster_id involved (SERIAL_CONFLICT: the cluster;
                      SPLIT_IDENTITY: " (multiple)" joined on detail).
        kind        — ``"SERIAL_CONFLICT"`` | ``"SPLIT_IDENTITY"``
        field       — ``"serial_number"``
        values      — comma-joined distinct offending normalized values
        detail      — explanation; for SPLIT_IDENTITY the cluster_ids
                      that share the serial are comma-joined here.

    Returns the annotation list and writes ``cluster_annotations.csv`` to
    ``PATHS.splink_output_dir`` (header only when empty).
    """
    paths = get_paths()
    rows: list[dict[str, str]] = []

    # ── 1. Group all non-empty serials by cluster ──────────────────────────
    cluster_serials: dict[str, set[str]] = defaultdict(set)
    # ── 2. Also index by serial → set of clusters (for split check) ───────
    serial_clusters: dict[str, set[str]] = defaultdict(set)

    with open(clusters_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for record in reader:
            cid = record.get("cluster_id", "").strip()
            raw = record.get("serial_number", "")
            norm = normalize_serial(raw)

            if not cid:
                continue
            if not _is_empty(norm):
                cluster_serials[cid].add(norm)
                serial_clusters[norm].add(cid)

    # ── 3. SERIAL_CONFLICT — one cluster, multiple distinct serials ────────
    for cid, serials in sorted(cluster_serials.items()):
        # Filter out any empties that snuck through
        non_empty = {s for s in serials if not _is_empty(s)}
        if len(non_empty) > 1:
            vals = ",".join(sorted(non_empty))
            rows.append(
                {
                    "cluster_id": cid,
                    "kind": "SERIAL_CONFLICT",
                    "field": "serial_number",
                    "values": vals,
                    "detail": f"Cluster {cid} has {len(non_empty)} distinct serials",
                }
            )

    # ── 4. SPLIT_IDENTITY — one serial, multiple clusters ──────────────────
    for serial, cids in sorted(serial_clusters.items()):
        if len(cids) > 1:
            cid_list = ",".join(sorted(cids))
            # Emit one row per serial, with all clusters in detail
            rows.append(
                {
                    "cluster_id": "(multiple)",
                    "kind": "SPLIT_IDENTITY",
                    "field": "serial_number",
                    "values": serial,
                    "detail": f"Serial {serial} appears in {len(cids)} clusters: {cid_list}",
                }
            )

    # ── 5. Write annotations CSV ──────────────────────────────────────────
    out_dir = paths.splink_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cluster_annotations.csv"
    fieldnames = ["cluster_id", "kind", "field", "values", "detail"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return rows
