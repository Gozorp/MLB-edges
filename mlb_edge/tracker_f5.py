"""
tracker_f5.py
-------------
The daily paper-tracking loop for F5 moneyline predictions.

Two commands:
  python -m mlb_edge.tracker_f5 predict
      Fetches today's MLB slate and live F5 odds, runs the trained F5 model,
      outputs today's picks to the rolling log.

  python -m mlb_edge.tracker_f5 resolve
      Reads all unresolved bets in the log, fetches actual F5 scores via
      pybaseball, marks wins/losses, prints updated running stats.

Log file: f5_tracker_log.csv
Schema: date_picked, game_date, home_team, away_team, pick_team, side,
        tier, decimal_odds, model_prob, fair_prob, edge_pp, stake_units,
        signals, resolved, won, pnl_units, home_f5_score, away_f5_score

The log is appended to — never rewritten. Every prediction ever made is
preserved for later analysis.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import build_pipeline as bp
from . import live_f5
from . import model as md
from .backtest_f5 import score_f5_conviction
from .config_f5 import (
    F5_KELLY_FRACTION, F5_MAX_DAILY_RISK_UNITS, F5_MAX_MODEL_PROB,
    F5_MIN_EDGE_PCT, F5_MIN_MODEL_PROB, F5_TIER_SIZES,
)
from .edge_calculator import kelly_stake
from .market_analysis import shin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.tracker_f5")

LOG_FILE = Path("f5_tracker_log.csv")
MODEL_FILE = Path("models/f5_stage1.pkl")


LOG_COLUMNS = [
    "date_picked", "game_date", "home_team", "away_team", "pick_team",
    "side", "tier", "decimal_odds", "model_prob", "fair_prob", "edge_pp",
    "stake_units", "signals", "resolved", "won", "pnl_units",
    "home_f5_score", "away_f5_score",
]


# ---------------------------------------------------------------------------
# Predict
# ---------------------------------------------------------------------------
def run_predict(bankroll: float) -> None:
    today = date.today()
    log.info("=== F5 PREDICT: %s ===", today)

    # 1. Load Stage 1 model (or train if not saved yet)
    if MODEL_FILE.exists():
        import joblib
        stage1 = joblib.load(MODEL_FILE)
        log.info("Loaded Stage 1 from %s", MODEL_FILE)
    else:
        log.info("No saved model found; training Stage 1 on 2023+2024+2025...")
        stage1 = _train_and_save_stage1()

    # 2. Build today's slate features
    games = bp.build_slate_frame(today)
    if games.empty:
        log.error("No games on slate for today")
        return

    # 3. Fetch live F5 odds
    raw_odds = live_f5.fetch_live_f5_odds()
    if raw_odds.empty:
        log.error("No live F5 odds available")
        return

    f5_wide = live_f5.median_f5_by_game(raw_odds)
    if f5_wide.empty:
        log.error("No clean F5 odds after aggregation")
        return

    log.info("Live F5 odds available for %d games", len(f5_wide))

    # 4. Join odds onto slate
    games["game_date_only"] = pd.to_datetime(games["game_date"]).dt.date
    joined = games.merge(
        f5_wide, left_on=["home_team", "away_team", "game_date_only"],
        right_on=["home_team", "away_team", "commence_date"], how="inner"
    )
    if joined.empty:
        log.error("No games matched between slate and F5 odds — team name issue?")
        return

    # 5. Predict F5 probability
    feats = [c for c in stage1.feature_cols if c in joined.columns]
    joined["f5_prob"] = stage1.booster.predict_proba(joined[feats].values)[:, 1]

    # 6. For each game, apply filter + conviction + Kelly. Low cardinality
    #    (≤15 games/day) — iterrows is plenty fast and reads cleaner than
    #    vectorizing for a handful of rows.
    picks = []
    total_risk_units = 0.0
    for _, r in joined.iterrows():
        home_dec = r["home_f5_decimal"]
        away_dec = r["away_f5_decimal"]
        if (pd.isna(home_dec) or pd.isna(away_dec)
                or home_dec < 1.05 or home_dec > 10.0
                or away_dec < 1.05 or away_dec > 10.0):
            continue

        p_home_raw = 1.0 / home_dec
        p_away_raw = 1.0 / away_dec
        p_home_fair, _ = shin(p_home_raw, p_away_raw)
        if pd.isna(p_home_fair):
            continue

        p_f5 = r["f5_prob"]
        if p_f5 >= 0.5:
            side, dec, fair, prob = "home", home_dec, p_home_fair, p_f5
            pick_team = r["home_team"]
        else:
            side, dec, fair, prob = "away", away_dec, 1 - p_home_fair, 1 - p_f5
            pick_team = r["away_team"]

        if not (F5_MIN_MODEL_PROB <= prob <= F5_MAX_MODEL_PROB):
            continue
        edge = prob - fair
        if edge < F5_MIN_EDGE_PCT:
            continue

        perspective = r.copy()
        if side == "away":
            for col in ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                        "sp_siera_gap", "sp_fip_gap"]:
                if col in perspective:
                    perspective[col] = -perspective[col]
            perspective["home_sp_luck"], perspective["away_sp_luck"] = (
                perspective.get("away_sp_luck"), perspective.get("home_sp_luck"),
            )
        tier, signals_fired = score_f5_conviction(perspective)
        mult = F5_TIER_SIZES[tier]
        if mult == 0:
            continue

        stake_frac = kelly_stake(prob, dec, fraction=F5_KELLY_FRACTION) * mult
        stake_units = stake_frac * 100  # normalize to "units" (1u = 1% of bankroll)

        # Daily cap
        if total_risk_units + stake_units > F5_MAX_DAILY_RISK_UNITS:
            stake_units = max(0.0, F5_MAX_DAILY_RISK_UNITS - total_risk_units)
            if stake_units <= 0:
                continue
        total_risk_units += stake_units

        gd = r["game_date_only"]
        gd_str = gd.isoformat() if hasattr(gd, "isoformat") else str(gd)
        picks.append({
            "date_picked":   today.isoformat(),
            "game_date":     gd_str,
            "home_team":     r["home_team"],
            "away_team":     r["away_team"],
            "pick_team":     pick_team,
            "side":          side,
            "tier":          tier,
            "decimal_odds":  round(dec, 3),
            "model_prob":    round(prob, 4),
            "fair_prob":     round(fair, 4),
            "edge_pp":       round(edge * 100, 2),
            "stake_units":   round(stake_units, 2),
            "signals":       "; ".join(signals_fired),
            "resolved":      False,
            "won":           "",
            "pnl_units":     "",
            "home_f5_score": "",
            "away_f5_score": "",
        })

    if not picks:
        print(f"\nNo F5 picks today (no games passed filter).")
        return

    picks_df = pd.DataFrame(picks)

    # Print bet sheet
    print(f"\n=== F5 PICKS — {today} ===")
    display_cols = ["game_date", "home_team", "away_team", "pick_team", "side",
                    "tier", "decimal_odds", "model_prob", "edge_pp", "stake_units"]
    print(picks_df[display_cols].to_string(index=False))
    print(f"\nTotal bets: {len(picks_df)}, Total risk: {picks_df['stake_units'].sum():.2f} units")

    # Append to log
    _append_to_log(picks_df)
    log.info("Appended %d picks to %s", len(picks_df), LOG_FILE)


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------
def run_resolve() -> None:
    log.info("=== F5 RESOLVE ===")

    if not LOG_FILE.exists():
        log.error("No log file yet — run predict first")
        return

    # Force text columns to object so empty-string initial values don't get
    # auto-typed as float64 and block subsequent string writes. Fresh logs
    # (every row unresolved, every `won` cell empty) would otherwise read
    # the column as all-NaN float, and `logdf.loc[idx, "won"] = "win"` below
    # blows up with LossySetitemError.
    logdf = pd.read_csv(LOG_FILE, dtype={
        "resolved": object, "won": object,
    })
    # Normalize resolved column to bool
    logdf["resolved"] = logdf["resolved"].astype(str).str.lower().isin(["true", "1", "yes"])
    unresolved = logdf[~logdf["resolved"]].copy()

    if unresolved.empty:
        log.info("Nothing to resolve.")
        _print_running_stats(logdf)
        return

    # Get unique game dates in unresolved, pull Statcast for that range
    unresolved["game_date"] = pd.to_datetime(unresolved["game_date"]).dt.date

    # Only resolve games that are strictly in the past (yesterday or earlier)
    today = date.today()
    resolvable_mask = unresolved["game_date"] < today
    resolvable = unresolved[resolvable_mask]
    if resolvable.empty:
        log.info("No unresolved bets with completed games yet.")
        _print_running_stats(logdf)
        return

    log.info("Fetching Statcast %s → %s to resolve %d bets",
             resolvable["game_date"].min(), resolvable["game_date"].max(),
             len(resolvable))

    from . import data_ingestion as di
    sc = di.fetch_statcast_range(resolvable["game_date"].min(),
                                 resolvable["game_date"].max())
    if sc.empty:
        log.error("No Statcast data returned for resolution window")
        _print_running_stats(logdf)
        return

    sc["game_date_dt"] = pd.to_datetime(sc["game_date"]).dt.date
    f5 = sc[sc["inning"] <= 5]
    outcomes = (f5.groupby(["game_date_dt", "home_team", "away_team"])
                  .agg(home_f5=("post_home_score", "max"),
                       away_f5=("post_away_score", "max"))
                  .reset_index())

    # Resolve each bet. `idx` is the row's index in `logdf` (resolvable is a
    # view preserving the original index), so we update in place via
    # `logdf.loc[idx, ...]` with no auxiliary mask needed.
    resolved_count = 0
    for idx, row in resolvable.iterrows():
        m = outcomes[
            (outcomes["game_date_dt"] == row["game_date"]) &
            (outcomes["home_team"] == row["home_team"]) &
            (outcomes["away_team"] == row["away_team"])
        ]
        if m.empty:
            log.warning("No Statcast outcome found for %s %s@%s",
                        row["game_date"], row["away_team"], row["home_team"])
            continue

        home_f5 = float(m.iloc[0]["home_f5"])
        away_f5 = float(m.iloc[0]["away_f5"])

        # Determine outcome
        if home_f5 == away_f5:
            # Tie → push: stake refunded, pnl = 0
            pnl = 0.0
            won_str = "push"
        else:
            home_wins_f5 = home_f5 > away_f5
            won = (row["side"] == "home" and home_wins_f5) or \
                  (row["side"] == "away" and not home_wins_f5)
            dec = float(row["decimal_odds"])
            stake = float(row["stake_units"])
            pnl = stake * (dec - 1) if won else -stake
            won_str = "win" if won else "loss"

        logdf.loc[idx, "resolved"] = True
        logdf.loc[idx, "won"] = won_str
        logdf.loc[idx, "pnl_units"] = round(pnl, 3)
        logdf.loc[idx, "home_f5_score"] = home_f5
        logdf.loc[idx, "away_f5_score"] = away_f5
        resolved_count += 1

    # Save back
    logdf.to_csv(LOG_FILE, index=False)
    log.info("Resolved %d bets", resolved_count)

    _print_running_stats(logdf)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def _print_running_stats(logdf: pd.DataFrame) -> None:
    resolved = logdf[logdf["resolved"].astype(str).str.lower().isin(["true", "1", "yes"])].copy()
    if resolved.empty:
        print("\nNo resolved bets yet.")
        return

    # Coerce numeric
    resolved["pnl_units"] = pd.to_numeric(resolved["pnl_units"], errors="coerce")
    resolved["stake_units"] = pd.to_numeric(resolved["stake_units"], errors="coerce")
    resolved["decimal_odds"] = pd.to_numeric(resolved["decimal_odds"], errors="coerce")

    total_stake = resolved["stake_units"].sum()
    total_pnl = resolved["pnl_units"].sum()
    wins = (resolved["won"] == "win").sum()
    losses = (resolved["won"] == "loss").sum()
    pushes = (resolved["won"] == "push").sum()
    decided = wins + losses
    wr = wins / decided if decided > 0 else 0.0
    roi = (total_pnl / total_stake * 100) if total_stake > 0 else 0.0

    print("\n" + "=" * 50)
    print("F5 TRACKER — RUNNING STATS")
    print("=" * 50)
    print(f"  Total bets resolved: {len(resolved)}")
    print(f"    Wins:   {wins}")
    print(f"    Losses: {losses}")
    print(f"    Pushes: {pushes}")
    print(f"  Win rate (non-push): {wr*100:.1f}%")
    print(f"  Total staked: {total_stake:.2f} units")
    print(f"  Total P&L:    {total_pnl:+.2f} units")
    print(f"  ROI:          {roi:+.2f}%")

    if "tier" in resolved.columns:
        print(f"\n  By tier:")
        for tier, sub in resolved.groupby("tier"):
            n = len(sub)
            tw = (sub["won"] == "win").sum()
            tl = (sub["won"] == "loss").sum()
            td = tw + tl
            tpnl = sub["pnl_units"].sum()
            tst = sub["stake_units"].sum()
            twr = tw/td*100 if td else 0
            troi = tpnl/tst*100 if tst > 0 else 0
            print(f"    {tier:8s}: n={n:3d}  w/l/p={tw}/{tl}/{n-tw-tl}  "
                  f"wr={twr:.1f}%  pnl={tpnl:+.2f}u  roi={troi:+.1f}%")
    print("=" * 50 + "\n")


# ---------------------------------------------------------------------------
# Model training helper
# ---------------------------------------------------------------------------
def _train_and_save_stage1():
    """Train Stage 1 on 2023+2024+2025 and persist."""
    from . import build_pipeline as bp
    import joblib

    frames = []
    for s in [2023, 2024, 2025]:
        f = bp.build_historical_frame(s)
        if not f.empty:
            frames.append(f)
    if not frames:
        raise RuntimeError("No training data")
    df = pd.concat(frames, ignore_index=True).sort_values("game_date")
    df = df.dropna(subset=["home_f5_win"])
    log.info("Training Stage 1 on %d games", len(df))

    stage1 = md.train_stage1_f5(df)
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(stage1, MODEL_FILE)
    log.info("Saved Stage 1 to %s (train_auc=%.4f)",
             MODEL_FILE, stage1.metadata.get("train_auc", 0.0))
    return stage1


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def _append_to_log(new_rows: pd.DataFrame) -> None:
    if LOG_FILE.exists():
        existing = pd.read_csv(LOG_FILE)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    # Ensure all expected columns present
    for c in LOG_COLUMNS:
        if c not in combined.columns:
            combined[c] = ""
    combined = combined[LOG_COLUMNS]
    combined.to_csv(LOG_FILE, index=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv or argv[0] not in ("predict", "resolve", "stats"):
        print("Usage: python -m mlb_edge.tracker_f5 {predict|resolve|stats}")
        sys.exit(1)
    cmd = argv[0]
    if cmd == "predict":
        run_predict(bankroll=100.0)
    elif cmd == "resolve":
        run_resolve()
    elif cmd == "stats":
        if LOG_FILE.exists():
            _print_running_stats(pd.read_csv(LOG_FILE))
        else:
            print("No log file yet.")


if __name__ == "__main__":
    main()
