"""
backtest_totals.py
------------------
Walk-forward backtest of the totals (over/under) model against real historical
totals lines from the-odds-api cached snapshots.

Runtime flow:
  1. Load cached feature frame (same as full-game backtest uses).
  2. Enrich with home_score, away_score, home_f5_score, away_f5_score from
     raw Statcast (not in the cached parquet — computed fresh, fast).
  3. Build totals odds frame from cached historical snapshots.
  4. Merge → join into per-game rows with total_line + over_decimal +
     under_decimal.
  5. Walk-forward train Stage 1 (F5 runs) + Stage 2 (full-game runs).
  6. For each game, compute edge vs the posted line:
        edge_over  = predicted_total_runs - total_line
        edge_under = total_line - predicted_total_runs
     Whichever is larger AND exceeds MIN_EDGE_RUNS is the side we bet.
  7. Size with fractional Kelly using the side's decimal odds.
  8. Resolve: actual_total_runs vs line (over wins if actual > line;
     under wins if actual < line; tie = push).

Key differences from moneyline backtest:
  - Edge is in RUNS (continuous), not percentage points.
  - We're comparing model runs vs market line, then betting the side of
    the implied probability where we have the bigger mispricing.
  - Pushes (actual == line exactly) are handled as stake-refunded.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import data_ingestion as di
from .edge_calculator import kelly_stake
from .market_analysis import shin_vec
from .model_totals import walkforward_totals_predict  # re-export for main_totals

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
TOTALS_MIN_EDGE_RUNS = 0.30   # predicted vs line; 0.30 runs = ~3% implied prob edge
TOTALS_KELLY_FRACTION = 0.20  # quarter-Kelly variants — start conservative
TOTALS_MAX_DAILY_RISK_UNITS = 5.0
TOTALS_MAX_DECIMAL = 10.0
TOTALS_MIN_DECIMAL = 1.05


@dataclass
class TotalsBacktestResult:
    bets: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: Dict


# ---------------------------------------------------------------------------
# Score enrichment — pulls scores from raw Statcast at runtime
# ---------------------------------------------------------------------------
def enrich_scores(games: pd.DataFrame, season: int,
                  through: Optional[date] = None) -> pd.DataFrame:
    """
    Add home_score, away_score, home_f5_score, away_f5_score columns.

    Reads cached raw Statcast (same cache full-game backtest uses — zero cost
    if features are already built).
    """
    from datetime import date as _date
    start = _date(season, 3, 20)
    end = through or _date(season, 10, 5)

    log.info("Loading Statcast for scores (cached)...")
    sc = di.fetch_statcast_range(start, end)
    if sc.empty:
        log.error("No Statcast available for score enrichment")
        return games

    sc["game_date"] = pd.to_datetime(sc["game_date"])
    sc = sc[sc["game_date"].dt.year == season]
    if through:
        sc = sc[sc["game_date"].dt.date <= through]

    # Final scores: last pitch of each game
    fin = sc.sort_values(["game_pk", "inning", "at_bat_number", "pitch_number"])
    finals = fin.groupby("game_pk").tail(1)[
        ["game_pk", "post_home_score", "post_away_score"]
    ].rename(columns={"post_home_score": "home_score",
                      "post_away_score": "away_score"})

    # F5 scores: max post_*_score where inning <= 5
    f5 = sc[sc["inning"] <= 5].groupby("game_pk").agg(
        home_f5_score=("post_home_score", "max"),
        away_f5_score=("post_away_score", "max"),
    ).reset_index()

    scores = finals.merge(f5, on="game_pk", how="left")
    scores = scores.rename(columns={"game_pk": "game_id"})

    # Games' game_id was also set to game_pk
    out = games.merge(scores, on="game_id", how="left")
    missing = out["home_score"].isna().sum()
    if missing:
        log.warning("Could not enrich %d games with scores", missing)
    return out


# ---------------------------------------------------------------------------
# Side selection helper — available for parity tests / live path; the main
# simulator inlines the same logic vectorized.
# ---------------------------------------------------------------------------
def choose_side(pred_runs: float, line: float,
                over_dec: float, under_dec: float) -> Optional[Dict]:
    """
    Returns a dict with side info if the edge passes the threshold, else None.

    The decision rule:
      - If pred > line + threshold → bet OVER (we think game goes higher)
      - If pred < line - threshold → bet UNDER (we think game goes lower)
      - Otherwise → no bet
    """
    if pd.isna(pred_runs) or pd.isna(line):
        return None

    edge_runs = pred_runs - line
    if abs(edge_runs) < TOTALS_MIN_EDGE_RUNS:
        return None

    if edge_runs > 0:
        return {"side": "over", "decimal": over_dec, "edge_runs": edge_runs}
    else:
        return {"side": "under", "decimal": under_dec, "edge_runs": -edge_runs}


# ---------------------------------------------------------------------------
# ROI simulation
# ---------------------------------------------------------------------------
def simulate_totals_roi(preds: pd.DataFrame,
                        start_bankroll: float = 100.0) -> TotalsBacktestResult:
    """
    Simulate totals betting against real book lines.

    preds must contain:
      game_id, game_date, home_team, away_team,
      total_line, over_decimal, under_decimal,
      total_runs_pred, total_runs (actual)

    Vectorization matches `backtest_f5.simulate_f5_roi` and
    `backtesting.simulate_roi`: pre-filter + devig + edge + side selection
    run as numpy ops across the whole frame; only rows that pass the edge
    filter enter the per-row Kelly/daily-cap/resolve loop. Inner loop
    operates on a list of plain dicts (`df.to_dict("records")`) because
    `dict.get` is ~10× faster than `Series.get` inside a hot loop.
    """
    if preds.empty:
        return TotalsBacktestResult(pd.DataFrame(), pd.DataFrame(),
                                    {"note": "empty"})

    df = preds.sort_values("game_date").reset_index(drop=True)

    line_arr     = df["total_line"].to_numpy(dtype=float)
    over_dec_arr = df["over_decimal"].to_numpy(dtype=float)
    under_dec_arr = df["under_decimal"].to_numpy(dtype=float)
    pred_arr     = df["total_runs_pred"].to_numpy(dtype=float)
    actual_arr   = df["total_runs"].to_numpy(dtype=float)

    valid = (
        np.isfinite(line_arr)
        & np.isfinite(over_dec_arr) & np.isfinite(under_dec_arr)
        & np.isfinite(pred_arr) & np.isfinite(actual_arr)
        & (over_dec_arr  >= TOTALS_MIN_DECIMAL) & (over_dec_arr  <= TOTALS_MAX_DECIMAL)
        & (under_dec_arr >= TOTALS_MIN_DECIMAL) & (under_dec_arr <= TOTALS_MAX_DECIMAL)
    )
    n_drop = int((~valid).sum())
    if n_drop:
        log.warning("Dropping %d totals rows with missing/out-of-range inputs",
                    n_drop)
    df = df.loc[valid].reset_index(drop=True)
    line_arr      = line_arr[valid]
    over_dec_arr  = over_dec_arr[valid]
    under_dec_arr = under_dec_arr[valid]
    pred_arr      = pred_arr[valid]
    actual_arr    = actual_arr[valid]

    # Devig via Shin
    p_over_fair_arr, p_under_fair_arr = shin_vec(
        1.0 / over_dec_arr, 1.0 / under_dec_arr
    )

    # Edge and side selection
    edge_signed_arr = pred_arr - line_arr
    is_over = edge_signed_arr > 0
    side_edge_arr = np.abs(edge_signed_arr)
    side_dec_arr = np.where(is_over, over_dec_arr, under_dec_arr)
    side_fair_arr = np.where(is_over, p_over_fair_arr, p_under_fair_arr)

    # The scalar drop approximated:
    #   our_prob = book_fair + min(0.02 * edge_runs, 0.10)
    # clamped to [0.01, 0.99]. Same here, elementwise.
    bump_arr = np.minimum(0.02 * side_edge_arr, 0.10)
    our_prob_arr = np.clip(side_fair_arr + bump_arr, 0.01, 0.99)

    consider = (
        np.isfinite(side_fair_arr)
        & (side_edge_arr >= TOTALS_MIN_EDGE_RUNS)
    )

    bankroll = start_bankroll
    bets: List[Dict] = []
    equity: List[Dict] = []
    daily_risk: Dict = {}
    cap_dollars = (TOTALS_MAX_DAILY_RISK_UNITS / 100.0) * start_bankroll

    records = df.to_dict("records")
    for i, r in enumerate(records):
        if not consider[i]:
            continue

        side = "over" if is_over[i] else "under"
        dec        = float(side_dec_arr[i])
        our_prob   = float(our_prob_arr[i])
        book_fair  = float(side_fair_arr[i])
        edge_runs  = float(side_edge_arr[i])
        line       = float(line_arr[i])
        pred_runs  = float(pred_arr[i])
        actual     = float(actual_arr[i])

        stake_frac = kelly_stake(our_prob, dec, fraction=TOTALS_KELLY_FRACTION)
        stake = stake_frac * bankroll
        if stake <= 0:
            continue

        day_key = pd.Timestamp(r["game_date"]).date()
        used = daily_risk.get(day_key, 0.0)
        remaining = max(0.0, cap_dollars - used)
        if remaining <= 0:
            continue
        stake = min(stake, remaining)
        daily_risk[day_key] = used + stake

        # Resolve
        if actual == line:
            pnl = 0.0
            outcome = "push"
        elif side == "over":
            won = actual > line
            pnl = stake * (dec - 1) if won else -stake
            outcome = "win" if won else "loss"
        else:
            won = actual < line
            pnl = stake * (dec - 1) if won else -stake
            outcome = "win" if won else "loss"

        bankroll += pnl

        bets.append({
            "game_id":     r["game_id"],
            "game_date":   r["game_date"],
            "home_team":   r["home_team"],
            "away_team":   r["away_team"],
            "total_line":  line,
            "pred_runs":   round(pred_runs, 2),
            "actual_runs": actual,
            "edge_runs":   round(edge_runs, 2),
            "side":        side,
            "decimal":     round(dec, 3),
            "our_prob":    round(our_prob, 4),
            "book_fair":   round(book_fair, 4),
            "stake":       round(stake, 3),
            "outcome":     outcome,
            "pnl":         round(pnl, 3),
            "bankroll":    round(bankroll, 3),
        })
        equity.append({"game_date": r["game_date"], "bankroll": bankroll})

    bets_df = pd.DataFrame(bets)
    eq_df = pd.DataFrame(equity)

    if bets_df.empty:
        return TotalsBacktestResult(bets_df, eq_df, {"note": "no totals bets"})

    wins = (bets_df["outcome"] == "win").sum()
    losses = (bets_df["outcome"] == "loss").sum()
    pushes = (bets_df["outcome"] == "push").sum()
    decided = wins + losses
    total_stake = bets_df["stake"].sum()
    total_pnl = bets_df["pnl"].sum()

    summary = {
        "n_bets":           len(bets_df),
        "wins":             int(wins),
        "losses":           int(losses),
        "pushes":           int(pushes),
        "win_rate":         float(wins / decided) if decided else 0.0,
        "total_stake":      float(total_stake),
        "total_pnl":        float(total_pnl),
        "roi_pct":          float(total_pnl / total_stake * 100)
                            if total_stake > 0 else 0.0,
        "starting_bankroll": start_bankroll,
        "ending_bankroll":  float(bankroll),
        "max_drawdown_pct": float(_max_dd(eq_df["bankroll"]) * 100)
                            if not eq_df.empty else 0.0,
        "by_side":          bets_df.groupby("side").agg(
            n=("outcome", "size"),
            w=("outcome", lambda s: (s == "win").sum()),
            l=("outcome", lambda s: (s == "loss").sum()),
            p=("outcome", lambda s: (s == "push").sum()),
            pnl=("pnl", "sum"),
            stake=("stake", "sum"),
        ).to_dict(),
    }
    return TotalsBacktestResult(bets_df, eq_df, summary)


def _max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    rm = equity.cummax()
    dd = (equity - rm) / rm
    return float(dd.min())
