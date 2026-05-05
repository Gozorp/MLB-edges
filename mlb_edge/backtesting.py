"""
backtesting.py
--------------
Simulate the full pipeline on historical data with zero future leakage.

Contract:
  - `fit_and_predict_walk_forward` retrains at each fold boundary and predicts
    ONLY the held-out future games. There is no cross-fold contamination.
  - Kelly stakes are computed using the bankroll *at the time of the bet*.
  - Every bet that passes the conviction + edge filter is recorded with its
    resolved outcome so you can slice P&L by tier, by signal family, by
    market (home vs away), by month, etc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import (
    KELLY_FRACTION,
    MAX_DAILY_RISK_UNITS,
    MAX_EDGE_PCT,
    MIN_EDGE_PCT,
    MIN_FAIR_PROB,
    TIER_SIZES,
)
from .edge_calculator import (
    expected_value,
    kelly_stake,
    score_conviction,
)
from .market_analysis import shin_vec
from .model import (
    F5_FEATURES,
    FULL_FEATURES_EXTRA,
    TrainedModel,
    predict,
    time_series_cv,
    train_stage1_f5,
    train_stage2_full,
)

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    bets: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: Dict


def fit_and_predict_walk_forward(df: pd.DataFrame,
                                 n_splits: int = 6) -> pd.DataFrame:
    """
    Walk-forward training. For each time-series fold:
      1. Fit Stage 1 on train, Stage 2 on train.
      2. Predict on valid.
      3. Concatenate all valid predictions into a single DataFrame.

    The caller can then run the edge/bet sim on the resulting frame.
    """
    out_frames = []
    for fold_idx, (tr, va) in enumerate(time_series_cv(df, n_splits=n_splits)):
        log.info("Fold %d: train %d, valid %d", fold_idx, len(tr), len(va))
        try:
            m1 = train_stage1_f5(tr, valid=va)
            m2 = train_stage2_full(tr, m1, valid=va)
        except Exception as e:
            log.error("Fold %d training failed: %s", fold_idx, e)
            continue
        preds = predict(m1, m2, va)
        preds["fold"] = fold_idx
        out_frames.append(preds)
    if not out_frames:
        return pd.DataFrame()
    return pd.concat(out_frames, ignore_index=True)


def simulate_roi(preds: pd.DataFrame,  # noqa: C901  (kept flat for profile)
                 odds: pd.DataFrame,
                 start_bankroll: float = 100.0,
                 min_edge: Optional[float] = None) -> BacktestResult:
    """
    Simulate Kelly-sized betting on the predictions.

    preds must contain:
        game_id, game_date, model_prob, home_team, away_team, home_win,
        plus all conviction-signal columns.

    odds must contain (long form):
        game_id, market=h2h, outcome (team name), decimal (decimal odds)
    """
    if preds.empty:
        return BacktestResult(pd.DataFrame(), pd.DataFrame(), {"note": "empty preds"})

    # Collapse duplicate (game, outcome) rows (in case the caller hasn't
    # pre-medianed) and resolve home/away decimals via two simple merges
    # instead of a pivot+per-row column lookup. O(N log N) vs the old
    # row-wise lookup.
    h2h_med = (odds.loc[odds["market"] == "h2h",
                        ["game_id", "outcome", "decimal"]]
                   .groupby(["game_id", "outcome"], sort=False)["decimal"]
                   .median().reset_index())
    df = (preds.merge(
              h2h_med.rename(columns={"outcome": "home_team",
                                      "decimal": "home_dec"}),
              on=["game_id", "home_team"], how="left")
               .merge(
              h2h_med.rename(columns={"outcome": "away_team",
                                      "decimal": "away_dec"}),
              on=["game_id", "away_team"], how="left")
               .sort_values("game_date")
               .reset_index(drop=True))

    # Vectorized sanity filter. Realistic MLB moneyline decimals live in
    # roughly [1.10, 6.0]; anything outside [1.05, 10.0] is upstream bug
    # residue. Drop the whole set in one pass instead of per-row.
    home_dec_arr = df["home_dec"].to_numpy(dtype=float)
    away_dec_arr = df["away_dec"].to_numpy(dtype=float)
    valid_odds = (
        np.isfinite(home_dec_arr) & np.isfinite(away_dec_arr)
        & (home_dec_arr >= 1.05) & (home_dec_arr <= 10.0)
        & (away_dec_arr >= 1.05) & (away_dec_arr <= 10.0)
    )
    n_drop = int((~valid_odds).sum())
    if n_drop:
        log.warning("Dropping %d games with missing or out-of-range decimal odds", n_drop)
    df = df.loc[valid_odds].reset_index(drop=True)
    home_dec_arr = home_dec_arr[valid_odds]
    away_dec_arr = away_dec_arr[valid_odds]

    # Vectorized devig + side selection + edge for every surviving game.
    # Only rows that pass the edge filter need the per-row conviction pass.
    model_probs = df["model_prob"].to_numpy(dtype=float)
    home_wins = df["home_win"].to_numpy()
    p_home_fair_arr, _ = shin_vec(1.0 / home_dec_arr, 1.0 / away_dec_arr)

    is_home_side = model_probs >= 0.5
    side_dec_arr = np.where(is_home_side, home_dec_arr, away_dec_arr)
    side_prob_arr = np.where(is_home_side, model_probs, 1.0 - model_probs)
    side_fair_arr = np.where(is_home_side, p_home_fair_arr, 1.0 - p_home_fair_arr)
    edge_arr = side_prob_arr - side_fair_arr
    # v8: add MAX_EDGE_PCT ceiling (false-extreme filter) and MIN_FAIR_PROB
    # floor (don't chase underdogs the market already prices as longshots).
    # Backtest 2023-25 pooled: edge>10pp → -36% ROI; fair<0.30 → -55% ROI;
    # edge[5,10] + fair>=0.45 → +11% ROI consistently across all 3 seasons.
    effective_min_edge = MIN_EDGE_PCT if min_edge is None else min_edge
    consider = (
        np.isfinite(side_fair_arr)
        & (edge_arr >= effective_min_edge)
        & (edge_arr <= MAX_EDGE_PCT)
        & (side_fair_arr >= MIN_FAIR_PROB)
    )

    # Per-row sequential stage: conviction, Kelly sizing, daily-cap clamp,
    # bankroll accumulation. dict.get() on a plain dict is ~10x faster than
    # pandas Series.get inside a hot loop, so we pre-materialize records.
    bankroll = start_bankroll
    bets: List[Dict] = []
    equity_points: List[Dict] = []
    daily_risk: Dict = {}
    cap_dollars = (MAX_DAILY_RISK_UNITS / 100.0) * start_bankroll

    records = df.to_dict("records")
    for i, r in enumerate(records):
        if not consider[i]:
            continue

        if is_home_side[i]:
            side = "home"
            side_team = r["home_team"]
        else:
            side = "away"
            side_team = r["away_team"]
        dec = float(side_dec_arr[i])
        prob = float(side_prob_arr[i])
        fair = float(side_fair_arr[i])
        edge = float(edge_arr[i])

        # Conviction — build row perspective, flipping away-side gaps.
        perspective = dict(r)
        if side == "away":
            for col in ("sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                        "sp_siera_gap", "sp_fip_gap"):
                if col in perspective:
                    perspective[col] = -perspective[col]
            perspective["home_sp_luck"], perspective["away_sp_luck"] = (
                perspective.get("away_sp_luck"), perspective.get("home_sp_luck"),
            )
        conviction = score_conviction(perspective)
        mult = TIER_SIZES[conviction.tier]
        if mult == 0:
            continue

        stake_frac = kelly_stake(prob, dec, fraction=KELLY_FRACTION) * mult
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

        won = (home_wins[i] == 1 and side == "home") or \
              (home_wins[i] == 0 and side == "away")
        pnl = stake * (dec - 1) if won else -stake
        bankroll += pnl

        bets.append({
            "game_id":   r["game_id"],
            "game_date": r["game_date"],
            "side":      side,
            "team":      side_team,
            "decimal":   dec,
            "prob":      prob,
            "fair":      fair,
            "edge_pp":   edge * 100,
            "ev":        expected_value(prob, dec),
            "tier":      conviction.tier,
            "signals":   ", ".join(conviction.signals_fired),
            "stake":     stake,
            "won":       won,
            "pnl":       pnl,
            "bankroll":  bankroll,
        })
        equity_points.append({"game_date": r["game_date"], "bankroll": bankroll})

    bets_df = pd.DataFrame(bets)
    eq_df = pd.DataFrame(equity_points)

    if bets_df.empty:
        return BacktestResult(bets_df, eq_df, {"note": "no bets"})

    summary = {
        "n_bets": len(bets_df),
        "win_rate": float(bets_df["won"].mean()),
        "total_pnl": float(bets_df["pnl"].sum()),
        "roi_pct": float(bets_df["pnl"].sum() / bets_df["stake"].sum() * 100),
        "starting_bankroll": start_bankroll,
        "ending_bankroll": bankroll,
        "max_drawdown_pct": float(_max_drawdown_pct(eq_df["bankroll"]) * 100)
                            if not eq_df.empty else 0.0,
        "by_tier": bets_df.groupby("tier").agg(
            n=("won", "size"),
            wr=("won", "mean"),
            pnl=("pnl", "sum"),
            roi=("pnl", lambda s: s.sum() / max(bets_df.loc[s.index, "stake"].sum(), 1e-9)),
        ).to_dict(),
    }
    return BacktestResult(bets_df, eq_df, summary)


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min())
