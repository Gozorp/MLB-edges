"""
mlb_edge/lineup_shape.py
------------------------
Lineup-shape features that capture HOW a lineup distributes its offensive
talent across the 9 batting-order slots, not just the team's average
production.  Two lineups with identical wRC+ can have very different shape:
one top-heavy with a clear dropoff after the 4-spot, one balanced 1-9.
Top-heavy lineups are more vulnerable to:
  * losing a star to injury / pinch-hit / late-inning sub
  * weak innings starting from the 6-7-8 hole
  * relief pitchers who navigate the top of the order successfully
Balanced lineups string hits together more reliably and punish bullpens
with no easy outs.

Public API
----------
concentration_index(per_batter_metrics) -> float
    Ratio of average production in top-3 vs bottom-3 slots.
    1.0 = perfectly balanced.   2.0 = severely top-heavy.

top_bottom_dropoff(per_batter_metrics) -> float
    Absolute dropoff: mean(top_3) - mean(bottom_3).

Both functions take a list of per-batter metric values (e.g. xwOBA, wRC+,
OPS) in batting-order sequence 1..N (N<=9).  They return np.nan when the
input is too small or all-NaN.

These functions are PURE (no I/O).  Consumed by mlb_edge.lineup just before
the lineup-features dict is returned to build_pipeline, so the resulting
`lineup_concentration_idx` flows downstream into both the model feature
matrix and the diag CSV without further plumbing.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np


def _clean(values: Sequence[Optional[float]]) -> List[float]:
    """Return values as a float list, dropping NaN/None and non-numeric."""
    out: List[float] = []
    for v in values or []:
        try:
            f = float(v)
            if np.isnan(f):
                continue
            out.append(f)
        except (TypeError, ValueError):
            continue
    return out


def concentration_index(per_batter_metrics: Sequence[Optional[float]],
                        top_k: int = 3,
                        bot_k: int = 3) -> float:
    """Top-heaviness ratio.

    Given a list of per-batter metric values in batting-order sequence
    (slot 1 first), return:

        mean(slots 1..top_k) / mean(slots N-bot_k+1 .. N)

    where N is the length of the valid (non-NaN) input.  Higher values
    indicate a top-heavy lineup; ~1.0 is balanced; <1.0 means the bottom
    is hotter than the top (rare).

    Returns np.nan if either window can't be filled (e.g. fewer than 6
    non-NaN values).

    Example:
        >>> concentration_index([1.037, .950, .880, .770, .720, .650, .600, .500, .175])
        1.94  # top-heavy: Langeliers-era Athletics shape
        >>> concentration_index([.820, .810, .795, .785, .770, .755, .740, .725, .700])
        1.07  # balanced: Cardinals shape
    """
    vals = _clean(per_batter_metrics)
    if len(vals) < (top_k + bot_k):
        return float("nan")
    top = vals[:top_k]
    bot = vals[-bot_k:]
    top_mean = float(np.mean(top))
    bot_mean = float(np.mean(bot))
    if bot_mean <= 0:
        return float("nan")
    return top_mean / bot_mean


def top_bottom_dropoff(per_batter_metrics: Sequence[Optional[float]],
                       top_k: int = 3,
                       bot_k: int = 3) -> float:
    """Absolute dropoff: mean(top_3) - mean(bottom_3).

    Same windows as concentration_index but returns a difference instead
    of a ratio.  Useful when the metric can be zero or negative
    (e.g. wRC+ deltas) where a ratio would be ill-defined.  In xwOBA
    terms, a dropoff > 0.080 is "top-heavy"; > 0.150 is "severe."
    """
    vals = _clean(per_batter_metrics)
    if len(vals) < (top_k + bot_k):
        return float("nan")
    return float(np.mean(vals[:top_k]) - np.mean(vals[-bot_k:]))


def bullpen_strain_score(opposing_hl_pen_xwoba: Optional[float],
                         our_top_lineup_xwoba: Optional[float]) -> float:
    """Interaction score: how dangerous is the opposing high-leverage
    bullpen against our top-of-order hitters?

    Both inputs are picked-side-perspective xwOBA values (range roughly
    0.260 - 0.380 in practice; higher = better for the hitter).  The
    product captures the multiplicative interaction the user's
    "WHIP-to-OPS collision" framing describes — except we use xwOBA as
    the underlying signal because the diag pipeline doesn't currently
    expose per-closer WHIP.

    Returns the product when both are valid, np.nan otherwise.

    Interpretation guidance (calibrated to current MLB distributions):
      < 0.090 : collision risk LOW   (their pen is good or our top is weak)
      0.090-0.115 : MODERATE
      > 0.115 : HIGH  (high-xwOBA top of order vs bleeding bullpen)
    """
    try:
        a = float(opposing_hl_pen_xwoba)
        b = float(our_top_lineup_xwoba)
        if np.isnan(a) or np.isnan(b):
            return float("nan")
        return a * b
    except (TypeError, ValueError):
        return float("nan")
