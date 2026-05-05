"""
main_f5_synthetic.py
--------------------
CLI for synthetic-odds F5 backtesting.

Usage:
    python -m mlb_edge.main_f5_synthetic --season 2025 --out bt_f5s_2025.csv
    python -m mlb_edge.main_f5_synthetic --season 2024 --out bt_f5s_2024.csv
    python -m mlb_edge.main_f5_synthetic --season 2023 --out bt_f5s_2023.csv

No new API calls. Reuses:
  - data/feature_cache/   (from prior full-game runs)
  - data/odds_cache/      (full-game historical odds, also from prior runs)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from . import backtest_f5 as btf5
from . import backtest_f5_synthetic as btf5s
from . import build_pipeline as bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.f5s")


def run(season: int, through: Optional[date], out: Optional[str],
        bankroll: float, shrinkage: float, f5_vig: float) -> None:
    log.info("=== SYNTHETIC F5 BACKTEST: season=%d through=%s ===", season, through)
    log.info("    shrinkage=%.2f, f5_vig=%.3f", shrinkage, f5_vig)

    # Reuse feature cache (built during full-game runs)
    games = bp.build_historical_frame(season, through=through)
    if games.empty:
        log.error("Feature frame empty — run full-game backtest first to build cache")
        return

    # Reuse full-game odds (already fetched)
    full_odds = bp.build_odds_frame(season, through=through)
    if full_odds.empty:
        log.error("Full-game odds empty")
        return

    # Join full-game odds onto games (same function as full-game backtest)
    games_with = bp.merge_games_and_odds(games, full_odds)
    matched = games_with["home_decimal"].notna().sum()
    log.info("Games with full-game odds: %d / %d", matched, len(games_with))
    games_with = games_with[games_with["home_decimal"].notna()].copy()

    # Synthesize F5 odds from full-game odds
    games_with = btf5s.add_synthetic_f5_odds(games_with, shrinkage=shrinkage,
                                             f5_vig=f5_vig)
    games_with = games_with.dropna(subset=["home_f5_decimal", "home_f5_win"])
    log.info("Games with synthetic F5 odds + F5 targets: %d", len(games_with))

    if games_with.empty:
        log.error("Nothing to backtest")
        return

    log.info("Walk-forward fitting Stage 1...")
    preds = btf5.walkforward_f5_predict(games_with, n_splits=5)
    if preds.empty:
        log.error("Walk-forward produced no predictions")
        return

    # Predictions come out of walk-forward with the valid-fold columns only.
    # We need to re-attach home_f5_decimal / away_f5_decimal for the simulator.
    preds = preds.merge(
        games_with[["game_id", "home_f5_decimal", "away_f5_decimal"]],
        on="game_id", how="left", suffixes=("", "_dup")
    )
    # Drop any "_dup" columns that may have been created
    for c in list(preds.columns):
        if c.endswith("_dup"):
            preds = preds.drop(columns=[c])

    log.info("Simulating synthetic F5 ROI...")
    result = btf5s.simulate_f5_roi_synthetic(preds, start_bankroll=bankroll)

    print("\n" + "=" * 60)
    print(f"SYNTHETIC F5 BACKTEST — season {season}"
          f"{f' through {through}' if through else ''}")
    print(f"   shrinkage={shrinkage:.2f}, f5_vig={f5_vig:.3f}")
    print("=" * 60)
    for k, v in result.summary.items():
        if k == "by_tier":
            print(f"  by_tier: {v.get('n', {})}")
        elif isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    print("=" * 60 + "\n")

    if out and not result.bets.empty:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        result.bets.to_csv(out, index=False)
        log.info("Wrote %d bets to %s", len(result.bets), out)


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Synthetic-odds F5 backtester")
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--through", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--out")
    p.add_argument("--bankroll", type=float, default=100.0)
    p.add_argument("--shrinkage", type=float, default=0.25,
                   help="How much to pull F5 probability toward 0.5 (0..1, default 0.25)")
    p.add_argument("--f5_vig", type=float, default=0.06,
                   help="Target F5 market vig (default 0.06 = 6%)")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    run(args.season, args.through, args.out, args.bankroll,
        args.shrinkage, args.f5_vig)


if __name__ == "__main__":
    main()
