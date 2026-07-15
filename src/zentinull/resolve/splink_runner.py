"""Splink runner — profile-driven entity resolution.

Replaces the hardcoded Splink configuration in scripts/run_splink.py with
a manifest-driven runner that reads comparisons, blocking rules, and thresholds
from a ResolutionProfile.
"""

from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import splink.comparison_library as cl

from splink import DuckDBAPI, Linker, SettingsCreator, block_on

from ..config import PATHS
from ..logging_config import get_logger
from ..manifest.types import Comparison, ResolutionProfile

log = get_logger("splink.runner")


def _translate_comparison(comp: Comparison) -> Any:
    """Translate a manifest Comparison into a splink.comparison_library object.

    Per the plan §5 contract:
    - levenshtein → cl.LevenshteinAtThresholds(col, distance_threshold_or_thresholds=thresholds)
    - jaro_winkler → cl.JaroWinklerAtThresholds(col, score_threshold_or_thresholds=thresholds)
    - exact → cl.ExactMatch(col)
    - If term_frequency_adjustments=True, chain .configure(term_frequency_adjustments=True)
    """
    obj: Any = None
    if comp.kind == "levenshtein":
        obj = cl.LevenshteinAtThresholds(
            comp.column,
            distance_threshold_or_thresholds=[int(t) for t in comp.thresholds],
        )
    elif comp.kind == "jaro_winkler":
        obj = cl.JaroWinklerAtThresholds(
            comp.column,
            score_threshold_or_thresholds=list(comp.thresholds),
        )
    elif comp.kind == "exact":
        obj = cl.ExactMatch(comp.column)
    else:
        raise ValueError(f"Unknown comparison kind: {comp.kind}")

    if comp.term_frequency_adjustments:
        obj = obj.configure(term_frequency_adjustments=True)

    return obj


