"""
backtest_f5_synthetic.py
------------------------
F5 backtest using SYNTHETIC F5 odds derived from full-game moneylines.

The-odds-api's historical endpoint doesn't carry F5 markets, so we can't
fetch real F5 closing lines for 2023-2025. Instead, we estimate F5 odds
from the full-game moneyline using a conservative formula, then backtest
Stage 1 against those estimates.

==================================================================
HOW F5 ODDS RELATE TO FULL-GAME ODDS
==================================================================
F5 markets are a subset of the full game. The same starting pitchers, same
offenses, same park. But the F5 outcome is dominated by the starting pitchers
whereas the full-game outcome also includes bullpens and late-game offense.

Empirically (from published book data), F5 moneylines:
  - Pull toward pick'em vs full-game (because shorter sample = more variance)
  - Add 1-2 percentage points of vig (F5 markets are less liquid)

Our conversion:
  1. Take full-game implied probability (devig using Shin)
  2. Shrink toward 0.50 by factor k (0 = no shrinkage, 1 = total shrinkage)
     - Default k=0.25 approximates published Pinnacle F5 vs ML data
  3. Re-add F5 vig (default 6%, vs full-game 4.5%)
  4. Convert back to decimal odds

==================================================================
CAVEAT — READ THIS BEFORE TRUSTING ANY NUMBER
==================================================================
Synthetic odds != real odds. This backtest tells us whether Stage 1 has
PREDICTIVE edge on F5 outcomes given reasonable market assumptions. It does
NOT tell us the real-world ROI you'd get if you bet F5 with a live book.

Interpretation guide:
  - Positive ROI in all 3 seasons, consistent across seasons
    -> Stage 1 has real F5 predictive signal, worth paper-trading with
       live F5 odds
  - Mixed results or one blowup season
    -> Same pattern as full-game ML; pivot or shelve
  - Zero bets firing
    -> Thresholds too tight OR market too efficient for shrunk odds
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .backtest_f5 import (
    F5BacktestResult,
    _max_drawdown_pct,
    score_f5_conviction,
    walkforward_f5_predict,
)
from .config_f5 import (
    F5_KELLY_FRACTION, F5_MAX_DAILY_RISK_UNITS, F5_MAX_MODEL_PROB,
    F5_MIN_EDGE_PCT, F5_MIN_MODEL_PROB, F5_TIER_SIZES,
)
from .edge_calculator import expected_value, kelly_stake
from .market_analysis import shin, shin_vec

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic-odds conversion
# ---------------------------------------------------------------------------
def synthesize_f5_decimal(home_full_decimal: float,
                          away_full_decimal: float,
                          shrinkage: float = 0.25,
                          f5_vig: float = 0.06) -> Tuple[float, float]:
    """
    Convert (home_full_decimal, away_full_decimal) -> estimated
    (home_f5_decimal, away_f5_decimal).

    shrinkage : 0..1, how much we pull F5 probability toward 50/50. Higher
                values make F5 more pick'em. 0.25 matches published book data
                reasonably well.
    f5_vig    : target vig for the synthetic F5 market. 0.06 = 6%.

    Returns (home_f5_decimal, away_f5_decimal) rounded to 3 d.p.
    Returns (nan, nan) on bad input.

    This scalar form is kept for documentation and for spot-checks; the
    batch path (`add_synthetic_f5_odds`) is fully vectorized and produces
    bitwise-identical numbers.
    """
    if (pd.isna(home_full_decimal) or pd.isna(away_full_decimal)
            or home_full_decimal < 1.05 or away_full_decimal < 1.05):
        return np.nan, np.nan

    # Step 1: devig full-game to fair probabilities via Shin
    p_h_raw = 1.0 / home_full_decimal
    p_a_raw = 1.0 / away_full_decimal
    p_h_fair, p_a_fair = shin(p_h_raw, p_a_raw)
    if pd.isna(p_h_fair) or pd.isna(p_a_fair):
        return np.nan, np.nan

    # Step 2: shrink toward 0.50 (F5 is noisier -> closer to coin flip)
    p_h_f5 = 0.5 + (p_h_fair - 0.5) * (1.0 - shrinkage)
    p_a_f5 = 1.0 - p_h_f5

    # Step 3: re-add F5 vig proportionally (same side-distribution as full game)
    # Total implied after vig = 1.0 + f5_vig
    total = 1.0 + f5_vig
    p_h_book = p_h_f5 * total
    p_a_book = p_a_f5 * total

    # Step 4: convert to decimal
    if p_h_book <= 0 or p_a_book <= 0:
        return np.nan, np.nan
    home_dec = round(1.0 / p_h_book, 3)
    away_dec = round(1.0 / p_a_book, 3)
    return home_dec, away_dec


def add_synthetic_f5_odds(games: pd.DataFrame,
                          shrinkage: float = 0.25,
                          f5_vig: float = 0.06) -> pd.DataFrame:
    """
    Given a games DataFrame with `home_decimal` and `away_decimal` (full-game),
    add `home_f5_decimal` and `away_f5_decimal` columns using synthesis.

    Vectorized: the old per-row `apply(axis=1)` form called into Python N
    times and spent most of its wall-clock on pandas overhead, not math.
    This version runs the four-step conversion (devig, shrink, re-vig,
    invert) as numpy array ops. `shin_vec` is numerically identical to the
    scalar `shin`, so bitwise output matches `synthesize_f5_decimal`.
    """
    if "home_decimal" not in games.columns or "away_decimal" not in games.columns:
        raise ValueError("games frame must have home_decimal/away_decimal "
                         "(run merge_games_and_odds first)")

    g = games.copy()
    h = g["home_decimal"].to_numpy(dtype=float)
    a = g["away_decimal"].to_numpy(dtype=float)

    # Mirror the scalar guards: either-NaN or sub-1.05 decimals collapse to NaN
    # output. shin_vec already propagates NaN, but masking up-front keeps the
    # math out of the invalid rows and avoids spurious warnings.
    valid_in = np.isfinite(h) & np.isfinite(a) & (h >= 1.05) & (a >= 1.05)

    with np.errstate(divide="ignore", invalid="ignore"):
        p_h_raw = np.where(valid_in, 1.0 / h, np.nan)
        p_a_raw = np.where(valid_in, 1.0 / a, np.nan)

    # Step 1: devig
    p_h_fair, p_a_fair = shin_vec(p_h_raw, p_a_raw)

    # Step 2: shrink toward 0.50
    p_h_f5 = 0.5 + (p_h_fair - 0.5) * (1.0 - shrinkage)
    p_a_f5 = 1.0 - p_h_f5

    # Step 3: re-vig
    total = 1.0 + f5_vig
    p_h_book = p_h_f5 * total
    p_a_book = p_a_f5 * total

    # Step 4: invert, round to 3dp (matches scalar form)
    ok = np.isfinite(p_h_book) & np.isfinite(p_a_book) \
        & (p_h_book > 0) & (p_a_book > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        home_dec = np.where(ok, np.round(1.0 / p_h_book, 3), np.nan)
        away_dec = np.where(ok, np.round(1.0 / p_a_book, 3), np.nan)

    g["home_f5_decimal"] = home_dec
    g["away_f5_decimal"] = away_dec
    return g


# ---------------------------------------------------------------------------
# Synthetic-F5 ROI simulator
# ---------------------------------------------------------------------------
def simulate_f5_roi_synthetic(preds: pd.DataFrame,
                              start_bankroll: float = 100.0) -> F5BacktestResult:
    """
    Identical contract to simulate_f5_roi in backtest_f5.py, but reads
    home_f5_decimal / away_f5_decimal that came from synthesis (not real
    odds).

    We DO NOT share the implementation with simulate_f5_roi: the design
    intent is that any future tweaks to synthetic-odds sim should NOT bleed
    back into real-odds sim when we eventually have both available. The
    vectorized shape (shin_vec + dict-records loop) is mirrored from the
    real-odds version but the functions stay distinct.
    """
    if preds.empty:
        return F5BacktestResult(pd.DataFrame(), pd.DataFrame(),
                                {"note": "empty preds"})

    df = preds.sort_values("game_date").reset_index(drop=True)

    home_dec_arr = df["home_f5_decimal"].to_numpy(dtype=float)
    away_dec_arr = df["away_f5_decimal"].to_numpy(dtype=float)
    valid_odds = (
        np.isfinite(home_dec_arr) & np.isfinite(away_dec_arr)
        & (home_dec_arr >= 1.05) & (home_dec_arr <= 10.0)
        & (away_dec_arr >= 1.05) & (away_dec_arr <= 10.0)
    )
    n_drop = int((~valid_odds).sum())
    if n_drop:
        log.warning("Dropping %d synthetic-F5 rows with missing or out-of-range "
                    "decimal odds", n_drop)
    df = df.loc[valid_odds].reset_index(drop=True)
    home_dec_arr = home_dec_arr[valid_odds]
    away_dec_arr = away_dec_arr[valid_odds]

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
        return F5BacktestResult(bets_df, eq_df, {"note": "no synthetic F5 bets"})

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
