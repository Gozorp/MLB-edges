"""
diagnose_features.py
--------------------
Answer two questions empirically, from our own trained model and cached
feature frames:

(A) Does pitcher quality actually drive predictions?
    -> XGBoost feature importance on the saved F5 + Stage-2 models.
    -> If SP features dominate the top of the list, the model IS
       responsive to pitcher quality.

(B) Does batter quality drive predictions?
    -> Same importance readout, counting the `team_*` batting features.
    -> NOTE the known limitation: batter features are TEAM aggregates,
       not individual lineup. A missing/added star hitter only shifts
       the team aggregate to the extent of his PA share over the
       rolling window, which dilutes his individual signal.

(C) For specific named players mentioned by the user:
    - Z. Littell : pull MLB Stats API game log for his team in each of
                   his 2024+2025 starts, compare team W/L to baseline.
    - J. Wood    : pull Nationals game log, split on whether Wood played,
                   compare team win rate.
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from pathlib import Path


def importance_report():
    bundle_path = Path("models/latest.pkl")
    if not bundle_path.exists():
        print(f"Missing {bundle_path}")
        return
    bundle = joblib.load(bundle_path)
    stage1, stage2 = bundle["stage1"], bundle["stage2"]

    print("=" * 72)
    print("  XGBoost feature importance (gain) - saved F5 + full-game models")
    print("=" * 72)

    for name, bundle in [("STAGE 1 (F5, starter-only)", stage1),
                         ("STAGE 2 (full game, +team/park/ump)", stage2)]:
        booster = bundle.booster
        feats = bundle.feature_cols
        imp = booster.feature_importances_ if hasattr(booster, "feature_importances_") else None
        # sklearn wrapper gives .feature_importances_ by default (weight).
        # Pull gain-based importance via booster.get_booster().get_score().
        if hasattr(booster, "get_booster"):
            raw = booster.get_booster().get_score(importance_type="gain")
        else:
            raw = {f"f{i}": v for i, v in enumerate(imp)}

        # Map f0/f1/... -> feature names
        rows = []
        for i, f in enumerate(feats):
            g = raw.get(f"f{i}", raw.get(f, 0.0))
            rows.append((f, float(g)))
        rows.sort(key=lambda t: -t[1])
        total = sum(g for _, g in rows) or 1.0

        print(f"\n--- {name} ---")
        print(f"{'feature':30s}  {'gain':>10s}  {'% total':>7s}  category")
        for f, g in rows:
            cat = ("SP" if f.startswith("sp_") else
                   "BATTING (team)" if f.startswith("team_") else
                   "BULLPEN" if f.startswith("bullpen_") else
                   "PARK" if f.startswith("park_") else
                   "UMP" if "ump" in f else
                   "CATCHER" if "catcher" in f else
                   "LUCK" if "luck" in f else
                   "STRUCTURAL" if f in ("is_divisional", "tz_diff",
                                         "is_opener", "is_quick_turnaround",
                                         "f5_model_output") else "OTHER")
            print(f"{f:30s}  {g:>10.2f}  {100*g/total:>6.1f}%  {cat}")

        # Aggregate by category
        agg = {}
        for f, g in rows:
            cat = ("SP (individual pitcher)" if f.startswith("sp_") else
                   "BATTING (team aggregate)" if f.startswith("team_") else
                   "BULLPEN" if f.startswith("bullpen_") else
                   "PARK" if f.startswith("park_") else
                   "UMP" if "ump" in f else
                   "CATCHER" if "catcher" in f else
                   "LUCK / xERA" if "luck" in f else
                   "F5 -> full chain" if f == "f5_model_output" else
                   "STRUCTURAL")
            agg[cat] = agg.get(cat, 0.0) + g
        print(f"\n  Category totals ({name}):")
        for cat, g in sorted(agg.items(), key=lambda t: -t[1]):
            print(f"    {cat:30s}  {100*g/total:>6.1f}%")


if __name__ == "__main__":
    importance_report()
