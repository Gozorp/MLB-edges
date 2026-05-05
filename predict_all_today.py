"""
predict_all_today.py
--------------------
Emit the model's home-team win probability for EVERY game on a given date,
not just the filtered bet sheet. We need this to compare model_prob vs
external sources (FanDuel/numberFire, Dimers) across all games, including
games where the model had no edge vs market.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from mlb_edge import build_pipeline as bp
from mlb_edge import model as md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--model_path", default="models/latest.pkl")
    ap.add_argument("--out", default="picks_all_today.csv")
    ap.add_argument("--slate_path", default=None,
                    help="Optional pre-built slate parquet (e.g. the output "
                         "of fill_slate.py). Skips bp.build_slate_frame so "
                         "you can score the NaN-filled variant.")
    args = ap.parse_args()

    stage1, stage2 = md.load(args.model_path)
    if args.slate_path:
        games = pd.read_parquet(args.slate_path)
        print(f"Loaded pre-built slate from {args.slate_path} ({len(games)} games)")
    else:
        games = bp.build_slate_frame(args.date)
    if games.empty:
        print(f"No slate for {args.date}")
        return

    preds = md.predict(stage1, stage2, games)

    # `md.predict` emits home-side probabilities. Expose explicit home/away
    # columns so the CSV is self-describing — no need to remember that the
    # bare `model_prob` column means "home wins".
    out_df = preds[["game_id", "home_team", "away_team"]].copy()
    out_df["home_f5_prob"]    = preds["f5_prob"]
    out_df["away_f5_prob"]    = 1.0 - preds["f5_prob"]
    out_df["home_prob_raw"]   = preds["model_prob_raw"]
    out_df["away_prob_raw"]   = 1.0 - preds["model_prob_raw"]
    out_df["home_prob"]       = preds["model_prob"]
    out_df["away_prob"]       = 1.0 - preds["model_prob"]
    out_df["model_pick"]      = out_df.apply(
        lambda r: r["home_team"] if r["home_prob"] >= 0.5 else r["away_team"],
        axis=1,
    )
    out_df["pick_prob"]       = out_df[["home_prob", "away_prob"]].max(axis=1)
    # F5 (starter-anchored) pick — useful when it disagrees with the full-game
    # pick, since that usually means the bullpen/offense features are pulling
    # the Stage 2 prediction off the pitching anchor.
    out_df["f5_pick"]         = out_df.apply(
        lambda r: r["home_team"] if r["home_f5_prob"] >= 0.5 else r["away_team"],
        axis=1,
    )
    out_df["f5_pick_prob"]    = out_df[["home_f5_prob", "away_f5_prob"]].max(axis=1)
    # Confidence band flag: HIGH if the filter's model-prob band [0.48,0.72]
    # contains the pick; OUT if the pick is so confident it falls outside
    # (which, per backtest, is where the model tends to be overfit).
    out_df["band"] = out_df["pick_prob"].apply(
        lambda p: "OK" if 0.48 <= p <= 0.72 else "OUT_OF_BAND"
    )

    # Sort by descending confidence for easy reading.
    out_df = out_df.sort_values("pick_prob", ascending=False).reset_index(drop=True)

    out_df.to_csv(args.out, index=False)
    print(f"Wrote {len(out_df)} games to {args.out}")
    print()

    # Human-readable console view: sorted, rounded, pick-focused, no raw cols.
    show = out_df[["away_team", "home_team",
                   "f5_pick", "f5_pick_prob",
                   "model_pick", "pick_prob",
                   "band"]].copy()
    show[["f5_pick_prob", "pick_prob"]] = \
        show[["f5_pick_prob", "pick_prob"]].round(3)
    show.columns = ["away", "home", "f5_pick", "f5_p",
                    "pick", "prob", "band"]
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
