"""
main_f5.py
----------
CLI entrypoint for F5 (first-5-innings) backtesting.

Usage:
    python -m mlb_edge.main_f5 --mode backtest --season 2025 --out bt_f5_2025.csv
    python -m mlb_edge.main_f5 --mode backtest --season 2024 --out bt_f5_2024.csv
    python -m mlb_edge.main_f5 --mode backtest --season 2023 --out bt_f5_2023.csv
    python -m mlb_edge.main_f5 --mode backtest --season 2026 --through 2026-04-19 --out bt_f5_2026_ytd.csv

Reuses feature caches from the full-game pipeline (`data/feature_cache`), so
if you've already run the full-game backtest for a season, the F5 run only
needs to fetch F5 odds (new API requests) and train Stage 1 — typically
~2 minutes per season.
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
from . import build_pipeline as bp
from . import odds_f5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.f5")


def run_backtest(season: int, through: Optional[date],
                 out: Optional[str], bankroll: float) -> None:
    log.info("=== F5 BACKTEST: season=%d through=%s ===", season, through)

    # Reuse existing feature frame (same cache as full-game)
    games = bp.build_historical_frame(season, through=through)
    if games.empty:
        log.error("Feature frame empty")
        return

    f5_odds = odds_f5.build_f5_odds_frame(season, through=through)
    if f5_odds.empty:
        log.error("F5 odds frame empty — check ODDS_API_KEY and quota")
        return

    games_with = odds_f5.merge_games_and_f5_odds(games, f5_odds)
    matched = games_with["home_f5_decimal"].notna().sum()
    log.info("Games with matched F5 odds: %d / %d", matched, len(games_with))
    games_with = games_with[games_with["home_f5_decimal"].notna()].copy()

    if games_with.empty:
        log.error("No games matched F5 odds")
        return

    # Drop rows missing the F5 target (rain-shortened games, ties, etc.).
    # Now effective because `home_f5_win` is nullable in build_pipeline.
    games_with = games_with.dropna(subset=["home_f5_win"]).copy()

    log.info("Walk-forward fitting Stage 1...")
    preds = btf5.walkforward_f5_predict(games_with, n_splits=5)
    if preds.empty:
        log.error("Walk-forward produced no predictions")
        return

    log.info("Simulating F5 ROI...")
    result = btf5.simulate_f5_roi(preds, start_bankroll=bankroll)

    print("\n" + "=" * 60)
    print(f"F5 BACKTEST SUMMARY — season {season}"
          f"{f' through {through}' if through else ''}")
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
        log.info("Wrote %d F5 bets to %s", len(result.bets), out)


def _parse_args(argv):
    p = argparse.ArgumentParser(description="F5 moneyline backtester")
    p.add_argument("--mode", choices=["backtest"], required=True)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--through", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--out")
    p.add_argument("--bankroll", type=float, default=100.0)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    run_backtest(args.season, args.through, args.out, args.bankroll)


if __name__ == "__main__":
    main()
