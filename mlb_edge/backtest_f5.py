"""
backtest_f5.py
--------------
F5 (first-5-innings) moneyline backtest.

Key architectural difference from backtesting.py:
  - Uses Stage 1 F5 model output DIRECTLY, not passed through Stage 2.
    In full-game ML, Stage 2 refines Stage 1 with offense/bullpen/context.
    For F5 ML, only innings 1-5 matter — bullpen and late-game context are
    irrelevant. Stage 1 IS the model for F5.

  - Walk-forward refits Stage 1 only. No Stage 2.

  - Conviction filter uses F5-specific thresholds from config_f5.py.

  - Target column is home_f5_win (already in the feature frame).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config_f5 import (
    F5_CONVICTION, F5_KELLY_FRACTION, F5_MAX_DAILY_RISK_UNITS,
    F5_MAX_MODEL_PROB, F5_MIN_EDGE_PCT, F5_MIN_MODEL_PROB, F5_TIER_SIZES,
)
from .edge_calculator import expected_value, kelly_stake
from .market_analysis import shin_vec
from .model import F5_FEATURES, train_stage1_f5, time_series_cv

log = logging.getLogger(__name__)


@dataclass
class F5BacktestResult:
    bets: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: Dict


# ---------------------------------------------------------------------------
# F5 conviction scoring
# ---------------------------------------------------------------------------
def score_f5_conviction(row) -> Tuple[str, List[str]]:
    """
    Evaluate the 4 signals with F5-specific thresholds.

    Accepts either a pandas Series or a plain dict — both expose `.get()`
    with the same fallback semantics, which lets the simulator call this
    from inside a `df.to_dict("records")` loop without building a Series
    per row.

    Returns (tier, signals_fired).
    """
    signals: List[str] = []

    # F1 - SP xERA gap (primary signal for F5)
    xera = row.get("sp_xera_gap", np.nan)
    if pd.notna(xera) and xera >= F5_CONVICTION.xera_gap_min:
        signals.append(f"F1_xera={xera:.2f}")

    # F2 - team xwOBA gap (less weight for F5)
    xwoba = row.get("team_woba_gap", np.nan)
    if pd.notna(xwoba) and xwoba >= F5_CONVICTION.xwoba_gap_min:
        signals.append(f"F2_xwoba={xwoba:.3f}")

    # F3 - swing-take (smaller sample in 5 innings)
    stake_gap = row.get("swing_take_gap", np.nan)
    if pd.notna(stake_gap) and stake_gap >= F5_CONVICTION.swing_take_gap_min:
        signals.append(f"F3_stake={stake_gap:.1f}")

    # F4 - pitcher luck
    our_luck = row.get("home_sp_luck", np.nan)
    opp_luck = row.get("away_sp_luck", np.nan)
    f4_fired = False
    if pd.notna(our_luck) and our_luck >= -F5_CONVICTION.pitcher_luck_max:
        signals.append(f"F4_our_unlucky={our_luck:.2f}")
        f4_fired = True
    if pd.notna(opp_luck) and opp_luck <= F5_CONVICTION.pitcher_luck_max:
        signals.append(f"F4_opp_lucky={opp_luck:.2f}")
        f4_fired = True

    fired_families = {s.split("_")[0] for s in signals}

    if len(fired_families) >= 3:
        tier = "DIAMOND"
    elif len(fired_families) == 2:
        tier = "PLATINUM"
    elif len(fired_families) == 1 and f4_fired:
        tier = "GOLD"
    else:
        tier = "SKIP"
    return tier, signals


# ---------------------------------------------------------------------------
# Walk-forward Stage-1 predictor (single-stage model for F5)
# ---------------------------------------------------------------------------
def walkforward_f5_predict(df: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    """
    Walk-forward fit Stage 1 only and produce per-row F5 probability.
    Returns concatenated valid-fold predictions with `f5_prob` column.
    """
    out_frames = []
    for i, (tr, va) in enumerate(time_series_cv(df, n_splits=n_splits)):
        log.info("F5 Fold %d: train %d, valid %d", i, len(tr), len(va))
        try:
            m = train_stage1_f5(tr, valid=va)
        except Exception as e:
            log.error("F5 fold %d failed: %s", i, e)
            continue
        preds = va.copy()
        preds["f5_prob"] = m.booster.predict_proba(va[m.feature_cols].values)[:, 1]
        preds["fold"] = i
        out_frames.append(preds)
    return pd.concat(out_frames, ignore_index=True) if out_frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# ROI simulation
# ---------------------------------------------------------------------------
def simulate_f5_roi(preds: pd.DataFrame,
                    start_bankroll: float = 100.0) -> F5BacktestResult:
    """
    Simulate F5 moneyline betting.

    preds must contain:
      game_id, game_date, home_team, away_team, f5_prob, home_f5_win,
      home_f5_decimal, away_f5_decimal,
      plus all conviction-signal columns.

    Vectorization matches the full-game `backtesting.simulate_roi`:
    pre-filter + side selection + devig + edge run as numpy ops across the
    whole frame; only rows that pass the edge filter enter the per-row
    conviction/Kelly/daily-cap loop. Inner loop operates on a list of plain
    dicts (`df.to_dict("records")`) because `dict.get` is ~10× faster than
    `Series.get` inside a hot loop.
    """
    if preds.empty:
        return F5BacktestResult(pd.DataFrame(), pd.DataFrame(),
                                {"note": "empty preds"})

    df = (preds.sort_values("game_date")
                .reset_index(drop=True))

    home_dec_arr = df["home_f5_decimal"].to_numpy(dtype=float)
    away_dec_arr = df["away_f5_decimal"].to_numpy(dtype=float)
    valid_odds = (
        np.isfinite(home_dec_arr) & np.isfinite(away_dec_arr)
        & (home_dec_arr >= 1.05) & (home_dec_arr <= 10.0)
        & (away_dec_arr >= 1.05) & (away_dec_arr <= 10.0)
    )
    n_drop = int((~valid_odds).sum())
    if n_drop:
        log.warning("Dropping %d F5 rows with missing or out-of-range decimal odds",
                    n_drop)
    df = df.loc[valid_odds].reset_index(drop=True)
    home_dec_arr = home_dec_arr[valid_odds]
    away_dec_arr = away_dec_arr[valid_odds]

    # Vectorized devig, side selection, edge.
    model_probs = df["f5_prob"].to_numpy(dtype=float)
    home_f5_wins = df["home_f5_win"].to_numpy()
    p_home_fair_arr, _ = shin_vec(1.0 / home_dec_arr, 1.0 / away_dec_arr)

    is_home_side = model_probs >= 0.5
    side_dec_arr = np.where(is_home_side, home_dec_arr, away_dec_arr)
    side_prob_arr = np.where(is_home_side, model_probs, 1.0 - model_probs)
    side_fair_arr = np.where(is_home_side, p_home_fair_arr, 1.0 - p_home_fair_arr)
    edge_arr = side_prob_arr - side_fair_arr

    consider = (
        np.isfinite(side_fair_arr)
        & (edge_arr >= F5_MIN_EDGE_PCT)
        & (side_prob_arr >= F5_MIN_MODEL_PROB)
        & (side_prob_arr <= F5_MAX_MODEL_PROB)
    )

    bankroll = start_bankroll
    bets: List[Dict] = []
    equity: List[Dict] = []
    daily_risk: Dict = {}
    cap_dollars = (F5_MAX_DAILY_RISK_UNITS / 100.0) * start_bankroll

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

        # Conviction — build row perspective, flipping away-side signed gaps.
        perspective = dict(r)
        if side == "away":
            for col in ("sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                        "sp_siera_gap", "sp_fip_gap"):
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

        won = (home_f5_wins[i] == 1 and side == "home") or \
              (home_f5_wins[i] == 0 and side == "away")
        pnl = stake * (dec - 1) if won else -stake
        bankroll += pnl

        bets.append({
            "game_id":   r.get("game_id"),
            "game_date": r["game_date"],
            "side":      side,
            "team":      side_team,
            "decimal":   dec,
            "prob":      prob,
            "fair":      fair,
            "edge_pp":   edge * 100,
            "ev":        expected_value(prob, dec),
            "tier":      tier,
            "signals":   ", ".join(signals_fired),
            "stake":     stake,
            "won":       won,
            "pnl":       pnl,
            "bankroll":  bankroll,
        })
        equity.append({"game_date": r["game_date"], "bankroll": bankroll})

    bets_df = pd.DataFrame(bets)
    eq_df = pd.DataFrame(equity)

    if bets_df.empty:
        return F5BacktestResult(bets_df, eq_df, {"note": "no F5 bets"})

    summary = {
        "n_bets":           len(bets_df),
        "win_rate":         float(bets_df["won"].mean()),
        "total_pnl":        float(bets_df["pnl"].sum()),
        "roi_pct":          float(bets_df["pnl"].sum() / bets_df["stake"].sum() * 100),
        "starting_bankroll": start_bankroll,
        "ending_bankroll":  bankroll,
        "max_drawdown_pct": float(_max_drawdown_pct(eq_df["bankroll"]) * 100)
                            if not eq_df.empty else 0.0,
        "by_tier":          bets_df.groupby("tier").agg(
            n=("won", "size"),
            wr=("won", "mean"),
            pnl=("pnl", "sum"),
            stake=("stake", "sum"),
        ).to_dict(),
    }
    return F5BacktestResult(bets_df, eq_df, summary)


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    rm = equity.cummax()
    dd = (equity - rm) / rm
    return float(dd.min())
