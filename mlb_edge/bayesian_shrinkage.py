"""Bayesian shrinkage of small-sample gap features (Phase 4).

Single import point used by both:
  - production code (when USE_BAYESIAN_SHRINKAGE flips ON, currently False)
  - shadow code (USE_BAYESIAN_SHRINKAGE_SHADOW=True by default)

Shrinkage formula:
    shrunk = (n_eff / (n_eff + tau)) * raw + (tau / (n_eff + tau)) * 0
    n_eff  = min(home_side_sample, away_side_sample)

When n_eff is 0 or NaN, the gap collapses to 0 (the prior). This neutralizes
the "missing-bullpen-data inflates the gap" failure mode that produced the
84.7% / 71.4% / 65.6% inflated cards on the 2026-05-02 slate.

See phase4_bayesian_shrinkage.md for design rationale.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .config import BAYESIAN_SHRINKAGE_CFG

log = logging.getLogger(__name__)


def apply_shrinkage(df: pd.DataFrame, *, in_place: bool = False) -> pd.DataFrame:
    """Apply Bayesian shrinkage to gap features in-place or on a copy.

    Returns the modified DataFrame. When `in_place=False` (default), the
    input is not modified.

    Notes:
      - Missing sample-size columns: skip the entire group (logged at INFO).
      - NaN raw gap: keep NaN (XGBoost handles it natively).
      - NaN sample-size: treated as 0 (full shrinkage to prior).
    """
    out = df if in_place else df.copy()
    for label, gaps, home_n_col, away_n_col, tau in BAYESIAN_SHRINKAGE_CFG["groups"]:
        if home_n_col not in out.columns or away_n_col not in out.columns:
            log.info("[shrinkage:%s] missing sample cols (%s, %s); skipping group",
                     label, home_n_col, away_n_col)
            continue
        h_n = pd.to_numeric(out[home_n_col], errors="coerce").fillna(0)
        a_n = pd.to_numeric(out[away_n_col], errors="coerce").fillna(0)
        n_eff = np.minimum(h_n, a_n)
        weight_raw = n_eff / (n_eff + tau)
        for col in gaps:
            if col not in out.columns:
                continue
            raw = pd.to_numeric(out[col], errors="coerce")
            shrunk = weight_raw * raw  # prior = 0
            shrunk = shrunk.where(raw.notna(), np.nan)
            out[col] = shrunk
    return out


def shrinkage_diagnostics(df: pd.DataFrame) -> dict:
    """Per-group counts of how many rows are at full-weight, zero-effective,
    and median weight. Useful for the daily shadow-log entry."""
    diag = {}
    for label, gaps, home_n_col, away_n_col, tau in BAYESIAN_SHRINKAGE_CFG["groups"]:
        if home_n_col not in df.columns or away_n_col not in df.columns:
            continue
        h_n = pd.to_numeric(df[home_n_col], errors="coerce").fillna(0)
        a_n = pd.to_numeric(df[away_n_col], errors="coerce").fillna(0)
        n_eff = np.minimum(h_n, a_n)
        weight_raw = n_eff / (n_eff + tau)
        diag[label] = {
            "tau": int(tau),
            "n_total": int(len(df)),
            "n_full_weight": int((n_eff >= tau).sum()),
            "n_zero_eff": int((n_eff <= 1e-6).sum()),
            "median_weight": float(weight_raw.median()) if len(df) else float("nan"),
        }
    return diag
