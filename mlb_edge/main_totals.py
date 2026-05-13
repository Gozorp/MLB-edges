"""
main_totals.py
--------------
CLI for totals (over/under) model — supports three modes:

    backtest  — walk-forward simulate a season against cached real book odds
    train     — fit the totals model on 2023+2024+2025 and save to disk
    predict   — fetch today/tomorrow's slate + live totals odds, output picks

Usage:
    python -m mlb_edge.main_totals --mode backtest --season 2025 --out bt_totals_2025.csv
    python -m mlb_edge.main_totals --mode train --seasons 2023,2024,2025 --save models/totals_latest.pkl
    python -m mlb_edge.main_totals --mode predict --date 2026-04-22 --out picks_totals_today.csv

Predict mode requires a saved model from train mode first.

Zero new API cost for backtest/train. Predict mode costs 1 API request for
live totals odds.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


def _load_dotenv() -> None:
    """Tiny zero-dependency .env loader. Pushes KEY=VALUE lines from the
    repo-root ``.env`` into os.environ unless already set in the real shell.

    Mirrors predict.py's loader so that ``python -m mlb_edge.main_totals``
    sees ODDS_API_KEY when the CI workflow writes it to .env. Without this
    the totals predict step silently returns no odds and produces no CSV.
    """
    # main_totals.py lives at <repo>/mlb_edge/main_totals.py — repo root is parents[1]
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env at import time so any code path through main_totals (predict,
# backtest, train) that needs ODDS_API_KEY finds it.
_load_dotenv()

import numpy as np
import pandas as pd

from . import backtest_totals as bt_totals
from . import build_pipeline as bp
from . import live_totals
from . import odds_totals
from .backtest_totals import (
    TOTALS_KELLY_FRACTION, TOTALS_MAX_DAILY_RISK_UNITS,
    TOTALS_MIN_EDGE_RUNS, TOTALS_MAX_DECIMAL, TOTALS_MIN_DECIMAL,
    choose_side,
)
from .edge_calculator import kelly_stake
from .market_analysis import shin
from .model import F5_FEATURES
from .model_totals import (
    train_stage1_totals, train_stage2_totals, walkforward_totals_predict,
    save_totals, load_totals,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.totals")


# ===========================================================================
# BACKTEST MODE
# ===========================================================================
def run_backtest(season: int, through: Optional[date], out: Optional[str],
                 bankroll: float) -> None:
    log.info("=== TOTALS BACKTEST: season=%d through=%s ===", season, through)

    games = bp.build_historical_frame(season, through=through)
    if games.empty:
        log.error("Feature frame empty")
        return
    log.info("Feature frame: %d games", len(games))

    games = bt_totals.enrich_scores(games, season, through)
    games = games.dropna(subset=["home_score", "away_score",
                                 "home_f5_score", "away_f5_score"]).copy()
    log.info("Games with scores: %d", len(games))

    totals_odds = odds_totals.build_totals_frame(season, through=through)
    if totals_odds.empty:
        log.error("No totals odds in cache")
        return
    log.info("Totals odds rows: %d", len(totals_odds))

    games_with = odds_totals.merge_games_and_totals(games, totals_odds)
    games_with = games_with.dropna(subset=["total_line", "over_decimal",
                                           "under_decimal"]).copy()
    log.info("Games with totals line: %d", len(games_with))
    if games_with.empty:
        return

    log.info("Walk-forward fitting totals model...")
    preds = walkforward_totals_predict(games_with, n_splits=5)
    if preds.empty:
        log.error("Walk-forward produced no predictions")
        return

    result = bt_totals.simulate_totals_roi(preds, start_bankroll=bankroll)

    print("\n" + "=" * 60)
    print(f"TOTALS BACKTEST - season {season}"
          f"{f' through {through}' if through else ''}")
    print("=" * 60)
    for k, v in result.summary.items():
        if k == "by_side":
            print(f"  by_side:")
            n_dict = v.get('n', {})
            for side_name, n in n_dict.items():
                w = v.get('w', {}).get(side_name, 0)
                l = v.get('l', {}).get(side_name, 0)
                p = v.get('p', {}).get(side_name, 0)
                pnl = v.get('pnl', {}).get(side_name, 0.0)
                stake = v.get('stake', {}).get(side_name, 0.0)
                roi = (pnl / stake * 100) if stake > 0 else 0.0
                print(f"    {side_name:6s}: n={n}  w/l/p={w}/{l}/{p}  "
                      f"pnl={pnl:+.2f}  roi={roi:+.2f}%")
        elif isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    print("=" * 60 + "\n")

    if out and not result.bets.empty:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        result.bets.to_csv(out, index=False)
        log.info("Wrote %d totals bets to %s", len(result.bets), out)


# ===========================================================================
# TRAIN MODE
# ===========================================================================
def run_train(seasons: List[int], save_path: str,
              through: Optional[date] = None) -> None:
    log.info("=== TOTALS TRAIN: seasons=%s ===", seasons)

    frames = []
    for s in seasons:
        thru = through if s == max(seasons) else None
        f = bp.build_historical_frame(s, through=thru)
        if f.empty:
            log.warning("No feature frame for season %d — skipping", s)
            continue
        f = bt_totals.enrich_scores(f, s, thru)
        frames.append(f)

    if not frames:
        log.error("No training data")
        return

    df = pd.concat(frames, ignore_index=True).sort_values("game_date")
    df = df.dropna(subset=["home_score", "away_score",
                           "home_f5_score", "away_f5_score"]).copy()
    log.info("Totals training frame: %d games", len(df))

    log.info("Training Stage 1 (F5 runs)...")
    stage1 = train_stage1_totals(df)
    log.info("  Stage 1 MAE=%.3f RMSE=%.3f (target mean=%.2f)",
             stage1.metadata["train_mae"],
             stage1.metadata["train_rmse"],
             stage1.metadata["target_mean"])

    log.info("Training Stage 2 (full-game runs)...")
    stage2 = train_stage2_totals(df, stage1)
    log.info("  Stage 2 MAE=%.3f RMSE=%.3f (target mean=%.2f)",
             stage2.metadata["train_mae"],
             stage2.metadata["train_rmse"],
             stage2.metadata["target_mean"])

    save_totals(stage1, stage2, save_path)
    log.info("Saved totals bundle to %s", save_path)


# ===========================================================================
# PREDICT MODE
# ===========================================================================
def run_predict(target_date: date, model_path: str, bankroll: float,
                out: Optional[str]) -> None:
    log.info("=== TOTALS PREDICT: %s ===", target_date)

    try:
        stage1, stage2 = load_totals(model_path)
        log.info("Loaded totals model from %s", model_path)
    except FileNotFoundError:
        log.error("No trained totals model at %s. Run --mode train first.",
                  model_path)
        return

    games = bp.build_slate_frame(target_date)
    if games.empty:
        log.error("No games on slate for %s", target_date)
        return
    log.info("Slate: %d games", len(games))

    raw = live_totals.fetch_live_totals_odds()
    if raw.empty:
        log.error("No live totals odds returned")
        return
    wide = live_totals.median_totals_by_game(raw)
    if wide.empty:
        log.error("No clean totals lines after aggregation")
        return
    log.info("Live totals lines available for %d games", len(wide))

    games["game_date_only"] = pd.to_datetime(games["game_date"]).dt.date
    joined = games.merge(
        wide,
        left_on=["home_team", "away_team", "game_date_only"],
        right_on=["home_team", "away_team", "commence_date"],
        how="inner",
    )
    if joined.empty:
        log.error("No games matched between slate and totals odds")
        return
    log.info("Matched %d slate games to totals lines", len(joined))

    # Stage 1 prediction
    s1_feats = [c for c in F5_FEATURES if c in joined.columns]
    joined["f5_runs_pred"] = stage1.booster.predict(joined[s1_feats].values)

    # Stage 2 prediction
    s2_feats_present = [c for c in stage2.feature_cols if c in joined.columns]
    missing = set(stage2.feature_cols) - set(s2_feats_present)
    if missing:
        log.warning("Stage 2 missing features in slate: %s — filling NaN",
                    missing)
        for col in missing:
            joined[col] = np.nan
    joined["total_runs_pred"] = stage2.booster.predict(
        joined[stage2.feature_cols].values
    )

    # Generate picks. Slate is ≤15 games/day so iterrows is acceptable here;
    # the hot paths are all in the backtest simulator, which is vectorized.
    picks = []
    total_risk = 0.0
    for _, r in joined.iterrows():
        line = r["total_line"]
        over_dec = r["over_decimal"]
        under_dec = r["under_decimal"]
        pred = r["total_runs_pred"]

        if (pd.isna(line) or pd.isna(over_dec) or pd.isna(under_dec)
                or pd.isna(pred)):
            continue
        if (over_dec < TOTALS_MIN_DECIMAL or over_dec > TOTALS_MAX_DECIMAL
                or under_dec < TOTALS_MIN_DECIMAL or under_dec > TOTALS_MAX_DECIMAL):
            continue

        chosen = choose_side(pred, line, over_dec, under_dec)
        if chosen is None:
            continue

        p_over_raw = 1.0 / over_dec
        p_under_raw = 1.0 / under_dec
        p_over_fair, p_under_fair = shin(p_over_raw, p_under_raw)
        if pd.isna(p_over_fair):
            continue

        edge_bump = min(0.02 * chosen["edge_runs"], 0.10)
        if chosen["side"] == "over":
            our_prob = min(max(p_over_fair + edge_bump, 0.01), 0.99)
            book_fair = p_over_fair
        else:
            our_prob = min(max(p_under_fair + edge_bump, 0.01), 0.99)
            book_fair = p_under_fair

        dec = chosen["decimal"]
        stake_frac = kelly_stake(our_prob, dec, fraction=TOTALS_KELLY_FRACTION)
        # Stake is expressed in units (percent-of-bankroll). The bankroll arg
        # is accepted for future $-rendering but the current contract is units.
        stake_units = stake_frac * 100

        if total_risk + stake_units > TOTALS_MAX_DAILY_RISK_UNITS:
            stake_units = max(0.0, TOTALS_MAX_DAILY_RISK_UNITS - total_risk)
            if stake_units <= 0:
                continue
        total_risk += stake_units

        picks.append({
            "game_date":     str(r["game_date_only"]),
            "home_team":     r["home_team"],
            "away_team":     r["away_team"],
            "total_line":    line,
            "pred_runs":     round(pred, 2),
            "edge_runs":     round(chosen["edge_runs"], 2),
            "side":          chosen["side"],
            "decimal":       round(dec, 3),
            "our_prob":      round(our_prob, 4),
            "book_fair":     round(book_fair, 4),
            "stake_units":   round(stake_units, 2),
        })

    if not picks:
        print(f"\nNo totals picks for {target_date} — no games passed the "
              f"edge threshold of {TOTALS_MIN_EDGE_RUNS} runs.\n")
        return

    picks_df = pd.DataFrame(picks)
    print(f"\n=== TOTALS PICKS — {target_date} ===")
    print(picks_df.to_string(index=False))
    print(f"\nTotal bets: {len(picks_df)}, Total risk: "
          f"{picks_df['stake_units'].sum():.2f} units\n")

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        picks_df.to_csv(out, index=False)
        log.info("Wrote picks to %s", out)


# ===========================================================================
# ARGPARSE
# ===========================================================================
def _parse_args(argv):
    p = argparse.ArgumentParser(description="Totals (over/under) CLI")
    p.add_argument("--mode", choices=["backtest", "train", "predict"],
                   default="backtest")
    p.add_argument("--season", type=int)
    p.add_argument("--through", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    p.add_argument("--bankroll", type=float, default=100.0)
    p.add_argument("--seasons", type=lambda s: [int(x) for x in s.split(",")],
                   help="Comma-separated, e.g. 2023,2024,2025")
    p.add_argument("--save", default="models/totals_latest.pkl")
    p.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="Target date (YYYY-MM-DD) for predict mode")
    p.add_argument("--model_path", default="models/totals_latest.pkl")
    p.add_argument("--out")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    if args.mode == "backtest":
        if args.season is None:
            print("--season is required for backtest mode")
            sys.exit(1)
        run_backtest(args.season, args.through, args.out, args.bankroll)
    elif args.mode == "train":
        if not args.seasons:
            print("--seasons is required for train mode (e.g. 2023,2024,2025)")
            sys.exit(1)
        run_train(args.seasons, args.save, args.through)
    elif args.mode == "predict":
        if args.date is None:
            print("--date is required for predict mode (e.g. 2026-04-22)")
            sys.exit(1)
        run_predict(args.date, args.model_path, args.bankroll, args.out)


if __name__ == "__main__":
    main()
