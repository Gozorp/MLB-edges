"""
main.py
-------
CLI for the SP-anchored MLB edge engine.

Three modes:

    # Backtest a season (or part of one) against historical closing odds
    python -m mlb_edge.main --mode backtest --season 2025 --out bt_2025.csv
    python -m mlb_edge.main --mode backtest --season 2026 --through 2026-04-19 \\
           --out bt_2026_ytd.csv

    # Train a model on one or more seasons and save it
    python -m mlb_edge.main --mode train --seasons 2025,2026 --save models/latest.pkl \\
           --through 2026-04-19

    # Predict today's slate using a saved model
    python -m mlb_edge.main --mode predict --date 2026-04-20 \\
           --model_path models/latest.pkl --bankroll 100 --out picks.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from . import backtesting as bt
from . import build_pipeline as bp
from . import data_ingestion as di
from . import model as md
from .bullpen_fatigue_blocker import apply_bullpen_ceiling, compute_bullpen_workload
from .config import SP_WEIGHTS
from .edge_calculator import recommend_slate
from .sp_savant_gate import gate_sp_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge")


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------
def run_backtest(season: int, through: Optional[date],
                 out: Optional[str], bankroll: float) -> None:
    log.info("=== BACKTEST: season=%d through=%s ===", season, through)

    games = bp.build_historical_frame(season, through=through)
    if games.empty:
        log.error("Historical frame empty — check Statcast / weather")
        return

    odds = bp.build_odds_frame(season, through=through)
    if odds.empty:
        log.error("Historical odds empty — check ODDS_API_KEY and quota")
        return

    # Merge odds onto games; drop games with no matched market
    games_with_odds = bp.merge_games_and_odds(games, odds)
    n_matched = games_with_odds["home_decimal"].notna().sum()
    log.info("Games with matched odds: %d / %d", n_matched, len(games_with_odds))
    games_with_odds = games_with_odds[games_with_odds["home_decimal"].notna()].copy()

    if games_with_odds.empty:
        log.error("No games matched odds — team name normalization likely failing")
        return

    # Drop rows missing either training label. `home_f5_win` is now nullable
    # (ties-at-5 are pushes, not away-wins; see build_pipeline._game_outcomes),
    # and `_xy_split` calls `.astype(int)` which blows up on NaN. Dropping
    # here keeps the full-game backtest honest — Stage 1 fold-fits skip the
    # ambiguous rows instead of labeling them 0 (home loses F5).
    n_before = len(games_with_odds)
    games_with_odds = games_with_odds.dropna(
        subset=["home_win", "home_f5_win"]
    ).copy()
    n_drop = n_before - len(games_with_odds)
    if n_drop:
        log.info("Dropped %d rows missing home_win or home_f5_win "
                 "(ties-at-5, rain-shortened, etc.)", n_drop)

    # Convert to the long-format odds DF that backtesting expects
    long_odds = _wide_to_long_odds(games_with_odds)

    log.info("Fitting walk-forward models...")
    preds = bt.fit_and_predict_walk_forward(games_with_odds, n_splits=5)
    if preds.empty:
        log.error("Walk-forward produced no predictions")
        return

    log.info("Simulating ROI...")
    result = bt.simulate_roi(preds, long_odds, start_bankroll=bankroll)

    print("\n" + "=" * 60)
    print(f"BACKTEST SUMMARY — season {season}"
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
        log.info("Wrote %d bets to %s", len(result.bets), out)


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------
def run_train(seasons: List[int], through: Optional[date],
              save_path: str) -> None:
    log.info("=== TRAIN: seasons=%s through=%s ===", seasons, through)

    frames = []
    for s in seasons:
        f = bp.build_historical_frame(s, through=through if s == seasons[-1] else None)
        if not f.empty:
            frames.append(f)
    if not frames:
        log.error("No training data")
        return

    df = pd.concat(frames, ignore_index=True).sort_values("game_date")
    df = df.dropna(subset=["home_win", "home_f5_win"])
    log.info("Training frame: %d games", len(df))

    # Fit on all data (no holdout — for a held-out evaluation, use backtest mode)
    stage1 = md.train_stage1_f5(df)
    stage2 = md.train_stage2_full(df, stage1)

    log.info("Stage 1 train AUC: %.4f", stage1.metadata["train_auc"])
    log.info("Stage 2 train AUC: %.4f", stage2.metadata["train_auc"])

    md.save(stage1, stage2, save_path)
    log.info("Saved models to %s", save_path)


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------
def run_predict(day: date, model_path: str, bankroll: float,
                out: Optional[str]) -> None:
    log.info("=== PREDICT: %s ===", day)

    stage1, stage2 = md.load(model_path)
    log.info("Loaded models from %s", model_path)

    games = bp.build_slate_frame(day)
    if games.empty:
        log.error("No games on slate for %s", day)
        return

    preds = md.predict(stage1, stage2, games)

    # v5.1: strict Statcast NaN/sample gate. Neutralizes sp_xera_gap (and
    # team_woba_gap on hard-veto) for rows where the SP sample is too small
    # or critical Statcast columns are corrupt — closes the 2026-04-25 BAL
    # 17-1 failure mode at the feature layer before conviction sees the row.
    preds = gate_sp_features(preds)

    client = di.OddsClient()
    odds = client.current_lines()
    if odds.empty:
        log.error("No live odds returned")
        return
    # Normalize odds team names
    from .stadiums import normalize_team
    odds["outcome"] = odds["outcome"].apply(normalize_team)

    sheet = recommend_slate(preds, odds, bankroll=bankroll)

    # v5.1: 72h high-leverage bullpen workload ceiling. Optional — only runs
    # if a recent pitch log is available on disk. Loaded lazily so backtests
    # and predict-without-pitch-log don't break.
    pitch_log_path = Path("data/pitch_logs/recent_72h.parquet")
    if not sheet.empty and pitch_log_path.exists():
        try:
            pitch_log = pd.read_parquet(pitch_log_path)
            workload = compute_bullpen_workload(
                pitch_log, slate_date=pd.Timestamp(day)
            )
            sheet_for_ceiling = sheet.rename(
                columns={"team": "pick_winner", "tier": "conv_tier"}
            ).copy()
            sheet_for_ceiling["home_team"] = ""
            sheet_for_ceiling["away_team"] = ""
            capped = apply_bullpen_ceiling(sheet_for_ceiling, workload)
            sheet["tier"] = capped["conv_tier_v51"].values
            demoted = capped[capped["bullpen_demote_reason"] != ""]
            if not demoted.empty:
                log.info(
                    "Bullpen ceiling demoted %d picks: %s",
                    len(demoted),
                    list(demoted["pick_winner"]),
                )
        except Exception as e:
            log.warning("Bullpen ceiling skipped: %s", e)

    if sheet.empty:
        print("\nNo bets pass the filter for this slate.")
        return

    print("\n=== BET SHEET ===")
    print(sheet.to_string(index=False))
    print(f"\nTotal bets: {len(sheet)}, Total risk: {sheet['stake_u'].sum():.2f} units")

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        sheet.to_csv(out, index=False)
        log.info("Wrote picks to %s", out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _wide_to_long_odds(games: pd.DataFrame) -> pd.DataFrame:
    """Convert games-with-home_decimal/away_decimal to the long-format that
    backtesting.simulate_roi expects (decimal odds column).

    Vectorized: a concat of the home-view and away-view slices, no per-row
    iteration. On a full-season frame (~2500 games) this drops the step from
    seconds to <10ms."""
    home = (games[["game_id", "home_team", "home_decimal"]]
            .rename(columns={"home_team": "outcome",
                             "home_decimal": "decimal"}))
    away = (games[["game_id", "away_team", "away_decimal"]]
            .rename(columns={"away_team": "outcome",
                             "away_decimal": "decimal"}))
    long = pd.concat([home, away], ignore_index=True)
    long["market"] = "h2h"
    return long[["game_id", "market", "outcome", "decimal"]]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv):
    p = argparse.ArgumentParser(description="SP-anchored MLB edge engine")
    p.add_argument(
        "--mode",
        choices=["backtest", "train", "predict"],
        required=True,
    )
    p.add_argument("--season", type=int, help="Backtest season (backtest mode)")
    p.add_argument("--seasons", help="Comma-sep seasons (train mode), e.g. 2025,2026")
    p.add_argument("--through", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="YTD cutoff date, e.g. 2026-04-19")
    p.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="Slate date (predict mode)")
    p.add_argument("--model_path", default="models/latest.pkl")
    p.add_argument("--save",  help="Where to save trained models (train mode)")
    p.add_argument("--out",   help="Optional CSV output path")
    p.add_argument("--bankroll", type=float, default=100.0)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    if args.mode == "backtest":
        if not args.season:
            sys.exit("--season required for backtest")
        run_backtest(args.season, args.through, args.out, args.bankroll)
    elif args.mode == "train":
        if not args.seasons or not args.save:
            sys.exit("--seasons and --save required for train")
        seasons = [int(s) for s in args.seasons.split(",")]
        run_train(seasons, args.through, args.save)
    else:  # predict
        if not args.date:
            sys.exit("--date required for predict")
        run_predict(args.date, args.model_path, args.bankroll, args.out)


if __name__ == "__main__":
    main()
