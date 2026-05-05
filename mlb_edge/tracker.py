"""
tracker.py
----------
Unified paper-tracking for full-game moneyline AND totals picks.

Three commands:

    python -m mlb_edge.tracker predict [--date YYYY-MM-DD]
        Generate today's (or specified date's) picks for both markets and
        append them to the rolling log. Does not rerun picks that are
        already logged for that date — idempotent within a day.

    python -m mlb_edge.tracker resolve
        For every unresolved pick whose game_date is now in the past, pull
        the actual final score from pybaseball/Statcast and mark it win,
        loss, or push. Updates the log in place.

    python -m mlb_edge.tracker stats
        Print running stats — overall ROI, per-market ROI, per-tier
        breakdown, recent 7-day rolling, win rate vs break-even.

Log file: tracker_log.csv  (shared between ML and totals markets)
Columns:
    date_picked, market, game_id, game_date, home_team, away_team,
    pick_detail, side, tier_or_edge, decimal_odds, model_prob, fair_prob,
    stake_units, signals, resolved, won, pnl_units,
    actual_home_score, actual_away_score
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.tracker")

LOG_FILE = Path("tracker_log.csv")

LOG_COLUMNS = [
    "date_picked", "market", "game_id", "game_date",
    "home_team", "away_team", "pick_detail", "side", "tier_or_edge",
    "decimal_odds", "model_prob", "fair_prob", "stake_units", "signals",
    "resolved", "won", "pnl_units",
    "actual_home_score", "actual_away_score",
]


# ---------------------------------------------------------------------------
# Predict — runs both main predict commands and merges results into log
# ---------------------------------------------------------------------------
def run_predict(target_date: date) -> None:
    log.info("=== TRACKER PREDICT: %s ===", target_date)

    ml_csv = Path(f"_tmp_ml_{target_date}.csv")
    tot_csv = Path(f"_tmp_totals_{target_date}.csv")

    # Call main predict modes as subprocesses. This keeps the existing logic
    # intact (conviction filters, Kelly sizing, edge thresholds) and avoids
    # duplicating it here.
    log.info("Running moneyline predict...")
    _run_subprocess([
        sys.executable, "-m", "mlb_edge.main",
        "--mode", "predict",
        "--date", target_date.isoformat(),
        "--out", str(ml_csv),
    ])

    log.info("Running totals predict...")
    _run_subprocess([
        sys.executable, "-m", "mlb_edge.main_totals",
        "--mode", "predict",
        "--date", target_date.isoformat(),
        "--out", str(tot_csv),
    ])

    # Load both pick CSVs (if they exist — may be empty if no picks fired)
    new_rows = []

    if ml_csv.exists():
        try:
            ml = pd.read_csv(ml_csv)
            for _, r in ml.iterrows():
                new_rows.append(_normalize_ml_row(r, target_date))
            log.info("Moneyline picks: %d", len(ml))
        except pd.errors.EmptyDataError:
            log.info("Moneyline: no picks")
        ml_csv.unlink(missing_ok=True)
    else:
        log.info("Moneyline: no output file (no picks fired)")

    if tot_csv.exists():
        try:
            tot = pd.read_csv(tot_csv)
            for _, r in tot.iterrows():
                if float(r.get("stake_units", 0)) > 0:
                    new_rows.append(_normalize_totals_row(r, target_date))
            log.info("Totals picks with stake > 0: %d of %d",
                     len([r for r in new_rows if r["market"] == "totals"]),
                     len(tot))
        except pd.errors.EmptyDataError:
            log.info("Totals: no picks")
        tot_csv.unlink(missing_ok=True)
    else:
        log.info("Totals: no output file (no picks fired)")

    if not new_rows:
        print(f"\nNo picks generated for {target_date}.")
        return

    new_df = pd.DataFrame(new_rows)

    # Idempotency: if picks for this date already in log, drop them first
    existing = _load_log()
    if not existing.empty:
        mask = (existing["date_picked"] == target_date.isoformat())
        if mask.any():
            log.info("Replacing %d existing picks logged for %s",
                     mask.sum(), target_date)
            existing = existing[~mask]

    combined = pd.concat([existing, new_df], ignore_index=True)
    _save_log(combined)

    print(f"\n=== LOGGED {len(new_df)} PICKS FOR {target_date} ===")
    print(new_df[[
        "market", "home_team", "away_team", "pick_detail",
        "decimal_odds", "model_prob", "stake_units"
    ]].to_string(index=False))
    print()


def _normalize_ml_row(r: pd.Series, target_date: date) -> dict:
    """Convert a picks_ml_today.csv row into the unified log schema."""
    return {
        "date_picked":     datetime.now().date().isoformat(),
        "market":          "moneyline",
        "game_id":         r.get("game_id"),
        "game_date":       target_date.isoformat(),
        # Reconstruct home/away from pick_team + side
        "home_team":       _home_team_from_row(r),
        "away_team":       _away_team_from_row(r),
        "pick_detail":     r.get("team"),
        "side":            r.get("side"),
        "tier_or_edge":    r.get("tier"),
        "decimal_odds":    round(float(r.get("decimal", 0)), 3),
        "model_prob":      round(float(r.get("model_prob", 0)), 4),
        "fair_prob":       round(float(r.get("fair_prob", 0)), 4),
        "stake_units":     round(float(r.get("stake_u", 0)), 3),
        "signals":         r.get("signals", ""),
        "resolved":        False,
        "won":             "",
        "pnl_units":       "",
        "actual_home_score": "",
        "actual_away_score": "",
    }


def _normalize_totals_row(r: pd.Series, target_date: date) -> dict:
    """Convert a picks_totals_today.csv row into the unified log schema."""
    return {
        "date_picked":     datetime.now().date().isoformat(),
        "market":          "totals",
        "game_id":         "",  # totals CSV doesn't have it; match by teams+date
        "game_date":       target_date.isoformat(),
        "home_team":       r.get("home_team"),
        "away_team":       r.get("away_team"),
        "pick_detail":     f"{r.get('side')} {r.get('total_line')}",
        "side":            r.get("side"),
        "tier_or_edge":    f"edge={r.get('edge_runs')}r",
        "decimal_odds":    round(float(r.get("decimal", 0)), 3),
        "model_prob":      round(float(r.get("our_prob", 0)), 4),
        "fair_prob":       round(float(r.get("book_fair", 0)), 4),
        "stake_units":     round(float(r.get("stake_units", 0)), 3),
        "signals":         f"pred={r.get('pred_runs')}r, line={r.get('total_line')}",
        "resolved":        False,
        "won":             "",
        "pnl_units":       "",
        "actual_home_score": "",
        "actual_away_score": "",
    }


def _home_team_from_row(r: pd.Series) -> str:
    # ML pick CSV has 'team' and 'side' — reconstruct home/away
    team = r.get("team")
    side = r.get("side")
    # We don't have the other team in the CSV directly — need matchup lookup
    # For now, put "team" into home or away per side, leave other blank.
    # Resolver will use game_id to fetch both teams.
    return team if side == "home" else ""


def _away_team_from_row(r: pd.Series) -> str:
    team = r.get("team")
    side = r.get("side")
    return team if side == "away" else ""


# ---------------------------------------------------------------------------
# Resolve — pull Statcast for past games, score the open bets
# ---------------------------------------------------------------------------
def run_resolve() -> None:
    log.info("=== TRACKER RESOLVE ===")

    logdf = _load_log()
    if logdf.empty:
        print("No log file yet — run `tracker predict` first.")
        return

    unresolved_mask = ~logdf["resolved"].astype(str).str.lower().isin(["true", "1"])
    unresolved = logdf[unresolved_mask].copy()
    if unresolved.empty:
        log.info("Nothing to resolve.")
        _print_running_stats(logdf)
        return

    unresolved["game_date_dt"] = pd.to_datetime(unresolved["game_date"]).dt.date
    today = date.today()
    # Only resolve games that have actually finished (before today)
    resolvable = unresolved[unresolved["game_date_dt"] < today]
    if resolvable.empty:
        log.info("No unresolved bets with completed games yet.")
        _print_running_stats(logdf)
        return

    log.info("Resolving %d bets from %s to %s",
             len(resolvable),
             resolvable["game_date_dt"].min(),
             resolvable["game_date_dt"].max())

    # Fetch final scores from Statcast for the relevant date range
    from . import data_ingestion as di
    sc = di.fetch_statcast_range(
        resolvable["game_date_dt"].min(),
        resolvable["game_date_dt"].max(),
    )
    if sc.empty:
        log.error("No Statcast returned — cannot resolve")
        _print_running_stats(logdf)
        return

    sc["game_date"] = pd.to_datetime(sc["game_date"])
    # Final scores: use max(post_*_score) per game_pk (end-of-game totals)
    finals = sc.groupby(["game_pk", "home_team", "away_team"]).agg(
        home_final=("post_home_score", "max"),
        away_final=("post_away_score", "max"),
        game_date=("game_date", "first"),
    ).reset_index()
    finals["game_date"] = finals["game_date"].dt.date

    resolved_count = 0
    for idx, row in resolvable.iterrows():
        # Find matching game — for ML picks we have game_id, for totals we
        # match on (game_date, home, away). Try game_id first.
        match = None
        gid = row.get("game_id")
        if pd.notna(gid) and str(gid).strip() and str(gid) != "":
            try:
                gid_int = int(float(gid))
                m = finals[finals["game_pk"] == gid_int]
                if not m.empty:
                    match = m.iloc[0]
            except (ValueError, TypeError):
                pass

        if match is None:
            # Fall back to (date, home, away)
            gd = row["game_date_dt"]
            home = row.get("home_team")
            away = row.get("away_team")
            if home and away:
                m = finals[
                    (finals["game_date"] == gd)
                    & (finals["home_team"] == home)
                    & (finals["away_team"] == away)
                ]
                if not m.empty:
                    match = m.iloc[0]

        if match is None:
            log.warning("No final score found for %s %s@%s",
                        row["game_date"], row.get("away_team"),
                        row.get("home_team"))
            continue

        home_final = float(match["home_final"])
        away_final = float(match["away_final"])

        outcome, pnl = _score_bet(row, home_final, away_final)

        logdf.loc[idx, "resolved"] = True
        logdf.loc[idx, "won"] = outcome
        logdf.loc[idx, "pnl_units"] = round(pnl, 3)
        logdf.loc[idx, "actual_home_score"] = home_final
        logdf.loc[idx, "actual_away_score"] = away_final
        resolved_count += 1

    _save_log(logdf)
    log.info("Resolved %d bets.", resolved_count)
    _print_running_stats(logdf)


def _score_bet(row: pd.Series, home_final: float, away_final: float):
    """Return (outcome_str, pnl_units) for a single bet given final scores."""
    stake = float(row.get("stake_units", 0))
    dec = float(row.get("decimal_odds", 0))
    market = row.get("market")

    if market == "moneyline":
        home_won = home_final > away_final
        side = str(row.get("side", "")).lower()
        won = (side == "home" and home_won) or (side == "away" and not home_won)
        if home_final == away_final:  # shouldn't happen in MLB but defensive
            return "push", 0.0
        pnl = stake * (dec - 1) if won else -stake
        return ("win" if won else "loss"), pnl

    elif market == "totals":
        # Parse the line from tier_or_edge or signals. Cleanest: from pick_detail
        # which is like "over 8.5" or "under 9.0"
        detail = str(row.get("pick_detail", ""))
        parts = detail.split()
        if len(parts) < 2:
            return "unresolved", 0.0
        side = parts[0].lower()
        try:
            line = float(parts[1])
        except ValueError:
            return "unresolved", 0.0
        total = home_final + away_final
        if total == line:
            return "push", 0.0
        won = (side == "over" and total > line) or \
              (side == "under" and total < line)
        pnl = stake * (dec - 1) if won else -stake
        return ("win" if won else "loss"), pnl

    return "unresolved", 0.0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def run_stats() -> None:
    logdf = _load_log()
    if logdf.empty:
        print("No log file yet.")
        return
    _print_running_stats(logdf)


def _print_running_stats(logdf: pd.DataFrame) -> None:
    resolved = logdf[
        logdf["resolved"].astype(str).str.lower().isin(["true", "1"])
    ].copy()
    if resolved.empty:
        print("\nNo resolved bets yet.")
        return

    resolved["pnl_units"] = pd.to_numeric(resolved["pnl_units"], errors="coerce")
    resolved["stake_units"] = pd.to_numeric(resolved["stake_units"], errors="coerce")

    print("\n" + "=" * 60)
    print("TRACKER STATS")
    print("=" * 60)

    def _summarize(df: pd.DataFrame, label: str):
        if df.empty:
            print(f"  {label}: no bets")
            return
        n = len(df)
        w = (df["won"] == "win").sum()
        l = (df["won"] == "loss").sum()
        p = (df["won"] == "push").sum()
        decided = w + l
        wr = w / decided * 100 if decided else 0
        stake = df["stake_units"].sum()
        pnl = df["pnl_units"].sum()
        roi = (pnl / stake * 100) if stake > 0 else 0
        print(f"  {label}: n={n}  w/l/p={w}/{l}/{p}  "
              f"wr={wr:.1f}%  stake={stake:.1f}u  pnl={pnl:+.2f}u  roi={roi:+.2f}%")

    _summarize(resolved, "OVERALL ")
    print()

    for market in ["moneyline", "totals"]:
        sub = resolved[resolved["market"] == market]
        _summarize(sub, f"{market:10s}")

    # Rolling last 7 days
    resolved["gd"] = pd.to_datetime(resolved["game_date"]).dt.date
    cutoff = date.today() - timedelta(days=7)
    recent = resolved[resolved["gd"] >= cutoff]
    if not recent.empty:
        print()
        _summarize(recent, "LAST 7D ")

    # Break-even reference
    print()
    print(f"  Break-even reference (−110 vig): ~52.4% win rate required")
    print("=" * 60)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def _load_log() -> pd.DataFrame:
    if not LOG_FILE.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    df = pd.read_csv(LOG_FILE, dtype={"resolved": object})
    df["resolved"] = df["resolved"].astype(str).str.lower().isin(["true", "1"])
    return df


def _save_log(df: pd.DataFrame) -> None:
    # Ensure all columns present and in order
    for c in LOG_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[LOG_COLUMNS]
    df.to_csv(LOG_FILE, index=False)


def _run_subprocess(cmd: list) -> None:
    """Invoke a subprocess, stream output, don't crash on non-zero return."""
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            log.warning("Subprocess returned %d: %s",
                        result.returncode, " ".join(cmd))
    except Exception as e:
        log.error("Subprocess failed: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    p = argparse.ArgumentParser(description="Unified picks tracker")
    p.add_argument("command", choices=["predict", "resolve", "stats"])
    p.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="Target date (default today) — predict mode only")
    args = p.parse_args(argv)

    if args.command == "predict":
        target = args.date or date.today()
        run_predict(target)
    elif args.command == "resolve":
        run_resolve()
    elif args.command == "stats":
        run_stats()


if __name__ == "__main__":
    main()
