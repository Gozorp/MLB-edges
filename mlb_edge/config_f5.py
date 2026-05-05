"""
config_f5.py
------------
F5 (first-5-innings) specific configuration.

Why a separate file: F5 markets have materially different odds ranges and
conviction dynamics than full-game ML. Mixing them into config.py would mean
every threshold needs a qualifier. Better to keep them isolated.

Thresholds below are INITIAL guesses. We deliberately start conservative
(tighter than full-game) because F5 markets are thinner and edge signals
could be more noise-dominated. If 2023-2025 backtests show the model calls
are more reliable than assumed, loosen them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


# ---------------------------------------------------------------------------
# F5 conviction thresholds
# ---------------------------------------------------------------------------
# In F5 markets, the signal hierarchy changes:
#   F1 (SP xERA gap) becomes MORE important — it's nearly the whole market
#   F2 (team xwOBA gap) becomes LESS important — only 5 innings to express
#   F3 (swing-take) LESS important — smaller PA sample in 5 innings
#   F4 (pitcher luck regression) stays relevant
#
# So we keep F1 threshold tight, loosen F2/F3 slightly (fewer signal sources
# available), keep F4 the same.

@dataclass(frozen=True)
class F5ConvictionThresholds:
    xera_gap_min: float = 0.75        # F1: unchanged — still primary
    xwoba_gap_min: float = 0.015      # F2: slightly looser (was 0.020)
    swing_take_gap_min: float = 10.0  # F3: looser (was 15.0) — shorter sample
    pitcher_luck_max: float = -1.0    # F4: unchanged


F5_CONVICTION = F5ConvictionThresholds()


# ---------------------------------------------------------------------------
# F5 market thresholds
# ---------------------------------------------------------------------------
# F5 odds run wider than full-game because books charge more juice.
# Typical F5 ML: -140 / +120 with ~6% vig vs full-game's ~4%.
# Implications:
#   - MIN_EDGE_PCT should be higher (harder to find real edge through juice)
#   - MAX_MODEL_PROB can be a bit tighter (no 80% F5 favorites are realistic)

F5_MIN_EDGE_PCT: float = 0.04           # was 0.03 full-game; harder through juice
F5_MIN_MODEL_PROB: float = 0.46         # F5 has more ties-push, so bounds tighter
F5_MAX_MODEL_PROB: float = 0.68
F5_KELLY_FRACTION: float = 0.20         # more conservative — F5 edges thinner
F5_MAX_DAILY_RISK_UNITS: float = 5.0    # was 7.0


# ---------------------------------------------------------------------------
# F5 tier sizing — start stricter than full-game
# ---------------------------------------------------------------------------
# We learned from full-game ML that PLATINUM often tracks noise. Starting
# F5 with PLATINUM = 0 forces us to validate that DIAMOND alone has edge
# before adding noisier tiers back in.
F5_TIER_SIZES: Dict[str, float] = {
    "DIAMOND": 1.00,
    "PLATINUM": 0.00,
    "GOLD": 0.00,
    "SKIP": 0.00,
}
