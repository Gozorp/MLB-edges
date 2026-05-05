"""
predict_v7v8_blend.py
---------------------
Predict tonight's slate using a fixed-alpha blend of v7-equivalent and
v8 probabilities.

Why: diagnose_v8_calibration.py on the 2026 OOS slate showed
  - v7 alone:      Brier 0.2514
  - v8 alone:      Brier 0.2535
  - blend(0.5):    Brier 0.2499   <-- best under no-test-set-fitting prior
  - blend(0.6):    Brier 0.2498   <-- near-flat minimum

v8's lineup features add resolution (more discriminative) but hurt
calibration in April when the YTD cascade often lands on tier-3/tier-4
fallbacks. Averaging with v7 (team-aggregate only) regularizes v8's
over-confidence on middle-band and dog slices.

alpha = 0.5 is the default: no test-set tuning, symmetric prior on the
two models. Pass --alpha on the CLI to override.

Pipeline:
  1. Build slate frame (uses whichever cache the current code is on — v5).
  2. Score with both models side by side.
  3. Blend: p_final = alpha * p_v7_equiv + (1 - alpha) * p_v8.
  4. Overwrite model_prob/model_prob_raw in the preds frame so
     recommend_slate sees the blended number.
  5. Fetch odds, run the edge filter, write picks.csv.

Usage:
    python predict_v7v8_blend.py --date 2026-04-24
    python predict_v7v8_blend.py --date 2026-04-24 --alpha 0.5 --bankroll 100 --out picks_blended.csv
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from mlb_edge import build_pipeline as bp
from mlb_edge import data_ingestion as di
from mlb_edge import model as md
from mlb_edge.edge_calculator import recommend_slate
from mlb_edge.stadiums import normalize_team


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
log = logging.getLogger("predict_blend")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--v8_path", default="models/latest.pkl",
                    help="v8 (lineup-aware) model")
    ap.add_argument("--v7_path", default="models/v7_equiv.pkl",
                    help="v7-equivalent (no-lineup) model")
    ap.add_argument("--alpha", type=float, default=0.5,
                    help="Weight on v7 in blend. 0.0 = v8 only, 1.0 = v7 only. "
                         "Default 0.5 (no-overfit prior, near-optimal on 2026 OOS).")
    ap.add_argument("--bankroll", type=float, default=100.0)
    ap.add_argument("--out", default="picks.csv")
    args = ap.parse_args()

    log.info("=== PREDICT-BLEND: %s  alpha=%.2f ===", args.date, args.alpha)

    # -----------------------------------------------------------------
    # 1. Build slate
    # -----------------------------------------------------------------
    games = bp.build_slate_frame(args.date)
    if games.empty:
        log.error("No games on slate for %s", args.date)
        return
    log.info("Slate: %d games", len(games))

    # -----------------------------------------------------------------
    # 2. Score with BOTH models
    # -----------------------------------------------------------------
    log.info("Scoring with v8 (%s)...", args.v8_path)
    s1_v8, s2_v8 = md.load(args.v8_path)
    preds_v8 = md.predict(s1_v8, s2_v8, games)

    log.info("Scoring with v7-equiv (%s)...", args.v7_path)
    s1_v7, s2_v7 = md.load(args.v7_path)
    preds_v7 = md.predict(s1_v7, s2_v7, games)

    # -----------------------------------------------------------------
    # 3. Blend: replace model_prob so the downstream pipeline (filter,
    #    conviction, sizing) uses the blended number. Keep per-model
    #    columns for audit.
    # -----------------------------------------------------------------
    preds = preds_v8.copy()
    preds["model_prob_v8"]  = preds_v8["model_prob"]
    preds["model_prob_v7"]  = preds_v7["model_prob"]
    preds["model_prob"]     = (args.alpha * preds_v7["model_prob"]
                               + (1 - args.alpha) * preds_v8["model_prob"])
    preds["model_prob_raw"] = preds["model_prob"]  # no post-blend calibration
    # Stage-1 (F5) from v8 — F5 is SP-only and identical across the two models
    # in terms of features, but the trained boosters are fit on different
    # inner OOF sets, so pick one. v8's is freshest on the v5 cache.
    # preds already has f5_prob from v8; nothing to do.

    # -----------------------------------------------------------------
    # 4. Odds + filter
    # -----------------------------------------------------------------
    client = di.OddsClient()
    odds = client.current_lines()
    if odds.empty:
        log.error("No live odds returned")
        return
    odds["outcome"] = odds["outcome"].apply(normalize_team)

    sheet = recommend_slate(preds, odds, bankroll=args.bankroll)

    if sheet.empty:
        print("\nNo bets pass the filter for this slate.")
        return

    print("\n=== BET SHEET (v7/v8 blend, alpha={:.2f}) ===".format(args.alpha))
    print(sheet.to_string(index=False))
    print(f"\nTotal bets: {len(sheet)}, "
          f"Total risk: {sheet['stake_u'].sum():.2f} units")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        sheet.to_csv(args.out, index=False)
        log.info("Wrote picks to %s", args.out)


if __name__ == "__main__":
    main()
