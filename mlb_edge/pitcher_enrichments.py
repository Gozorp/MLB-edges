"""
pitcher_enrichments.py
----------------------
Three Tier-1 pitcher-level features, all computed from the Statcast cache:

  1. SP rest days          - game_date of start minus game_date of prior start
  2. SP velocity drop flag - fastball velo in last start vs season average
  3. SP handedness splits  - xwOBA allowed to LHH and to RHH separately

All features are computed point-in-time as of the game's date (`< game_date`),
so there is no look-ahead leakage. The functions consume the same Statcast
DataFrame already cached in data/statcast_cache/.

Integration: `build_pitcher_enrichments` returns three gap columns ready to
merge into the per-game row built by `build_pipeline._build_game_row`:

  - sp_rest_gap        : (home rest days) - (away rest days). Positive = home
                         has more rest (typically favorable).
  - sp_velo_drop_gap   : (home velo delta) - (away velo delta). Positive =
                         away pitcher is the one losing velocity (bad for
                         them, edge for home).
  - sp_vs_lineup_gap   : (away weighted xwOBA allowed) - (home weighted
                         xwOBA allowed). Positive = home pitcher holds the
                         opposing lineup to a lower xwOBA given its
                         handedness composition.

Every gap returns np.nan when either side lacks sufficient data, so the
XGBoost model handles it via its built-in default-direction learning and
feature frames that pre-date these columns still train cleanly once
`train_stage1_f5` filters `F5_FEATURES` to columns that actually exist.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Rest days
# ---------------------------------------------------------------------------
def compute_rest_days(statcast: pd.DataFrame,
                      pitcher_id: Optional[int],
                      game_date: date) -> Optional[int]:
    """
    Days between `game_date` and this pitcher's previous appearance.

    Returns None if no prior appearance exists in the cache (first start of
    the season, or pitcher ID not present).
    """
    if statcast is None or statcast.empty or pitcher_id is None:
        return None

    mask = (statcast["pitcher"] == pitcher_id)
    pitcher_sc = statcast.loc[mask, ["game_date"]]
    if pitcher_sc.empty:
        return None

    pitcher_sc = pitcher_sc.copy()
    pitcher_sc["game_date"] = pd.to_datetime(pitcher_sc["game_date"]).dt.date
    prior = pitcher_sc[pitcher_sc["game_date"] < game_date]
    if prior.empty:
        return None

    last_game = prior["game_date"].max()
    return (game_date - last_game).days


# ---------------------------------------------------------------------------
# 2. Velocity drop flag
# ---------------------------------------------------------------------------
# Fastball pitch-type codes in Statcast.
FASTBALL_TYPES = ("FF", "SI", "FC", "FT")


def compute_velo_drop(statcast: pd.DataFrame,
                      pitcher_id: Optional[int],
                      game_date: date,
                      min_season_pitches: int = 100,
                      min_last_start_pitches: int = 15) -> Optional[float]:
    """
    Velocity drop (MPH) = (last-start avg fastball velo) - (season avg
    fastball velo prior to that start).

    A NEGATIVE value is a drop (bad for the pitcher). Typical real drops are
    -1 to -3 mph; anything below -1.5 mph is a meaningful red flag.

    Returns None if insufficient data (rookie first start, thin fastball
    usage, etc.).
    """
    if statcast is None or statcast.empty or pitcher_id is None:
        return None

    sc = statcast[
        (statcast["pitcher"] == pitcher_id)
        & (statcast["pitch_type"].isin(FASTBALL_TYPES))
        & statcast["release_speed"].notna()
    ].copy()
    if sc.empty:
        return None

    sc["game_date"] = pd.to_datetime(sc["game_date"]).dt.date
    sc = sc[sc["game_date"] < game_date]
    if len(sc) < min_season_pitches:
        return None

    last_date = sc["game_date"].max()
    last_start = sc[sc["game_date"] == last_date]
    season_prior = sc[sc["game_date"] < last_date]

    if (len(last_start) < min_last_start_pitches
            or len(season_prior) < min_season_pitches):
        return None

    return float(last_start["release_speed"].mean()
                 - season_prior["release_speed"].mean())


# ---------------------------------------------------------------------------
# 3. Handedness splits
# ---------------------------------------------------------------------------
def compute_pitcher_vs_hand(statcast: pd.DataFrame,
                            pitcher_id: Optional[int],
                            game_date: date,
                            min_pa_per_split: int = 50) -> Dict[str, Optional[float]]:
    """
    Pitcher's xwOBA allowed vs LHH and vs RHH, computed point-in-time from
    pitch-level `estimated_woba_using_speedangle`, using only data prior to
    `game_date`.

    Returns {"xwoba_vs_lhh": float|None, "xwoba_vs_rhh": float|None}.
    xwOBA values typically range from .250 (elite) to .400 (disaster).

    If a split has fewer than `min_pa_per_split` plate appearances, the value
    is None for that side - a ~10 PA estimate is noisier than dropping the
    feature.
    """
    out: Dict[str, Optional[float]] = {"xwoba_vs_lhh": None, "xwoba_vs_rhh": None}

    if statcast is None or statcast.empty or pitcher_id is None:
        return out

    sc = statcast[
        (statcast["pitcher"] == pitcher_id)
        & statcast["estimated_woba_using_speedangle"].notna()
        & statcast["stand"].isin(["L", "R"])
    ].copy()
    if sc.empty:
        return out

    sc["game_date"] = pd.to_datetime(sc["game_date"]).dt.date
    sc = sc[sc["game_date"] < game_date]
    if sc.empty:
        return out

    # One row per plate appearance: take the last pitch of each PA (Statcast
    # stores xwOBA on the terminal pitch).
    pa = sc.sort_values(
        ["game_date", "game_pk", "at_bat_number", "pitch_number"]
    ).groupby(["game_pk", "at_bat_number"]).tail(1)

    for hand, key in [("L", "xwoba_vs_lhh"), ("R", "xwoba_vs_rhh")]:
        sub = pa[pa["stand"] == hand]
        if len(sub) >= min_pa_per_split:
            out[key] = float(sub["estimated_woba_using_speedangle"].mean())

    return out


def opposing_lineup_handedness_split(statcast: pd.DataFrame,
                                     team_abbr: str,
                                     game_date: date,
                                     lookback_days: int = 30) -> Tuple[float, float]:
    """
    Estimate what fraction of the opposing team's plate appearances over the
    last `lookback_days` came from LHH vs RHH. Returns (lhh_frac, rhh_frac).

    Used to weight a pitcher's handedness splits. Falls back to a league-wide
    (0.40, 0.60) prior when data is thin.
    """
    if statcast is None or statcast.empty:
        return (0.40, 0.60)

    end = game_date
    start = end - timedelta(days=lookback_days)

    sc = statcast.copy()
    sc["game_date"] = pd.to_datetime(sc["game_date"]).dt.date
    sc = sc[(sc["game_date"] >= start) & (sc["game_date"] < end)]
    if sc.empty:
        return (0.40, 0.60)

    if "home_team" in sc.columns and "away_team" in sc.columns:
        is_home_batting = (sc["home_team"] == team_abbr) & (sc["inning_topbot"] == "Bot")
        is_away_batting = (sc["away_team"] == team_abbr) & (sc["inning_topbot"] == "Top")
        batting = sc[is_home_batting | is_away_batting]
    else:
        return (0.40, 0.60)

    if batting.empty:
        return (0.40, 0.60)

    pa = batting.drop_duplicates(["game_pk", "at_bat_number"])
    pa = pa[pa["stand"].isin(["L", "R"])]
    if len(pa) < 30:
        return (0.40, 0.60)

    lhh_frac = (pa["stand"] == "L").mean()
    return (float(lhh_frac), float(1 - lhh_frac))


def pitcher_matchup_xwoba(pitcher_splits: Dict[str, Optional[float]],
                          opp_lhh_frac: float,
                          opp_rhh_frac: float) -> Optional[float]:
    """
    Weighted xwOBA this pitcher is expected to allow given the opposing
    lineup's handedness composition.

    If only one split is available (small LHH sample for a specialist, etc.),
    that side is used weighted to 1.0. Conservative, but avoids dropping the
    feature entirely when one split is thin.
    """
    l = pitcher_splits.get("xwoba_vs_lhh")
    r = pitcher_splits.get("xwoba_vs_rhh")

    if l is None and r is None:
        return None
    if l is None:
        return r
    if r is None:
        return l
    return opp_lhh_frac * l + opp_rhh_frac * r


# ---------------------------------------------------------------------------
# Consolidated per-game enrichment
# ---------------------------------------------------------------------------
def build_pitcher_enrichments(statcast: pd.DataFrame,
                              home_sp_id: Optional[int],
                              away_sp_id: Optional[int],
                              home_team_abbr: str,
                              away_team_abbr: str,
                              game_date: date) -> Dict[str, float]:
    """
    Returns three Tier-1 gap features ready to merge into the game row.

    All gaps follow the convention "positive = advantage for the home team."

    Output keys:
      sp_rest_gap        - home rest days - away rest days
                           (more home rest = positive gap)
      sp_velo_drop_gap   - home velo delta - away velo delta
                           (positive = away pitcher is down velo, bad for
                           them, edge for home)
      sp_vs_lineup_gap   - away weighted xwOBA - home weighted xwOBA
                           (positive = home SP holds opposing lineup to a
                           lower xwOBA given its handedness composition)

    Any gap whose underlying data is insufficient comes back as np.nan; the
    gradient booster handles the NaN via default-direction learning.
    """
    # Rest days -----------------------------------------------------------
    home_rest = compute_rest_days(statcast, home_sp_id, game_date)
    away_rest = compute_rest_days(statcast, away_sp_id, game_date)
    # Bug-fix 2026-05-08: clip rest_gap to [-20, 20]. A pitcher returning from
    # the IL after 30-40 days produces an unbounded rest_gap that dominates
    # the model. The sign still carries, but the magnitude is capped to a
    # range observed in normal rotation use.
    raw_rest_gap = (
        (home_rest - away_rest)
        if home_rest is not None and away_rest is not None
        else np.nan
    )
    sp_rest_gap = (
        float(np.clip(raw_rest_gap, -20.0, 20.0))
        if pd.notna(raw_rest_gap) else np.nan
    )

    # Velocity drop -------------------------------------------------------
    home_velo = compute_velo_drop(statcast, home_sp_id, game_date)
    away_velo = compute_velo_drop(statcast, away_sp_id, game_date)
    # Positive gap = home edge. If away pitcher is losing velo, that's bad
    # for them, good for home. Velo "deltas" here are negative when a
    # pitcher is dropping, so subtracting (home - away) makes a bigger
    # away drop produce a larger positive gap.
    sp_velo_drop_gap = (
        (home_velo - away_velo)
        if home_velo is not None and away_velo is not None
        else np.nan
    )

    # Handedness matchup --------------------------------------------------
    home_splits = compute_pitcher_vs_hand(statcast, home_sp_id, game_date)
    away_splits = compute_pitcher_vs_hand(statcast, away_sp_id, game_date)

    # Home SP faces the AWAY team's batters; away SP faces the HOME team's.
    away_bat_l, away_bat_r = opposing_lineup_handedness_split(
        statcast, away_team_abbr, game_date
    )
    home_bat_l, home_bat_r = opposing_lineup_handedness_split(
        statcast, home_team_abbr, game_date
    )

    home_sp_expected = pitcher_matchup_xwoba(home_splits, away_bat_l, away_bat_r)
    away_sp_expected = pitcher_matchup_xwoba(away_splits, home_bat_l, home_bat_r)

    # xwOBA allowed is a "high = bad for pitcher" rate. Positive home edge
    # means home SP allows LESS xwOBA than away SP.
    sp_vs_lineup_gap = (
        (away_sp_expected - home_sp_expected)
        if home_sp_expected is not None and away_sp_expected is not None
        else np.nan
    )

    return {
        "sp_rest_gap":      sp_rest_gap,
        "sp_velo_drop_gap": sp_velo_drop_gap,
        "sp_vs_lineup_gap": sp_vs_lineup_gap,
    }
