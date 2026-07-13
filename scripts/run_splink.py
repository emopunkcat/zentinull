"""
Splink v4 — properly trained from raw data.
No manual lambda. Deterministic rules -> lambda estimation -> u -> EM round-robin -> supervised labels.

DATA LINEAGE NOTICE:
The `additional_columns_to_retain` list below defines which CSV columns survive
Splink's clustering and appear in the output `clusters.csv`. Any field omitted
from this list is SILENTLY DROPPED — no warning, no log.

If you add a new field to `SPLINK_FIELDS` in `export_for_splink.py`, you MUST
also add it to `additional_columns_to_retain` here, or it will never reach the
DuckDB mesh database or the API layer.

Current retained fields: source, source_id, name, name_clean, serial_number,
mac_address, mac_clean, asset_tag, manufacturer, model, os, os_version,
assigned_user, ip_address, imei.
"""

from collections import defaultdict
from pathlib import Path

import pandas as pd
import splink.comparison_library as cl
from splink import DuckDBAPI, Linker, SettingsCreator, block_on

from zentinull.logging_config import get_logger, setup

ROOT = Path(__file__).resolve().parent.parent
CSV = str(ROOT / "export" / "csv" / "devices.csv")
LABELS = str(ROOT / "export" / "splink_output" / "training_labels.csv")
OUT_DIR = ROOT / "export" / "splink_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)
setup()
log = get_logger("splink")
db_api = DuckDBAPI()

# ── Load ───────────────────────────────────────────────────────────────────
df = pd.read_csv(CSV)
df.columns = [c.strip() for c in df.columns]
df["unique_id"] = df["source"] + "::" + df["source_id"].astype(str)
log.info({"event": "load", "records": len(df), "sources": len(df["source"].unique())})

# ── Settings — all normalized fields ───────────────────────────────────────
settings = SettingsCreator(
    link_type="dedupe_only",
    unique_id_column_name="unique_id",
    additional_columns_to_retain=[
        "source",
        "source_id",
        "name",
        "name_clean",
        "serial_number",
        "mac_address",
        "mac_clean",
        "asset_tag",
        "manufacturer",
        "model",
        "os",
        "os_version",
        "assigned_user",
        "ip_address",
        "imei",
        "extra_attributes",
    ],
    blocking_rules_to_generate_predictions=[
        block_on("serial_number"),
        block_on("mac_clean"),
        block_on("substr(name_clean, 1, 4)"),
    ],
    comparisons=[
        cl.ExactMatch("serial_number"),
        cl.ExactMatch("mac_clean"),
        cl.JaroWinklerAtThresholds("name_clean", [0.92, 0.85]),
        cl.ExactMatch("manufacturer"),
        cl.ExactMatch("assigned_user"),
        cl.ExactMatch("os"),
    ],
    retain_matching_columns=True,
)

linker = Linker(df, settings, db_api=db_api)

# ═══════════════════════════════════════════════════════════════════════════
#  PROPER TRAINING SEQUENCE (Splink docs)
# ═══════════════════════════════════════════════════════════════════════════

# 1. Estimate λ using deterministic matching rules
#    These are STRICT rules that definitely indicate a match.
#    serial match → match. mac_clean match → match.
log.info({"event": "step", "step": "estimate_lambda"})
linker.training.estimate_probability_two_random_records_match(
    deterministic_matching_rules=[
        block_on("serial_number"),  # 46 SP↔MDM matches
        block_on("mac_clean"),  # 39+ FG↔ME_EC matches
    ],
    recall=0.5,  # these strict rules catch ~50% of true matches
)

# 2. Estimate u-probabilities directly from random pairs
log.info({"event": "step", "step": "estimate_u"})
linker.training.estimate_u_using_random_sampling(max_pairs=2_000_000)

# 3. EM round-robin to train m-probabilities
#    Pass A: block on serial → trains mac_clean, name_clean, manufacturer, etc.
#    Pass B: block on name_clean prefix → trains serial
log.info({"event": "step", "step": "em_pass_serial"})
linker.training.estimate_parameters_using_expectation_maximisation(
    blocking_rule=block_on("serial_number"),
    fix_probability_two_random_records_match=True,
    fix_u_probabilities=True,
)

log.info({"event": "step", "step": "em_pass_name"})
linker.training.estimate_parameters_using_expectation_maximisation(
    blocking_rule=block_on("substr(name_clean, 1, 3)"),
    fix_probability_two_random_records_match=True,
    fix_u_probabilities=True,
)

