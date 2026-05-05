"""
market_analysis.py
------------------
Where is Vegas *systematically* wrong?

Vegas is generally extremely efficient — but sub-groups where public narrative
overwhelms signal tend to leak value. Historically documented inefficiencies:
  - Home favorites in day-after-travel spots (overpriced)
  - "Name brand" aces in divisional road starts (overpriced K props)
  - Pythag-overperforming teams in April/May (overpriced ML)
  - Double-underdog + bullpen-fatigued favorite (underpriced dog)

This module studies your historical odds+outcome DataFrame and returns the
cohorts where implied-vs-actual probability gap exceeds a threshold.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Odds math
# ---------------------------------------------------------------------------
def american_to_implied(odds: float) -> float:
    """Convert American moneyline to implied probability (with vig)."""
    if pd.isna(odds):
        return np.nan
    return (-odds) / ((-odds) + 100) if odds < 0 else 100 / (odds + 100)


def devig_two_way(p_a: float, p_b: float) -> tuple[float, float]:
    """
    Remove vig from a two-way market via the proportional (Shin) method's
    simplest form: divide each side by the total. For tighter market
    calibration use shin() below.
    """
    if pd.isna(p_a) or pd.isna(p_b):
        return np.nan, np.nan
    total = p_a + p_b
    if total <= 0:
        return np.nan, np.nan
    return p_a / total, p_b / total


def shin(p_a: float, p_b: float, iters: int = 25) -> tuple[float, float]:
    """
    Shin's model for devigging. Handles favorite-longshot bias better than
    proportional scaling, at the cost of a fixed-point iteration.
    """
    if pd.isna(p_a) or pd.isna(p_b):
        return np.nan, np.nan
    z = 0.02
    for _ in range(iters):
        denom = np.sqrt(z**2 + 4 * (1 - z) * (p_a**2 + p_b**2) / (p_a + p_b))
        z = (denom - z) / (2 * (1 - z) - 1e-9) if (1 - z) != 0 else z
        z = float(np.clip(z, 1e-6, 0.2))
    q_a = (np.sqrt(z**2 + 4 * (1 - z) * p_a**2) - z) / (2 * (1 - z) + 1e-9)
    q_b = (np.sqrt(z**2 + 4 * (1 - z) * p_b**2) - z) / (2 * (1 - z) + 1e-9)
    total = q_a + q_b
    return (q_a / total, q_b / total) if total > 0 else (np.nan, np.nan)


def shin_vec(p_a, p_b, iters: int = 25):
    """
    Vectorized Shin devigger. Same math as `shin()` but operates elementwise
    on arrays; 25 iterations over N games runs in ~N*25 numpy ops instead of
    N calls to the Python scalar version. NaN in either input propagates as
    NaN in both outputs for that row.
    """
    pa = np.asarray(p_a, dtype=float)
    pb = np.asarray(p_b, dtype=float)
    out_a = np.full(pa.shape, np.nan)
    out_b = np.full(pb.shape, np.nan)
    valid = np.isfinite(pa) & np.isfinite(pb)
    if not valid.any():
        return out_a, out_b

    a = pa[valid]
    b = pb[valid]
    z = np.full(a.shape, 0.02)
    for _ in range(iters):
        denom = np.sqrt(z**2 + 4 * (1 - z) * (a**2 + b**2) / (a + b))
        # Scalar shin guards against (1 - z) == 0; with z clipped to [1e-6, 0.2]
        # it never is, so we skip the branch here.
        z = (denom - z) / (2 * (1 - z) - 1e-9)
        z = np.clip(z, 1e-6, 0.2)
    q_a = (np.sqrt(z**2 + 4 * (1 - z) * a**2) - z) / (2 * (1 - z) + 1e-9)
    q_b = (np.sqrt(z**2 + 4 * (1 - z) * b**2) - z) / (2 * (1 - z) + 1e-9)
    total = q_a + q_b
    ok = total > 0
    norm_a = np.where(ok, q_a / np.where(ok, total, 1.0), np.nan)
    norm_b = np.where(ok, q_b / np.where(ok, total, 1.0), np.nan)
    out_a[valid] = norm_a
    out_b[valid] = norm_b
    return out_a, out_b


# ---------------------------------------------------------------------------
# Cohort inefficiency scan
# ---------------------------------------------------------------------------
def cohort_edge(df: pd.DataFrame,
                group_cols: List[str],
                min_n: int = 50) -> pd.DataFrame:
    """
    For each cohort, compute:
       mean_implied_prob : average devigged implied probability
       mean_win_rate     : actual win rate
       edge_pp           : win_rate - implied (in percentage points)
       n                 : sample size
       t_stat            : simple z-score on proportion gap

    Only returns cohorts with n >= min_n.

    Expected columns in df:
       home_implied_prob_devig, home_win (0/1), + the cohort columns
    """
    required = {"home_implied_prob_devig", "home_win"}
    if not required.issubset(df.columns):
        raise ValueError(f"df missing required columns: {required - set(df.columns)}")

    grouped = df.groupby(group_cols).agg(
        mean_implied_prob=("home_implied_prob_devig", "mean"),
        mean_win_rate=("home_win", "mean"),
        n=("home_win", "size"),
    ).reset_index()
    grouped = grouped[grouped["n"] >= min_n].copy()

    grouped["edge_pp"] = grouped["mean_win_rate"] - grouped["mean_implied_prob"]
    # Simple z-score on a proportion difference
    p = grouped["mean_implied_prob"]
    se = np.sqrt(p * (1 - p) / grouped["n"])
    grouped["z_stat"] = grouped["edge_pp"] / se.replace(0, np.nan)

    return grouped.sort_values("edge_pp", ascending=False)


def line_movement_signals(open_close: pd.DataFrame) -> pd.DataFrame:
    """
    Identify 'sharp money' markers — line moves against public ticket counts.
    Schema expected:
       game_id, home_open, home_close, home_ticket_pct, home_money_pct
    Returns a table with a boolean `reverse_line_move` flag and magnitude.
    """
    df = open_close.copy()
    df["line_move"] = df["home_close"] - df["home_open"]
    df["public_leaning_home"] = df["home_ticket_pct"] > 55
    # A "reverse line move" is when the line moves against the public side.
    df["reverse_line_move"] = (
        (df["public_leaning_home"] & (df["line_move"] > 0)) |
        (~df["public_leaning_home"] & (df["line_move"] < 0))
    )
    df["rlm_magnitude"] = np.where(df["reverse_line_move"],
                                   df["line_move"].abs(), 0.0)
    return df


def vegas_vs_model_calibration(df: pd.DataFrame,
                               bins: int = 10) -> pd.DataFrame:
    """
    Calibration table: bin model predictions into deciles and compare to
    observed win rate and Vegas implied. If our model is well-calibrated,
    bin midpoints should line up with win rates.
    """
    required = {"model_prob", "home_implied_prob_devig", "home_win"}
    if not required.issubset(df.columns):
        raise ValueError(f"df missing required columns: {required - set(df.columns)}")

    df = df.copy()
    df["model_bin"] = pd.qcut(df["model_prob"], q=bins, duplicates="drop")
    return (df.groupby("model_bin", observed=True)
              .agg(n=("home_win", "size"),
                   model_mid=("model_prob", "mean"),
                   vegas_mid=("home_implied_prob_devig", "mean"),
                   actual=("home_win", "mean"))
              .reset_index())
