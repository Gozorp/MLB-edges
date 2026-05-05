"""
analyze_slate.py
----------------
Turn each game's Stage 2 prediction into an analytical verdict by decomposing
the model's output into feature-family contributions via XGBoost SHAP values.

Per game it prints:
  - Matchup, final pick, pick probability, dominance tier
  - Signed logit contribution from each feature family
    (SP matchup, SP luck, Offense, Bullpen, Park, Ump/Catcher, Context)
    plus the approximate percentage-point lean each family adds toward home
  - Top 3 individual feature drivers (name, raw value, logit contribution)

Sign convention throughout: POSITIVE = favors home team, NEGATIVE = favors away.
Logit contributions sum (+ bias) to the Stage 2 logit; sigmoid(that) = prob.

Usage:
    python analyze_slate.py --date 2026-04-23
    python analyze_slate.py --date 2026-04-23 --top_drivers 5
"""
from __future__ import annotations

import argparse
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd
import xgboost as xgb

from mlb_edge import build_pipeline as bp
from mlb_edge import model as md


# ---------------------------------------------------------------------------
# Feature-family groupings
# ---------------------------------------------------------------------------
# Each family is a semantic cluster of Stage 2 inputs. Keeps the output
# readable — 19 individual SHAP numbers become 7 family scores.
FAMILIES: Dict[str, List[str]] = {
    "SP_matchup":  ["f5_model_output"],                 # Stage 1 distillation
    "SP_luck":     ["home_sp_luck", "away_sp_luck"],    # regression-to-mean
    "Offense":     ["team_wrcplus_gap", "team_woba_gap",
                    "team_bbk_gap", "team_hardhit_gap"],
    "Bullpen":     ["bullpen_siera_gap", "bullpen_fatigue_gap"],
    "Park":        ["park_runs_factor", "park_hr_factor"],
    "Ump_Catcher": ["home_ump_boost", "away_ump_boost",
                    "home_catcher_penalty", "away_catcher_penalty"],
    "Context":     ["is_divisional", "tz_diff", "is_opener",
                    "is_quick_turnaround"],
}


def _tier(prob: float) -> str:
    """Dominance tier based on absolute distance from 50-50."""
    lean = abs(prob - 0.5)
    if lean > 0.15:
        return "STRONG"
    if lean > 0.08:
        return "MODERATE"
    if lean > 0.03:
        return "LEAN"
    return "COIN_FLIP"


def _logit_to_pp_at_half(logit: float) -> float:
    """Convert a logit contribution to approximate pp-impact at p=0.5.
    sigmoid'(0) = 0.25, so 1 logit ≈ 25pp near the midpoint."""
    return logit * 25.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--model_path", default="models/latest.pkl")
    ap.add_argument("--top_drivers", type=int, default=3,
                    help="How many individual feature drivers to print per game.")
    ap.add_argument("--slate_path", default=None,
                    help="Optional pre-built slate parquet "
                         "(e.g. the output of fill_slate.py). When set, "
                         "skips bp.build_slate_frame — useful for analyzing "
                         "the NaN-filled variant alongside the raw one.")
    args = ap.parse_args()

    stage1, stage2 = md.load(args.model_path)
    if args.slate_path:
        import pandas as _pd
        games = _pd.read_parquet(args.slate_path)
        print(f"Loaded pre-built slate from {args.slate_path} "
              f"({len(games)} games)")
    else:
        games = bp.build_slate_frame(args.date)
    if games.empty:
        print(f"No slate for {args.date}")
        return

    # Build the exact Stage 2 input frame the model would see at inference.
    games = games.copy()
    games["f5_prob"] = stage1.booster.predict_proba(
        games[stage1.feature_cols].values
    )[:, 1]
    games["f5_model_output"] = games["f5_prob"]
    X = games[stage2.feature_cols].copy()

    # SHAP contributions via XGBoost's native pred_contribs. Shape is
    # (n_games, n_features + 1); the last column is the model's global bias.
    booster = stage2.booster.get_booster()
    dmat = xgb.DMatrix(X.values, feature_names=list(X.columns))
    contribs = booster.predict(dmat, pred_contribs=True)
    bias = contribs[:, -1]
    shap = contribs[:, :-1]
    feat_names = list(X.columns)
    feat_idx = {f: i for i, f in enumerate(feat_names)}

    # Final Stage 2 prob — match what predict() produces (raw, since
    # calibration is disabled in config).
    probs = stage2.booster.predict_proba(X.values)[:, 1]

    # Probability pick and confidence tier, plus sanity check that
    # sigmoid(sum(shap) + bias) ≈ prob.
    for i, (_, row) in enumerate(games.iterrows()):
        home, away = row["home_team"], row["away_team"]
        p = float(probs[i])
        pick = home if p >= 0.5 else away
        pick_prob = p if p >= 0.5 else 1 - p
        logit_total = shap[i].sum() + bias[i]
        sanity = 1.0 / (1.0 + np.exp(-logit_total))

        print("=" * 78)
        print(f"  {away} @ {home}   ->  PICK: {pick} ({pick_prob:.3f})"
              f"   [{_tier(pick_prob)}]")
        print(f"  Sanity: sigmoid(logit) = {sanity:.4f}   model prob = {p:.4f}")
        print("=" * 78)

        # Family breakdown
        print(f"  {'Family':<14s} {'logit':>8s}  {'pp@0.5':>8s}  direction")
        print(f"  {'-'*14} {'-'*8}  {'-'*8}  ---------")
        family_rows = []
        for fam, cols in FAMILIES.items():
            fam_cols_present = [c for c in cols if c in feat_idx]
            if not fam_cols_present:
                continue
            fam_logit = float(sum(shap[i, feat_idx[c]] for c in fam_cols_present))
            pp_half = _logit_to_pp_at_half(fam_logit)
            direction = (home if fam_logit > 0.02 else
                         away if fam_logit < -0.02 else "-")
            family_rows.append((fam, fam_logit, pp_half, direction))
            print(f"  {fam:<14s} {fam_logit:+8.3f}  {pp_half:+7.1f}pp   "
                  f"{direction}")
        total_logit = sum(r[1] for r in family_rows)
        print(f"  {'-'*14} {'-'*8}  {'-'*8}")
        print(f"  {'Net (ex-bias)':<14s} {total_logit:+8.3f}  "
              f"{_logit_to_pp_at_half(total_logit):+7.1f}pp   "
              f"{'bias =' + f' {bias[i]:+.3f}'}")
        print()

        # Top individual drivers by |logit contribution|
        print(f"  Top {args.top_drivers} individual drivers:")
        idx_sorted = np.argsort(-np.abs(shap[i]))
        shown = 0
        for k in idx_sorted:
            if shown >= args.top_drivers:
                break
            feat = feat_names[k]
            logit_c = float(shap[i, k])
            if abs(logit_c) < 0.005:
                break
            raw_val = row.get(feat, np.nan)
            direction = home if logit_c > 0 else away
            print(f"    {logit_c:+7.3f}  {feat:<28s} = "
                  f"{raw_val:>+7.3f}   (favors {direction})")
            shown += 1
        print()


if __name__ == "__main__":
    main()