# 4. Supervised boost from verified multi-source matches
log.info({"event": "step", "step": "supervised_boost"})
labels_df = pd.read_csv(LABELS)
positives = labels_df[labels_df["label"] == 1][["unique_id_l", "unique_id_r", "source_dataset_l", "source_dataset_r"]]
linker.table_management.register_table(positives, "labels", overwrite=True)
linker.training.estimate_m_from_pairwise_labels("labels")
log.info({"event": "supervised", "pairs": len(positives)})

# ═══════════════════════════════════════════════════════════════════════════
#  Predict & Cluster
# ═══════════════════════════════════════════════════════════════════════════
log.info({"event": "step", "step": "predict"})
pairwise = linker.inference.predict(threshold_match_weight=-20)

pw = pairwise.as_record_dict()
cross = [p for p in pw if p.get("source_l") != p.get("source_r")]
weights = sorted([p.get("match_weight", -99) for p in cross], reverse=True)
log.info({"event": "pairs", "total": len(pw), "cross_source": len(cross)})
if weights:
    log.info({"event": "match_weight_range", "min": round(weights[-1], 1), "max": round(weights[0], 1)})

# Show trained m-probabilities
log.info({"event": "step", "step": "trained_m_probabilities"})
for c in linker._settings_obj.comparisons:
    nm = c.output_column_name
    for lvl in c.comparison_levels:
        try:
            m = lvl.m_probability
            u = lvl.u_probability
            label = lvl.label_for_charts
            if m is not None:
                weight = f"{m / u:.0f}x" if u and u > 0 else "?"
                log.info(
                    {
                        "event": "m_probability",
                        "field": nm.strip(),
                        "level": label.strip(),
                        "m": round(m, 3),
                        "u": round(u, 6),
                        "ratio": weight.strip(),
                    }
                )
        except Exception:
            pass

# Threshold sweep
log.info({"event": "step", "step": "clusters"})
for mw in [10, 5, 0, -2, -5, -10]:
    clu = linker.clustering.cluster_pairwise_predictions_at_threshold(pairwise, threshold_match_weight=mw)
    cd = clu.as_record_dict()
    cs = defaultdict(set)
    cr = defaultdict(list)
    for rec in cd:
        cs[rec["cluster_id"]].add(rec["source"])
        cr[rec["cluster_id"]].append(rec)
    multi = sum(1 for v in cs.values() if len(v) >= 2)
    multi3 = sum(1 for v in cs.values() if len(v) >= 3)
    log.info(
        {
            "event": "threshold_sweep",
            "match_weight": mw,
            "clusters": len(cs),
            "multi_source": multi,
            "multi_source_3plus": multi3,
        }
    )

# Best threshold: where 3+ source clusters first appear
BEST = -5
for mw in [10, 5, 0, -2, -5, -10]:
    clu = linker.clustering.cluster_pairwise_predictions_at_threshold(pairwise, threshold_match_weight=mw)
    cd = clu.as_record_dict()
    cs = defaultdict(set)
    cr = defaultdict(list)
    for rec in cd:
        cs[rec["cluster_id"]].add(rec["source"])
        cr[rec["cluster_id"]].append(rec)
    multi3 = {k: v for k, v in cs.items() if len(v) >= 3}
    if multi3:
        BEST = mw
        break

log.info({"event": "best_threshold", "match_weight": BEST, "clusters_3plus": len(multi3), "separator": "=" * 60})

# Show 3+ source clusters
for cid in sorted(multi3, key=lambda c: -len(multi3[c]))[:15]:
    recs = cr[cid]
    srcs = sorted(multi3[cid])
    names = sorted(set(str(r.get("name", r.get("name_clean", "?")))[:30] for r in recs if r.get("name")))
    log.info(
        {"event": "cluster", "name": names[0] if names else "?", "sources_count": len(srcs), "sources": ", ".join(srcs)}
    )
    for r in recs[:8]:
        src = r.get("source", "?")
        nm = (r.get("name") or r.get("name_clean") or "?")[:30]
        mac = (r.get("mac_address") or r.get("mac_clean") or "")[:17]
        ser = (r.get("serial_number") or "")[:18]
        log.info(
            {
                "event": "cluster_device",
                "source": src.strip(),
                "name": nm.strip(),
                "mac": mac.strip(),
                "serial": ser.strip(),
            }
        )

# Export
pd.DataFrame(cd).to_csv(OUT_DIR / "clusters.csv", index=False)
log.info({"event": "export", "path": str(OUT_DIR / "clusters.csv")})
