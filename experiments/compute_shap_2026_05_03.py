"""Per-game SHAP decomposition for 2026-05-03 slate timeline narrative.

Outputs JSON keyed by matchup with:
  - family-level logit + pp@.5
  - top 5 individual drivers
  - all sample-size proxies (bp_min, sp_min) for archetype callouts
  - lineup gaps, weather, news overrides, gate trail
"""
from __future__ import annotations
import json
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.build_pipeline import build_slate_frame
from mlb_edge.model import predict as mlb_predict

DAY = date(2026, 5, 3)

FAMILIES = {
    "SP_matchup":  ["f5_model_output"],
    "SP_luck":     ["home_sp_luck", "away_sp_luck"],
    "Offense":     ["team_wrcplus_gap", "team_woba_gap", "team_bbk_gap",
                    "team_hardhit_gap", "team_batter_run_value_gap",
                    "team_whiff_rate_gap", "team_blast_swing_gap",
                    "lineup_wrcplus_gap", "lineup_vs_sp_gap",
                    "lineup_hardhit_gap"],
    "Bullpen":     ["bullpen_siera_gap", "bullpen_fatigue_gap",
                    "bullpen_xwoba_gap", "bullpen_k_pct_gap",
                    "bullpen_bb_pct_gap", "bullpen_hardhit_gap"],
    "Park":        ["park_runs_factor", "park_hr_factor", "wind_dir_park",
                    "wind_out_mph", "temp_f", "humidity_pct", "precip_prob"],
    "Ump_Catcher": ["home_ump_boost", "away_ump_boost",
                    "home_catcher_penalty", "away_catcher_penalty"],
    "Defense":     ["team_oaa_gap", "team_frv_gap"],
    "Context":     ["is_divisional", "tz_diff", "is_opener",
                    "is_quick_turnaround", "is_day_game",
                    "dow_sin", "dow_cos", "home_roof_type",
                    "sp_sample_reliability", "sp_ttop3_penalty_gap"],
}


def main():
    games = build_slate_frame(DAY, include_weather=True)
    print(f"Built {len(games)} games")
    models = joblib.load(r"D:\mlb_edge\mlb_edge\models\latest.pkl")
    s1, s2 = models["stage1"], models["stage2"]
    games = mlb_predict(s1, s2, games)
    games["f5_model_output"] = games.get("f5_prob", games["model_prob"])
    X = games[s2.feature_cols].copy()
    booster = s2.booster.get_booster()
    dmat = xgb.DMatrix(X.values, feature_names=list(X.columns))
    contribs = booster.predict(dmat, pred_contribs=True)
    bias = contribs[:, -1]
    shap = contribs[:, :-1]
    feat_names = list(X.columns)
    feat_idx = {f: i for i, f in enumerate(feat_names)}

    out = {}
    for i, (_, g) in enumerate(games.iterrows()):
        matchup = f"{g['away_team']} @ {g['home_team']}"
        # Family decomposition
        family_summary = []
        for fam, cols in FAMILIES.items():
            present = [c for c in cols if c in feat_idx]
            if not present: continue
            fam_logit = float(sum(shap[i, feat_idx[c]] for c in present))
            family_summary.append({
                "family": fam, "logit": round(fam_logit, 4),
                "pp_at_50": round(fam_logit * 25.0, 2),
                "direction": (g["home_team"] if fam_logit > 0.02
                              else g["away_team"] if fam_logit < -0.02 else "—"),
            })
        # Top drivers
        idx_sorted = np.argsort(-np.abs(shap[i]))
        drivers = []
        for k in idx_sorted[:8]:
            feat = feat_names[k]
            lc = float(shap[i, k])
            if abs(lc) < 0.005: break
            raw = g.get(feat, np.nan)
            drivers.append({
                "logit": round(lc, 4), "feature": feat,
                "raw": (round(float(raw), 3) if pd.notna(raw) else None),
                "favors": g["home_team"] if lc > 0 else g["away_team"],
            })

        # Per-game context
        out[matchup] = {
            "home_team": g["home_team"], "away_team": g["away_team"],
            "model_prob": float(g["model_prob"]),
            "f5_prob": float(g.get("f5_prob", float("nan"))),
            "home_sp_n_pitches": float(g.get("home_sp_n_pitches", float("nan"))),
            "away_sp_n_pitches": float(g.get("away_sp_n_pitches", float("nan"))),
            "home_bullpen_n_pitches": float(g.get("home_bullpen_n_pitches", float("nan"))),
            "away_bullpen_n_pitches": float(g.get("away_bullpen_n_pitches", float("nan"))),
            "home_hl_bullpen_n_pitches": float(g.get("home_hl_bullpen_n_pitches", float("nan"))),
            "away_hl_bullpen_n_pitches": float(g.get("away_hl_bullpen_n_pitches", float("nan"))),
            "lineup_wrcplus_gap": float(g.get("lineup_wrcplus_gap", float("nan"))),
            "lineup_vs_sp_gap": float(g.get("lineup_vs_sp_gap", float("nan"))),
            "lineup_hardhit_gap": float(g.get("lineup_hardhit_gap", float("nan"))),
            "bullpen_siera_gap": float(g.get("bullpen_siera_gap", float("nan"))),
            "bullpen_fatigue_gap": float(g.get("bullpen_fatigue_gap", float("nan"))),
            "bullpen_xwoba_gap": float(g.get("bullpen_xwoba_gap", float("nan"))),
            "temp_f": float(g.get("temp_f", float("nan"))),
            "wind_out_mph": float(g.get("wind_out_mph", float("nan"))),
            "wind_dir_park": float(g.get("wind_dir_park", float("nan"))),
            "humidity_pct": float(g.get("humidity_pct", float("nan"))),
            "precip_prob": float(g.get("precip_prob", float("nan"))),
            "park_runs_factor": float(g.get("park_runs_factor", float("nan"))),
            "park_hr_factor": float(g.get("park_hr_factor", float("nan"))),
            "home_roof_type": float(g.get("home_roof_type", float("nan"))),
            "bias": float(bias[i]),
            "families": family_summary,
            "drivers": drivers,
        }
    Path(r"D:\mlb_edge\shap_audit_2026-05-03.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote shap_audit_2026-05-03.json ({len(out)} games)")


if __name__ == "__main__":
    main()