def run(
    profile: ResolutionProfile,
    csv_path: str,
    labels_path: str | None = None,
) -> Path:
    """Run full Splink pipeline against a CSV using the profile's config.

    Args:
        profile: ResolutionProfile from the manifest (comparisons, blocking, thresholds).
        csv_path: Path to the input devices.csv.
        labels_path: Optional path to training_labels.csv for the supervised step.

    Returns:
        Path to the generated clusters.csv under export/splink_output/.
    """
    # ── Load CSV ────────────────────────────────────────────────────────
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    df["unique_id"] = df["source"] + "::" + df["source_id"].astype(str)
    log.info({"event": "load", "records": len(df), "sources": len(df["source"].unique())})

    if len(df) == 0:
        log.warning({"event": "empty_input", "csv": csv_path})
        PATHS.splink_output_dir.mkdir(parents=True, exist_ok=True)
        out_path = PATHS.splink_output_dir / "clusters.csv"
        pd.DataFrame().to_csv(out_path, index=False)
        return out_path

    # ── Translate comparisons ───────────────────────────────────────────
    comparisons = [_translate_comparison(c) for c in profile.comparisons]

    # ── Build SettingsCreator ───────────────────────────────────────────
    settings = SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="unique_id",
        additional_columns_to_retain=list(profile.fields),
        blocking_rules_to_generate_predictions=[block_on(col) for col in profile.blocking],
        comparisons=comparisons,
        retain_matching_columns=True,
    )

    db_api = DuckDBAPI()
    linker = Linker(df, settings, db_api=db_api)

    # ── Training Step 1: λ estimation ───────────────────────────────────
    log.info({"event": "step", "step": "estimate_lambda"})
    linker.training.estimate_probability_two_random_records_match(
        deterministic_matching_rules=[block_on(col) for col in profile.deterministic],
        recall=profile.lambda_recall,
    )

    # ── Training Step 2: u-probabilities ────────────────────────────────
    log.info({"event": "step", "step": "estimate_u"})
    linker.training.estimate_u_using_random_sampling(max_pairs=float(profile.u_max_pairs or 1_000_000))

    # ── Training Step 3: EM passes ──────────────────────────────────────
    for i, em_col in enumerate(profile.em_passes):
        log.info({"event": "step", "step": f"em_pass_{i}", "column": em_col})
        linker.training.estimate_parameters_using_expectation_maximisation(
            blocking_rule=block_on(em_col),
            fix_probability_two_random_records_match=True,
            fix_u_probabilities=True,
        )

    # ── Training Step 4: Supervised boost ───────────────────────────────
    if labels_path and Path(labels_path).exists():
        log.info({"event": "step", "step": "supervised_boost"})
        labels_df = pd.read_csv(labels_path)
        positives = labels_df[labels_df["label"] == 1][
            ["unique_id_l", "unique_id_r", "source_dataset_l", "source_dataset_r"]
        ]
        linker.table_management.register_table(positives, "labels", overwrite=True)
        linker.training.estimate_m_from_pairwise_labels("labels")  # type: ignore[no-untyped-call]
        log.info({"event": "supervised", "pairs": len(positives)})
    else:
        log.warning({"event": "skip_supervised", "reason": "labels_path missing or None"})

    # ── Predict ─────────────────────────────────────────────────────────
    log.info({"event": "step", "step": "predict"})
    pairwise = linker.inference.predict(threshold_match_weight=profile.predict_threshold)

    pw = pairwise.as_record_dict()
    cross = [p for p in pw if p.get("source_l") != p.get("source_r")]
    weights = sorted([p.get("match_weight", -99) for p in cross], reverse=True)
    log.info({"event": "pairs", "total": len(pw), "cross_source": len(cross)})
    if weights:
        log.info({"event": "match_weight_range", "min": round(weights[-1], 1), "max": round(weights[0], 1)})

    # ── Log trained m-probabilities ─────────────────────────────────────
    log.info({"event": "step", "step": "trained_m_probabilities"})
    for c in linker._settings_obj.comparisons:
        nm = c.output_column_name
        for lvl in c.comparison_levels:
            try:
                m = lvl.m_probability
                u = lvl.u_probability
                label = lvl.label_for_charts
                if m is not None and u is not None:
                    weight = f"{m / u:.0f}x" if u > 0 else "?"
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

    # ── Threshold sweep ─────────────────────────────────────────────────
    log.info({"event": "step", "step": "clusters", "cluster_threshold": profile.cluster_threshold})
    _sweep_results: dict[float, tuple[dict[str, set[str]], dict[str, list[dict[str, Any]]]]] = {}
    for mw in profile.sweep_thresholds:
        _clu = linker.clustering.cluster_pairwise_predictions_at_threshold(pairwise, threshold_match_weight=mw)
        _sweep_results[mw] = ({}, {})
        for rec in _clu.as_record_dict():
            _sweep_results[mw][0].setdefault(rec["cluster_id"], set()).add(rec["source"])
            _sweep_results[mw][1].setdefault(rec["cluster_id"], []).append(rec)
        log.info(
            {
                "event": "threshold_sweep",
                "match_weight": mw,
                "clusters": len(_sweep_results[mw][0]),
                "multi_source": sum(1 for v in _sweep_results[mw][0].values() if len(v) >= 2),
                "multi_source_3plus": sum(1 for v in _sweep_results[mw][0].values() if len(v) >= 3),
            }
        )

    # ── Final cluster ───────────────────────────────────────────────────
    _cluster_t = int(profile.cluster_threshold)
    if _cluster_t not in _sweep_results:
        _extra = linker.clustering.cluster_pairwise_predictions_at_threshold(
            pairwise, threshold_match_weight=_cluster_t
        )
        _sweep_results[_cluster_t] = ({}, {})
        for rec in _extra.as_record_dict():
            _sweep_results[_cluster_t][0].setdefault(rec["cluster_id"], set()).add(rec["source"])
            _sweep_results[_cluster_t][1].setdefault(rec["cluster_id"], []).append(rec)

    cs, cr = _sweep_results[_cluster_t]
    multi3 = {k: v for k, v in cs.items() if len(v) >= 3}
    log.info(
        {
            "event": "best_threshold",
            "match_weight": _cluster_t,
            "clusters_3plus": len(multi3),
            "separator": "=" * 60,
        }
    )

    # Show 3+ source clusters
    for cid in sorted(multi3, key=lambda c: -len(multi3[c]))[:15]:
        recs = cr[cid]
        srcs = sorted(multi3[cid])
        names = sorted(set(str(r.get("name", r.get("name_clean", "?")))[:30] for r in recs if r.get("name")))
        log.info(
            {
                "event": "cluster",
                "name": names[0] if names else "?",
                "sources_count": len(srcs),
                "sources": ", ".join(srcs),
            }
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

    # ── Export ──────────────────────────────────────────────────────────
    PATHS.splink_output_dir.mkdir(parents=True, exist_ok=True)
    out_path = PATHS.splink_output_dir / "clusters.csv"
    pd.DataFrame([r for recs in cr.values() for r in recs]).to_csv(out_path, index=False)
    log.info({"event": "export", "path": str(out_path), "records": sum(len(recs) for recs in cr.values())})

    return out_path
