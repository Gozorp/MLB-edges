"""
predict_blended.py
------------------
Score a slate twice (raw + MLB-API-filled) and return a blended probability.

Why blend? The 2026 backtest (backtest_fill_2026.py, 417 games) showed:

  - Pure RAW (NaN-tolerant XGBoost defaults): Brier 0.251, Acc 51.6%.
  - Pure FILLED (fallback at 100% strength):  Brier 0.254, Acc 53.2%.
    -> Picks more right sides but over-shoots confidence.
  - BLENDED at alpha=0.35 (≈ 65% raw + 35% fill):
    Brier 0.2500, Acc 53.7% on the full slate.
    On the 213 patched games: Brier 0.2456 (best point on the curve),
    Acc 56.3% — strictly dominates raw on every metric.

The minimum of the Brier curve is flat between alpha ∈ [0.3, 0.5], so 0.35
is a safe midpoint. Higher alpha (→ 0.6-0.7) squeezes a bit more accuracy
at the cost of a small Brier regression; lower alpha barely moves either
metric. 0.35 is the Brier-minimum rounded toward the accuracy side.

Pipeline:
  1. Build slate frame.
  2. Apply fill_one_game to produce a filled frame.
  3. model.predict on both.
  4. blended_prob = (1 - alpha) * raw_prob + alpha * filled_prob.
  5. Write a CSV with raw / filled / blended side by side.

Usage:
    python predict_blended.py --date 2026-04-23
    python predict_blended.py --date 2026-04-23 --alpha 0.35
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from mlb_edge import build_pipeline as bp
from mlb_edge import data_ingestion as di
from mlb_edge import model as md
from mlb_edge import point_in_time as pit
from fill_slate import fill_one_game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--model_path", default="models/latest.pkl")
    ap.add_argument("--alpha", type=float, default=0.35,
                    help="Fill weight at prediction-level. 0.0 = raw only, "
                         "1.0 = fill only. Backtested optimum (2026 OOS, "
                         "417 games): 0.3-0.5 range, default 0.35.")
    ap.add_argument("--out", default="picks_blended.csv")
    args = ap.parse_args()

    stage1, stage2 = md.load(args.model_path)

    # -----------------------------------------------------------------
    # 1. Build slate and fill it.
    # -----------------------------------------------------------------
    print(f"Building slate for {args.date}...")
    games = bp.build_slate_frame(args.date)
    if games.empty:
        print("No slate. Abort.")
        return
    print(f"  {len(games)} games")

    print("Loading schedule for SP IDs...")
    schedule = di.fetch_schedule_mlb_api(args.date)
    sched_by_pk = {g["game_pk"]: g for g in schedule}

    print("Pulling Statcast for fill lookups...")
    sc = di.fetch_ytd_statcast(args.date - timedelta(days=1))
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    starters_by_team = pit.infer_starters_by_team(sc)

    filled = games.copy()
    n_patched = 0
    for idx, row in games.iterrows():
        meta = sched_by_pk.get(int(row["game_id"]), {})
        patches, audit = fill_one_game(
            row=row,
            sc=sc,
            starters_by_team=starters_by_team,
            home_sp_id=meta.get("home_sp_id"),
            away_sp_id=meta.get("away_sp_id"),
            game_date=pd.Timestamp(args.date),
            home_sp_name=meta.get("home_sp_name", "?"),
            away_sp_name=meta.get("away_sp_name", "?"),
        )
        for col, val in patches.items():
            filled.at[idx, col] = val
        if patches:
            n_patched += 1
    print(f"  patched {n_patched}/{len(games)} games")
    print()

    # -----------------------------------------------------------------
    # 2. Score both and blend.
    # -----------------------------------------------------------------
    print(f"Scoring raw + filled, blending at alpha={args.alpha}...")
    raw_preds = md.predict(stage1, stage2, games)
    fill_preds = md.predict(stage1, stage2, filled)

    alpha = args.alpha
    blended = (1 - alpha) * raw_preds["model_prob"].to_numpy(dtype=float) \
            + alpha * fill_preds["model_prob"].to_numpy(dtype=float)
    blended_f5 = (1 - alpha) * raw_preds["f5_prob"].to_numpy(dtype=float) \
               + alpha * fill_preds["f5_prob"].to_numpy(dtype=float)

    out = raw_preds[["game_id", "home_team", "away_team"]].copy()
    out["raw_prob"] = raw_preds["model_prob"].round(3).values
    out["fill_prob"] = fill_preds["model_prob"].round(3).values
    out["blend_prob"] = np.round(blended, 3)
    out["raw_f5"] = raw_preds["f5_prob"].round(3).values
    out["fill_f5"] = fill_preds["f5_prob"].round(3).values
    out["blend_f5"] = np.round(blended_f5, 3)

    def _pick(p, h, a):
        return h if p >= 0.5 else a
    out["raw_pick"]   = out.apply(lambda r: _pick(r.raw_prob, r.home_team, r.away_team), axis=1)
    out["fill_pick"]  = out.apply(lambda r: _pick(r.fill_prob, r.home_team, r.away_team), axis=1)
    out["blend_pick"] = out.apply(lambda r: _pick(r.blend_prob, r.home_team, r.away_team), axis=1)
    out["blend_confidence"] = np.maximum(out.blend_prob, 1 - out.blend_prob).round(3)

    # Sort by descending blended confidence for easy reading.
    out = out.sort_values("blend_confidence", ascending=False).reset_index(drop=True)

    out.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")
    print()

    show = out[["away_team", "home_team", "raw_pick", "raw_prob",
                "fill_pick", "fill_prob", "blend_pick", "blend_prob",
                "blend_confidence"]].copy()
    show.columns = ["away", "home", "raw_pick", "raw_p",
                    "fill_pick", "fill_p", "blend_pick", "blend_p", "conf"]
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
