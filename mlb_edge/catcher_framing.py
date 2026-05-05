"""
catcher_framing.py
------------------
Per-catcher Called-Strike-Above-Expected (CSAE) rating, in percentage points.

Background
----------
MLB Statcast's `zone` column labels every pitch with its location:
    1-9   : inside the rulebook strike zone, divided into a 3x3 grid
    11-14 : "shadow zone" — just outside the rulebook strike zone
    21-29 : further outside ("chase" zone)

A good framing catcher converts shadow-zone pitches into called strikes at
above-league rate. The metric we compute here — CSAE — is exactly that
uplift, expressed in percentage points relative to league average. Elite
framers like J.T. Realmuto sit at +3 to +5 pp; poor framers at -3 to -5 pp.

Why this matters for win probability
------------------------------------
Over ~140 shadow-zone pitches per game, a +3 pp framer converts ~4 extra
balls into strikes. Run-value tables put each stolen strike at ~0.13 runs,
so a +3pp / -3pp framer matchup is ~1 run of expected offense. That's ~3%
on home-team win probability — roughly the same magnitude as a one-tier SP
xERA gap — so leaving `home_catcher_penalty` / `away_catcher_penalty`
hardcoded to 1.0 has been silently losing us meaningful signal.

Point-in-time correctness
-------------------------
`catcher_framing_as_of` uses ONLY pitches with game_date < as_of_date, so
computing this feature for game X never peeks at X's own result. Early
season sample size is small, so we shrink toward league mean 0.0 using the
same empirical-Bayes pattern as `point_in_time._shrink`.

Returning 0.0 on missing data is a deliberate design choice — it represents
"league-average catcher" and is the right default when:
    - no catcher ID was recorded for the game (data gap)
    - catcher has fewer than MIN_SHADOW_PITCHES of history
    - Statcast frame lacks required columns (older seasons)

Zero being the neutral value also means old parquets without this column
can be .fillna(0) on load without distorting training.

API
---
    catcher_framing_as_of(statcast_df, catcher_id, as_of_date) -> float
        Returns CSAE in percentage points, shrunk.
    get_game_catchers(statcast_df, game_pk) -> {'home_catcher','away_catcher'}
        Infers starting catcher per side from the first pitch of each half
        of inning 1. Returns IDs (int) or None.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import EARLY_SEASON_SHRINKAGE_ENABLED

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# League-average called-strike rate in the shadow zone (Statcast zones 11-14).
# Empirically ~0.25 across 2019-2024 (Savant's called-strike-probability model
# yields a similar long-run mean). Used as the pivot for CSAE deltas.
LG_SHADOW_CALLED_STRIKE_RATE: float = 0.25

# League mean for the CSAE score itself (not a rate — it's a delta).
LG_CATCHER_FRAMING_SCORE: float = 0.0

# "Stable sample" size for empirical-Bayes shrinkage. A full-time catcher
# sees ~140 shadow-zone pitches per start. 2000 pitches ~ 14 starts — enough
# history to stop shrinking observed values toward league mean.
CATCHER_STABLE_PITCHES: float = 2000.0

# Minimum shadow-zone pitches required to return anything other than the
# league-mean default. Below this we don't even attempt an estimate — too
# noisy for even shrinkage to rescue.
MIN_SHADOW_PITCHES: int = 50

# Statcast zone values that count as "shadow" (just outside strike zone).
SHADOW_ZONES: tuple = (11, 12, 13, 14)

# Statcast description values that represent a taken pitch (the only ones
# the catcher's framing can actually influence — swung-at pitches are
# outside the framing signal).
TAKEN_DESCRIPTIONS: tuple = ("called_strike", "ball", "blocked_ball")


# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------
def catcher_framing_as_of(statcast_df: pd.DataFrame,
                          catcher_id: Optional[int],
                          as_of_date: pd.Timestamp) -> float:
    """
    Per-catcher CSAE in percentage points, shrunk toward league mean.

    Returns 0.0 (league-average default) for:
        - None / NaN catcher_id
        - fewer than MIN_SHADOW_PITCHES of history
        - missing required Statcast columns (older data)

    The sign convention: positive = framer steals strikes for his pitcher.
    Downstream, this becomes a pitcher-FRIENDLY input, so the home-catcher
    value is subtracted from the away-catcher value to form
    home_catcher_framing_gap (positive = home pitchers favored).
    """
    if catcher_id is None or pd.isna(catcher_id):
        return 0.0
    try:
        cid = int(catcher_id)
    except (TypeError, ValueError):
        return 0.0

    # Guard against cache parquets that don't carry the zone/description
    # columns we need. If a column is missing we silently degrade to the
    # league-average default rather than raise.
    required = {"fielder_2", "zone", "description", "game_date"}
    if not required.issubset(statcast_df.columns):
        return 0.0

    mask = (
        (statcast_df["fielder_2"] == cid) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date)) &
        (statcast_df["zone"].isin(SHADOW_ZONES)) &
        (statcast_df["description"].isin(TAKEN_DESCRIPTIONS))
    )
    df = statcast_df.loc[mask, ["description"]]
    n = len(df)
    if n < MIN_SHADOW_PITCHES:
        return 0.0

    called = (df["description"] == "called_strike").sum()
    rate = called / n
    delta_pp = (rate - LG_SHADOW_CALLED_STRIKE_RATE) * 100.0

    if not EARLY_SEASON_SHRINKAGE_ENABLED:
        return float(delta_pp)

    # Empirical-Bayes shrinkage — same pattern as point_in_time._shrink.
    weight = min(n / CATCHER_STABLE_PITCHES, 1.0)
    return float(weight * delta_pp + (1.0 - weight) * LG_CATCHER_FRAMING_SCORE)


# ---------------------------------------------------------------------------
# Identify each side's starting catcher
# ---------------------------------------------------------------------------
def get_game_catchers(statcast_df: pd.DataFrame,
                      game_pk: int) -> Dict[str, Optional[int]]:
    """
    Return {'home_catcher': id, 'away_catcher': id} for a single game.

    Logic: the catcher is recorded on every pitch via `fielder_2`. For the
    starting catcher we grab the first pitch of each half of inning 1 —
    mid-game catcher changes (pinch-hit or injury) are rare enough that
    tracking them isn't worth the complexity for v1 deployment.

    Side assignment:
        Top of inning 1  -> away batting  -> HOME catcher behind plate
        Bot of inning 1  -> home batting  -> AWAY catcher behind plate
    """
    if statcast_df is None or statcast_df.empty:
        return {"home_catcher": None, "away_catcher": None}
    if "fielder_2" not in statcast_df.columns:
        return {"home_catcher": None, "away_catcher": None}

    game = statcast_df[statcast_df["game_pk"] == game_pk]
    if game.empty:
        return {"home_catcher": None, "away_catcher": None}

    first = game[(game["inning"] == 1) & (game["outs_when_up"] == 0)]
    home_catcher: Optional[int] = None
    away_catcher: Optional[int] = None

    top1 = first[first["inning_topbot"] == "Top"]
    bot1 = first[first["inning_topbot"] == "Bot"]
    if not top1.empty and pd.notna(top1.iloc[0].get("fielder_2")):
        home_catcher = int(top1.iloc[0]["fielder_2"])
    if not bot1.empty and pd.notna(bot1.iloc[0].get("fielder_2")):
        away_catcher = int(bot1.iloc[0]["fielder_2"])

    return {"home_catcher": home_catcher, "away_catcher": away_catcher}
