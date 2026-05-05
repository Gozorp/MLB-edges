"""
compare_today_vs_external.py
----------------------------
After the v6/v7 model retrains, run:
    python -m mlb_edge.main --mode predict --date 2026-04-22 \
           --model_path models/latest.pkl --out picks_today.csv

then run this script to diff our picks against:
  - FanDuel/numberFire implied win probabilities
  - Dimers implied win probabilities
  - The actual outcomes (where the game has concluded)

External data is loaded from today_external_predictions.csv alongside.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _american_to_implied(p):
    if pd.isna(p):
        return np.nan
    p = float(p)
    return (-p) / (-p + 100) if p < 0 else 100.0 / (p + 100.0)


def main():
    picks_path = sys.argv[1] if len(sys.argv) > 1 else "picks_today.csv"
    ext_path = "today_external_predictions.csv"

    if not Path(picks_path).exists():
        print(f"Picks file missing: {picks_path}")
        print(f"Run first: python -m mlb_edge.main --mode predict --date "
              f"2026-04-22 --model_path models/latest.pkl --out {picks_path}")
        sys.exit(1)
    if not Path(ext_path).exists():
        print(f"External file missing: {ext_path}")
        sys.exit(1)

    picks = pd.read_csv(picks_path)
    ext = pd.read_csv(ext_path)

    # Join on (home_team, away_team) — team codes match our model's schema.
    merged = picks.merge(
        ext, how="outer", on=["home_team", "away_team"],
        suffixes=("_model", "_ext"),
    )

    # Market implied (from home_ml in external table)
    merged["market_home_prob"] = merged["home_ml"].apply(_american_to_implied)

    # Our model's home prob
    if "model_prob" in merged.columns:
        merged["our_home_prob"] = merged["model_prob"]
    else:
        merged["our_home_prob"] = np.nan

    # Disagreement: |ours - external|
    merged["disagreement_vs_external_pp"] = (
        (merged["our_home_prob"] - merged["external_home_prob"]).abs() * 100
    )
    merged["disagreement_vs_market_pp"] = (
        (merged["our_home_prob"] - merged["market_home_prob"]).abs() * 100
    )

    show_cols = [
        "home_team", "away_team",
        "our_home_prob", "external_home_prob", "market_home_prob",
        "disagreement_vs_external_pp", "disagreement_vs_market_pp",
        "actual_home_win", "final_score",
    ]
    show_cols = [c for c in show_cols if c in merged.columns]
    with pd.option_context("display.max_columns", None,
                           "display.width", 160,
                           "display.float_format", lambda x: f"{x:.3f}"):
        print("\n=== Today vs external sources ===")
        print(merged[show_cols].to_string(index=False))

    # Agreement summary
    actual_cols = merged["actual_home_win"].notna()
    if actual_cols.any():
        done = merged[actual_cols].copy()
        print(f"\n=== Games with final outcomes: {len(done)} ===")
        if "our_home_prob" in done:
            done["our_pick_correct"] = (
                (done["our_home_prob"] > 0.5).astype(int)
                == done["actual_home_win"].astype(int)
            )
            ours = done["our_pick_correct"].sum()
            print(f"Our model correct: {ours} / {len(done)}")
        if "external_home_prob" in done:
            done["ext_pick_correct"] = (
                (done["external_home_prob"] > 0.5).astype(int)
                == done["actual_home_win"].astype(int)
            )
            ext_c = done["ext_pick_correct"].sum()
            print(f"External  correct: {ext_c} / {len(done)}")
        if "market_home_prob" in done:
            done["mkt_pick_correct"] = (
                (done["market_home_prob"] > 0.5).astype(int)
                == done["actual_home_win"].astype(int)
            )
            mkt_c = done["mkt_pick_correct"].sum()
            print(f"Market    correct: {mkt_c} / {len(done)}")


if __name__ == "__main__":
    main()
