"""
point_in_time.py
----------------
Compute pitcher and team stats as-of a given date using ONLY games before
that date. This is the leak-prevention layer for backtesting.

The naive approach — "use season-total xERA for every game" — includes games
played AFTER the one being predicted, which cheats. Every cumulative stat
here is computed from pitch-level Statcast data in strict chronological
order with a `.shift(1)` before any aggregation that touches the game's
own date.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import (
    AVG_BULLPEN_PITCHES_PER_GAME,
    AVG_PA_PER_GAME,
    BULLPEN_STABLE_GAMES,
    EARLY_SEASON_SHRINKAGE_ENABLED,
    LG_BB_PCT,
    LG_BULLPEN_XERA,
    LG_BULLPEN_XWOBA,
    LG_HARDHIT_PCT,
    LG_K_PCT,
    LG_SP_BB_PCT,
    LG_SP_HARDHIT_PCT,
    LG_SP_K_BB_PCT,
    LG_SP_K_PCT,
    LG_SP_TTOP3_PENALTY,
    LG_SP_XERA,
    LG_SP_XWOBA,
    LG_WOBA,
    LG_WRC_PLUS,
    LG_XWOBA,
    SP_PRIOR_YEAR_MIN_PITCHES,
    SP_STABLE_PITCHES,
    TEAM_STABLE_GAMES,
    BP_PRIOR_YEAR_MIN_PITCHES,
    BP_STABLE_PITCHES,
    BP_STABLE_PITCHES_K_BB,
    LG_BULLPEN_K_PCT,
    LG_BULLPEN_BB_PCT,
    LG_BULLPEN_HARDHIT_PCT,
)

log = logging.getLogger(__name__)


# ===========================================================================
# Early-season shrinkage
# ===========================================================================
def _shrink(observed: float, league_mean: float,
            sample: float, stable_sample: float) -> float:
    """
    Linear blend of `observed` toward `league_mean`, with weight proportional
    to accumulated sample size.

        weight = min(sample / stable_sample, 1.0)
        shrunk = weight * observed + (1 - weight) * league_mean

    Below `stable_sample` we trust the observed value less; at or above it,
    the function is a no-op (returns `observed` unchanged). NaN observed
    values pass through unchanged — they indicate the upstream min-sample
    floor already failed and the caller should keep the NaN signal.

    Disabling shrinkage (via `EARLY_SEASON_SHRINKAGE_ENABLED = False`) makes
    this a pass-through, preserving prior model behavior for A/B comparison.
    """
    if not EARLY_SEASON_SHRINKAGE_ENABLED:
        return observed
    if pd.isna(observed) or stable_sample <= 0:
        return observed
    w = min(max(sample, 0.0) / stable_sample, 1.0)
    return float(w * observed + (1.0 - w) * league_mean)


# ===========================================================================
# Pitcher prior — last full season as a Bayesian prior for shrinkage
# ===========================================================================
def _pitcher_prior_year(statcast_df: pd.DataFrame,
                        pitcher_id: int,
                        as_of_date: pd.Timestamp) -> Dict[str, float]:
    """
    Return the pitcher's stats from the PRIOR calendar year as a Bayesian
    prior for early-season shrinkage. If the pitcher didn't pitch last year
    OR the prior-year sample is below `SP_PRIOR_YEAR_MIN_PITCHES`, return
    an empty dict so the caller falls back to the league mean.

    This is the fix for the 2026-04-25 BAL miss: Garrett Crochet's 2026
    sample was 4 starts of 7.88 ERA, but his 2025 was Cy Young-caliber.
    Blending against his own 2025 baseline anchors his "true talent"
    estimate against the right reference point.
    """
    target_year = pd.Timestamp(as_of_date).year - 1
    yr_start = pd.Timestamp(f"{target_year}-01-01")
    yr_end = pd.Timestamp(f"{target_year}-12-31")

    df = statcast_df[
        (statcast_df["pitcher"] == pitcher_id) &
        (pd.to_datetime(statcast_df["game_date"]) >= yr_start) &
        (pd.to_datetime(statcast_df["game_date"]) <= yr_end)
    ]
    if len(df) < SP_PRIOR_YEAR_MIN_PITCHES:
        return {}

    xwoba_num = df["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = df["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan
    xera = -12.5 + 45.0 * xwoba if pd.notna(xwoba) else np.nan

    pa = df[df["events"].notna()]
    n_pa = len(pa)
    if n_pa == 0:
        return {}
    k_rate = (pa["events"] == "strikeout").sum() / n_pa
    bb_rate = (pa["events"] == "walk").sum() / n_pa
    k_bb_pct = (k_rate - bb_rate) * 100.0

    bip = df.dropna(subset=["launch_speed"])
    hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0 if len(bip) > 0 else np.nan

    return {
        "sp_xera": xera,
        "sp_xwoba_allowed": xwoba,
        "sp_k_bb_pct": k_bb_pct,
        "sp_k_pct": k_rate * 100.0,
        "sp_bb_pct": bb_rate * 100.0,
        "sp_hardhit_pct_allowed": hardhit_pct,
        "n_pitches": len(df),
    }


def _shrink_to_prior(observed: float, prior: float,
                     n_pitches: float, stable: float) -> float:
    """
    Blend `observed` toward `prior` with weight = min(n_pitches/stable, 1).
    Mirrors `_shrink()` but lets the caller pick a pitcher-specific prior
    instead of always using a league mean.
    """
    if not EARLY_SEASON_SHRINKAGE_ENABLED:
        return observed
    if pd.isna(observed) or stable <= 0:
        return observed
    if pd.isna(prior):
        return observed
    w = min(max(n_pitches, 0.0) / stable, 1.0)
    return float(w * observed + (1.0 - w) * prior)


# ===========================================================================
# Pitcher stats
# ===========================================================================
def pitcher_as_of(statcast_df: pd.DataFrame,
                  pitcher_id: int,
                  as_of_date: pd.Timestamp,
                  min_pitches: int = 100) -> Dict[str, float]:
    """
    Return a point-in-time stat dict for a pitcher.

    Pulls all pitches thrown by `pitcher_id` on games with game_date STRICTLY
    BEFORE `as_of_date`, then rolls up the canonical metrics we need.

    Required Statcast columns:
        pitcher, game_date, events, description, estimated_woba_using_speedangle,
        estimated_ba_using_speedangle, launch_speed, launch_angle,
        woba_value, woba_denom, babip_value
    """
    df = statcast_df[
        (statcast_df["pitcher"] == pitcher_id) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    ]
    if len(df) < min_pitches:
        # Below the stable-rate threshold: rate stats stay NaN (too noisy
        # to report), but surface the TRUE pitch count so a thin arm reads
        # "85", not "0", and a genuine 0 stays 0. Leaves the model feature
        # sp_n_pitches and the SP-savant gate untouched. (fix 2026-05-29)
        _thin = _nan_pitcher_dict()
        _thin["sp_n_pitches_actual"] = float(len(df))
        return _thin

    # xwOBA allowed (weighted on-base)
    xwoba_num = df["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = df["woba_denom"].fillna(0).sum()
    xwoba_allowed = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan

    # K and BB rates
    # Strikeouts in Statcast appear as events=='strikeout'
    pa = df[df["events"].notna()]
    n_pa = len(pa)
    if n_pa == 0:
        return _nan_pitcher_dict()
    k_rate = (pa["events"] == "strikeout").sum() / n_pa
    bb_rate = (pa["events"] == "walk").sum() / n_pa
    k_bb_pct = (k_rate - bb_rate) * 100.0

    # xERA proxy: linear transform of xwOBA allowed. The canonical mapping
    # (Savant): xERA ≈ -12.5 + 45 * xwOBA_allowed. This is the standard
    # league-wide approximation Baseball Savant uses for pitcher xERA.
    xera = -12.5 + 45.0 * xwoba_allowed if pd.notna(xwoba_allowed) else np.nan

    # Hard-hit rate (EV >= 95 on batted balls)
    bip = df.dropna(subset=["launch_speed"])
    if len(bip) > 0:
        hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0
    else:
        hardhit_pct = np.nan

    # FIP & SIERA approximations from Statcast (full formulas need league constants
    # and HR/9, BB/9, K/9 which require IP. We approximate: use season FG estimate
    # if available, else fall back to xERA as a proxy.)
    # For this backtest, sub-stats are acceptable proxies; gradient boosting picks
    # up the signal from xERA and K-BB% regardless.

    # Recent-form xFIP proxy: xERA over the most recent 30 days
    recent_cutoff = pd.Timestamp(as_of_date) - pd.Timedelta(days=30)
    recent = df[pd.to_datetime(df["game_date"]) >= recent_cutoff]
    if len(recent) >= 50:
        r_xwoba_num = recent["estimated_woba_using_speedangle"].fillna(0).sum()
        r_xwoba_den = recent["woba_denom"].fillna(0).sum()
        r_xwoba = r_xwoba_num / r_xwoba_den if r_xwoba_den > 0 else np.nan
        recent_xfip = -12.5 + 45.0 * r_xwoba if pd.notna(r_xwoba) else np.nan
    else:
        recent_xfip = xera  # fall back to season

    # IP per start: from number of distinct games started
    games = df.groupby("game_date").size()
    ip_per_start = (len(df) / 15.0) / max(len(games), 1)  # ~15 pitches per inning
    # (This is approximate — for a precise value we'd parse inning transitions.)

    # ERA - xERA gap (luck flag). Compute ERA from earned runs / IP.
    # Statcast doesn't give clean earned runs per-pitch. We approximate by
    # looking at woba_value (which contains run expectancy) aggregated.
    era_proxy = _approximate_era(df)
    era_xera_gap = era_proxy - xera if pd.notna(era_proxy) and pd.notna(xera) else np.nan

    # Third-time-through-order (TTOP3) penalty. Within each game we rank
    # plate appearances by (game_pk, batter) → 1st PA = TTOP1, 2nd = TTOP2,
    # 3rd+ = TTOP3. The penalty is xwOBA(TTOP3+) - xwOBA(TTOP1). Positive
    # means the pitcher gets noticeably worse the third time the lineup
    # sees him. NaN when either bucket has < 30 PA.
    ttop3_penalty = np.nan
    pa_full = df[df["events"].notna()].copy()
    if len(pa_full) >= 100 and "batter" in pa_full.columns:
        pa_full = pa_full.sort_values(["game_pk", "at_bat_number"])
        pa_full["ttop"] = (pa_full
                           .groupby(["game_pk", "batter"]).cumcount() + 1)
        pa_full["ttop_bucket"] = pa_full["ttop"].clip(upper=3)
        t1 = pa_full[pa_full["ttop_bucket"] == 1]
        t3 = pa_full[pa_full["ttop_bucket"] == 3]
        if len(t1) >= 30 and len(t3) >= 30:
            xw1_num = t1["estimated_woba_using_speedangle"].fillna(0).sum()
            xw1_den = t1["woba_denom"].fillna(0).sum()
            xw3_num = t3["estimated_woba_using_speedangle"].fillna(0).sum()
            xw3_den = t3["woba_denom"].fillna(0).sum()
            if xw1_den > 0 and xw3_den > 0:
                ttop3_penalty = (xw3_num / xw3_den) - (xw1_num / xw1_den)

    # ---------------------------------------------------------------------
    # Shrinkage — blend each rate stat toward a prior. Two-tier prior:
    #   1. If the pitcher has >= SP_PRIOR_YEAR_MIN_PITCHES last calendar
    #      year, use that as the prior (pitcher-specific, much sharper).
    #   2. Otherwise fall back to LG_SP_* league means.
    # Stabilization point = SP_STABLE_PITCHES. Below this, the observed
    # value gets pulled toward the prior; at/above, it's unchanged.
    # ---------------------------------------------------------------------
    n_pitches = float(len(df))
    prior = _pitcher_prior_year(statcast_df, pitcher_id, as_of_date)

    def _blend(obs: float, key: str, league_mean: float) -> float:
        prior_val = prior.get(key, np.nan)
        if pd.notna(prior_val):
            return _shrink_to_prior(obs, prior_val, n_pitches, SP_STABLE_PITCHES)
        return _shrink(obs, league_mean, n_pitches, SP_STABLE_PITCHES)

    sp_xera_shrunk    = _blend(xera, "sp_xera", LG_SP_XERA)
    sp_xwoba_shrunk   = _blend(xwoba_allowed, "sp_xwoba_allowed", LG_SP_XWOBA)
    sp_k_bb_shrunk    = _blend(k_bb_pct, "sp_k_bb_pct", LG_SP_K_BB_PCT)
    sp_k_shrunk       = _blend(k_rate * 100, "sp_k_pct", LG_SP_K_PCT)
    sp_bb_shrunk      = _blend(bb_rate * 100, "sp_bb_pct", LG_SP_BB_PCT)
    sp_hardhit_shrunk = _blend(hardhit_pct, "sp_hardhit_pct_allowed", LG_SP_HARDHIT_PCT)
    # recent_xfip is already a 30-day window — shrink that to the (already-
    # shrunk) season xera so an SP with 50 recent pitches doesn't propagate
    # a wild value into the model.
    sp_recent_xfip_shrunk = _shrink_to_prior(
        recent_xfip, sp_xera_shrunk, min(n_pitches, 600.0), 600.0
    )
    # TTOP3 penalty has its own internal min-sample gate (30 PA per bucket).
    # If it's defined we still shrink to league mean for stability.
    sp_ttop3_shrunk = _shrink(ttop3_penalty, LG_SP_TTOP3_PENALTY,
                              n_pitches, SP_STABLE_PITCHES)

    return {
        "sp_xera":                sp_xera_shrunk,
        "sp_xwoba_allowed":       sp_xwoba_shrunk,
        "sp_k_bb_pct":            sp_k_bb_shrunk,
        "sp_k_pct":               sp_k_shrunk,
        "sp_bb_pct":              sp_bb_shrunk,
        "sp_hardhit_pct_allowed": sp_hardhit_shrunk,
        "sp_siera":               sp_xera_shrunk,  # proxy
        "sp_fip":                 sp_xera_shrunk,  # proxy
        "sp_recent_xfip":         sp_recent_xfip_shrunk,
        "sp_ip_per_start":        ip_per_start,
        "sp_era_xera_gap":        era_xera_gap,  # positive = unlucky, due for improvement
        "sp_n_pitches":           len(df),
        "sp_n_pitches_actual":    float(len(df)),
        "sp_ttop3_penalty":       sp_ttop3_shrunk,
    }


def _nan_pitcher_dict() -> Dict[str, float]:
    return {k: np.nan for k in [
        "sp_xera", "sp_xwoba_allowed", "sp_k_bb_pct", "sp_k_pct", "sp_bb_pct",
        "sp_hardhit_pct_allowed", "sp_siera", "sp_fip", "sp_recent_xfip",
        "sp_ip_per_start", "sp_era_xera_gap", "sp_n_pitches",
        "sp_n_pitches_actual",
        "sp_ttop3_penalty",
    ]}


def _approximate_era(df: pd.DataFrame) -> float:
    """
    Rough ERA from pitch-level Statcast. Uses the `bat_score` and `fld_score`
    progression to count earned runs; divides by approximate innings (outs/3).
    """
    # Outs from events that record an out
    outs_events = {
        "field_out", "force_out", "sac_fly", "sac_bunt", "grounded_into_double_play",
        "double_play", "triple_play", "strikeout", "strikeout_double_play",
        "caught_stealing_2b", "caught_stealing_3b", "caught_stealing_home",
        "pickoff_1b", "pickoff_2b", "pickoff_3b", "fielders_choice_out",
        "other_out",
    }
    outs = df[df["events"].isin(outs_events)]
    n_outs = len(outs)
    n_outs += (df["events"] == "grounded_into_double_play").sum()
    n_outs += (df["events"] == "double_play").sum()
    n_outs += (df["events"] == "strikeout_double_play").sum()
    n_outs += (df["events"] == "triple_play").sum() * 2
    ip = n_outs / 3.0
    if ip < 5:
        return np.nan

    # Runs charged to this pitcher — approximation using delta_score
    # For simplicity, count runs scored in innings this pitcher appeared.
    # (A fully accurate ERA needs pitcher-of-record logic which Statcast
    # doesn't expose directly.)
    if "post_bat_score" in df.columns and "bat_score" in df.columns:
        runs = (df["post_bat_score"].fillna(0) - df["bat_score"].fillna(0)).clip(lower=0).sum()
    else:
        runs = np.nan
    if pd.isna(runs):
        return np.nan
    return 9.0 * runs / ip


# ===========================================================================
# Team offensive stats
# ===========================================================================
def team_batting_as_of(statcast_df: pd.DataFrame,
                       team: str,
                       as_of_date: pd.Timestamp,
                       min_pa: int = 200) -> Dict[str, float]:
    """
    Team-level offensive stats as of a date.

    We identify team plate appearances by: (home_team == team AND inning_topbot == 'Bot')
    OR (away_team == team AND inning_topbot == 'Top').

    Required Statcast columns:
        home_team, away_team, inning_topbot, game_date, events,
        estimated_woba_using_speedangle, woba_denom, launch_speed
    """
    is_home_ab = (statcast_df["home_team"] == team) & (statcast_df["inning_topbot"] == "Bot")
    is_away_ab = (statcast_df["away_team"] == team) & (statcast_df["inning_topbot"] == "Top")
    mask = (is_home_ab | is_away_ab) & \
           (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    df = statcast_df[mask]
    if len(df) < min_pa:
        return _nan_team_dict()

    pa = df[df["events"].notna()]
    n_pa = len(pa)
    if n_pa == 0:
        return _nan_team_dict()

    xwoba_num = df["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = df["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan

    # wOBA (real, not expected) as a reasonable wRC+ proxy — scaled to league
    if "woba_value" in df.columns and "woba_denom" in df.columns:
        woba_num = df["woba_value"].fillna(0).sum()
        woba_real = woba_num / xwoba_den if xwoba_den > 0 else np.nan
    else:
        woba_real = xwoba

    k_rate = (pa["events"] == "strikeout").sum() / n_pa
    bb_rate = (pa["events"] == "walk").sum() / n_pa

    bip = df.dropna(subset=["launch_speed"])
    hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0 if len(bip) > 0 else np.nan

    # wRC+ proxy: scale wOBA to league-average (~0.315). 100 = league-avg.
    league_woba = 0.315
    wrc_plus_proxy = 100.0 * (woba_real / league_woba) if pd.notna(woba_real) else np.nan

    # Swing-take run value gap — proxied via xwOBA run expectancy
    # For the full swing/take decomposition, you'd need pitch-zone data.
    # We use (xwoba - league_avg) * n_pa * some_scale as a proxy signal.
    swing_take_proxy = (xwoba - league_woba) * n_pa * 100.0 if pd.notna(xwoba) else np.nan

    # Early-season shrinkage — blend rate stats toward league means while
    # sample size is small. `games_eff` converts accumulated PAs into an
    # effective games count (~38 PA per team-game). Counting stats like
    # team_n_pa and the swing_take proxy already scale with sample size and
    # are left unshrunk.
    games_eff = n_pa / AVG_PA_PER_GAME

    return {
        "team_wrc_plus":    _shrink(wrc_plus_proxy,  LG_WRC_PLUS,    games_eff, TEAM_STABLE_GAMES),
        "team_xwoba":       _shrink(xwoba,           LG_XWOBA,       games_eff, TEAM_STABLE_GAMES),
        "team_woba":        _shrink(woba_real,       LG_WOBA,        games_eff, TEAM_STABLE_GAMES),
        "team_k_pct":       _shrink(k_rate * 100,    LG_K_PCT,       games_eff, TEAM_STABLE_GAMES),
        "team_bb_pct":      _shrink(bb_rate * 100,   LG_BB_PCT,      games_eff, TEAM_STABLE_GAMES),
        "team_hardhit_pct": _shrink(hardhit_pct,     LG_HARDHIT_PCT, games_eff, TEAM_STABLE_GAMES),
        "team_swing_take":  swing_take_proxy,
        "team_n_pa":        n_pa,
    }


def _nan_team_dict() -> Dict[str, float]:
    return {k: np.nan for k in [
        "team_wrc_plus", "team_xwoba", "team_woba", "team_k_pct",
        "team_bb_pct", "team_hardhit_pct", "team_swing_take", "team_n_pa",
    ]}


# ===========================================================================
# Bullpen prior — last full season as a Bayesian prior for shrinkage (v11)
# ===========================================================================
def _bullpen_prior_year(statcast_df: pd.DataFrame,
                        team: str,
                        as_of_date: pd.Timestamp,
                        starter_ids_by_team: Dict[str, set]) -> Dict[str, float]:
    """
    Return the team's bullpen stats from the PRIOR calendar year as a
    Bayesian prior for early-season shrinkage. Mirrors `_pitcher_prior_year`
    but aggregates across all relievers who pitched for `team` last season.

    Returns {} when the prior-year sample is below
    `BP_PRIOR_YEAR_MIN_PITCHES` so the caller falls back to league mean.

    Caveat: bullpen rosters turn over significantly year-to-year, so the
    team's prior-year aggregate isn't a per-pitcher prior — it's a team-level
    "philosophy + carryover" anchor. Empirically this is still much better
    than the league mean alone for early-season teams (LAD bullpens stay
    elite, COL bullpens stay rough — across personnel changes).
    """
    target_year = pd.Timestamp(as_of_date).year - 1
    yr_start = pd.Timestamp(f"{target_year}-01-01")
    yr_end = pd.Timestamp(f"{target_year}-12-31")

    mask = (
        (statcast_df[["home_team", "away_team"]].eq(team).any(axis=1)) &
        (pd.to_datetime(statcast_df["game_date"]) >= yr_start) &
        (pd.to_datetime(statcast_df["game_date"]) <= yr_end)
    )
    df = statcast_df[mask].copy()
    if df.empty:
        return {}

    is_home_pitch = (df["home_team"] == team) & (df["inning_topbot"] == "Top")
    is_away_pitch = (df["away_team"] == team) & (df["inning_topbot"] == "Bot")
    df = df[is_home_pitch | is_away_pitch]

    # Use the CURRENT-roster's starter set as the SP filter for prior-year
    # too. It's not perfect (SP rotation also turns over) but excluding
    # current SPs from the prior keeps the prior aligned with what the
    # bullpen feature represents now (everyone NOT in the current rotation).
    starters = starter_ids_by_team.get(team, set())
    bp = df[~df["pitcher"].isin(starters)]

    if len(bp) < BP_PRIOR_YEAR_MIN_PITCHES:
        return {}

    xwoba_num = bp["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = bp["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan
    xera = -12.5 + 45.0 * xwoba if pd.notna(xwoba) else np.nan

    pa = bp[bp["events"].notna()]
    n_pa = len(pa)
    if n_pa == 0:
        return {}
    k_rate = (pa["events"] == "strikeout").sum() / n_pa
    bb_rate = (pa["events"] == "walk").sum() / n_pa

    bip = bp.dropna(subset=["launch_speed"])
    hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0 if len(bip) > 0 else np.nan

    return {
        "bullpen_xera": xera,
        "bullpen_xwoba": xwoba,
        "bullpen_k_pct": k_rate * 100.0,
        "bullpen_bb_pct": bb_rate * 100.0,
        "bullpen_hardhit_pct": hardhit_pct,
        "n_pitches": len(bp),
    }


# ===========================================================================
# Bullpen aggregate (v11 — two-tier shrinkage with prior-year blend)
# ===========================================================================
def bullpen_as_of(statcast_df: pd.DataFrame,
                  team: str,
                  as_of_date: pd.Timestamp,
                  starter_ids_by_team: Dict[str, set],
                  min_pitches: int = 200) -> Dict[str, float]:
    """
    Aggregate bullpen stats = everyone pitching for `team` who isn't a
    known starter. Uses `starter_ids_by_team` to filter out SPs.

    v11: two-tier shrinkage — observed → team prior-year aggregate (if
    sample large enough) → league mean. Mirrors the v10 SP fix that
    eliminated the BAL miscalculation. Same root cause: April small-
    sample bullpen xERA was driving extreme +0.68 gaps that overstated
    true talent.
    """
    mask = (
        (statcast_df[["home_team", "away_team"]].eq(team).any(axis=1)) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    )
    df = statcast_df[mask].copy()

    # Pitches thrown by this team = (team == home_team AND inning_topbot == 'Top')
    #                            or (team == away_team AND inning_topbot == 'Bot')
    is_home_pitch = (df["home_team"] == team) & (df["inning_topbot"] == "Top")
    is_away_pitch = (df["away_team"] == team) & (df["inning_topbot"] == "Bot")
    df = df[is_home_pitch | is_away_pitch]

    starters = starter_ids_by_team.get(team, set())
    bp = df[~df["pitcher"].isin(starters)]
    n_pitches = float(len(bp))
    if n_pitches < min_pitches:
        return {
            "bullpen_xera": np.nan,
            "bullpen_xwoba": np.nan,
            "bullpen_k_pct": np.nan,
            "bullpen_bb_pct": np.nan,
            "bullpen_hardhit_pct": np.nan,
            "bullpen_n_pitches": int(n_pitches),
        }

    xwoba_num = bp["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = bp["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan
    xera = -12.5 + 45.0 * xwoba if pd.notna(xwoba) else np.nan

    pa = bp[bp["events"].notna()]
    n_pa = len(pa)
    k_rate = (pa["events"] == "strikeout").sum() / n_pa if n_pa > 0 else np.nan
    bb_rate = (pa["events"] == "walk").sum() / n_pa if n_pa > 0 else np.nan
    k_pct = k_rate * 100.0 if pd.notna(k_rate) else np.nan
    bb_pct = bb_rate * 100.0 if pd.notna(bb_rate) else np.nan

    bip = bp.dropna(subset=["launch_speed"])
    hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0 if len(bip) > 0 else np.nan

    # Two-tier shrinkage: prior-year team bullpen aggregate first, then
    # league mean if the prior is unavailable.
    prior = _bullpen_prior_year(statcast_df, team, as_of_date, starter_ids_by_team)

    def _blend(obs, key, league_mean, stable):
        prior_val = prior.get(key, np.nan)
        if pd.notna(prior_val):
            return _shrink_to_prior(obs, prior_val, n_pitches, stable)
        return _shrink(obs, league_mean, n_pitches, stable)

    return {
        "bullpen_xera":        _blend(xera,        "bullpen_xera",        LG_BULLPEN_XERA,        BP_STABLE_PITCHES),
        "bullpen_xwoba":       _blend(xwoba,       "bullpen_xwoba",       LG_BULLPEN_XWOBA,       BP_STABLE_PITCHES),
        "bullpen_k_pct":       _blend(k_pct,       "bullpen_k_pct",       LG_BULLPEN_K_PCT,       BP_STABLE_PITCHES_K_BB),
        "bullpen_bb_pct":      _blend(bb_pct,      "bullpen_bb_pct",      LG_BULLPEN_BB_PCT,      BP_STABLE_PITCHES_K_BB),
        "bullpen_hardhit_pct": _blend(hardhit_pct, "bullpen_hardhit_pct", LG_BULLPEN_HARDHIT_PCT, BP_STABLE_PITCHES),
        "bullpen_n_pitches":   int(n_pitches),
    }


# ===========================================================================
# High-leverage bullpen aggregate (v12 — added 2026-04-26 after CLE @ TOR
# loss revealed that aggregate bullpen xERA dilutes the closer/setup arms
# with mop-up pitchers. In the actual late-inning game-deciding work, TOR
# used Hoffman/Rogers/Varland — much better than the team-average bullpen
# xERA the v11 model used.)
# ===========================================================================
def high_leverage_bullpen_as_of(
    statcast_df: pd.DataFrame,
    team: str,
    as_of_date: pd.Timestamp,
    starter_ids_by_team: Dict[str, set],
    min_pitches: int = 100,
) -> Dict[str, float]:
    """
    Aggregate stats for the team's high-leverage relievers — proxied as
    'pitches thrown by non-starters in the 7th inning or later'. Since
    closers/setup arms work the late innings and mop-up arms work the
    blowouts (mostly innings 4-6 of lopsided games), filtering on
    inning >= 7 naturally biases toward the actual leverage corps.

    Two-tier shrinkage (observed → team prior-year HL aggregate → league
    mean) mirrors `bullpen_as_of`. Stable point is half the regular bullpen
    stable since HL pitches are a subset (~30-35% of bullpen pitches).
    """
    if "inning" not in statcast_df.columns:
        return {
            "hl_bullpen_xera": np.nan,
            "hl_bullpen_xwoba": np.nan,
            "hl_bullpen_n_pitches": 0,
        }

    mask = (
        (statcast_df[["home_team", "away_team"]].eq(team).any(axis=1)) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    )
    df = statcast_df[mask].copy()

    is_home_pitch = (df["home_team"] == team) & (df["inning_topbot"] == "Top")
    is_away_pitch = (df["away_team"] == team) & (df["inning_topbot"] == "Bot")
    df = df[is_home_pitch | is_away_pitch]

    starters = starter_ids_by_team.get(team, set())
    bp = df[~df["pitcher"].isin(starters)]
    # The HL filter — late innings only
    hl = bp[bp["inning"] >= 7]
    n_pitches = float(len(hl))

    if n_pitches < min_pitches:
        return {
            "hl_bullpen_xera": np.nan,
            "hl_bullpen_xwoba": np.nan,
            "hl_bullpen_n_pitches": int(n_pitches),
        }

    xwoba_num = hl["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = hl["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan
    xera = -12.5 + 45.0 * xwoba if pd.notna(xwoba) else np.nan

    # Prior-year HL aggregate as the shrinkage anchor
    target_year = pd.Timestamp(as_of_date).year - 1
    yr_start = pd.Timestamp(f"{target_year}-01-01")
    yr_end = pd.Timestamp(f"{target_year}-12-31")
    py_mask = (
        (statcast_df[["home_team", "away_team"]].eq(team).any(axis=1)) &
        (pd.to_datetime(statcast_df["game_date"]) >= yr_start) &
        (pd.to_datetime(statcast_df["game_date"]) <= yr_end)
    )
    pdf = statcast_df[py_mask].copy()
    py_home = (pdf["home_team"] == team) & (pdf["inning_topbot"] == "Top")
    py_away = (pdf["away_team"] == team) & (pdf["inning_topbot"] == "Bot")
    pdf = pdf[py_home | py_away]
    py_bp = pdf[~pdf["pitcher"].isin(starters)]
    py_hl = py_bp[py_bp["inning"] >= 7]

    prior_xera = np.nan
    prior_xwoba = np.nan
    if len(py_hl) >= 2000:   # at least ~10 team-games of HL data
        py_xwoba_num = py_hl["estimated_woba_using_speedangle"].fillna(0).sum()
        py_xwoba_den = py_hl["woba_denom"].fillna(0).sum()
        if py_xwoba_den > 0:
            prior_xwoba = py_xwoba_num / py_xwoba_den
            prior_xera = -12.5 + 45.0 * prior_xwoba

    # HL stable point — half the regular bullpen stable since HL pitches
    # are a subset. ~6750 HL pitches ≈ 45 team-games of late-inning work.
    HL_STABLE = 6750.0

    def _blend(obs, prior, league_mean):
        if pd.notna(prior):
            return _shrink_to_prior(obs, prior, n_pitches, HL_STABLE)
        return _shrink(obs, league_mean, n_pitches, HL_STABLE)

    return {
        "hl_bullpen_xera":      _blend(xera, prior_xera, LG_BULLPEN_XERA),
        "hl_bullpen_xwoba":     _blend(xwoba, prior_xwoba, LG_BULLPEN_XWOBA),
        "hl_bullpen_n_pitches": int(n_pitches),
    }


# ===========================================================================
# Bullpen fatigue
# ===========================================================================
def bullpen_fatigue_as_of(statcast_df: pd.DataFrame,
                          team: str,
                          as_of_date: pd.Timestamp,
                          starter_ids_by_team: Dict[str, set],
                          lookback_days: int = 3) -> float:
    """
    Fatigue score ∈ [0, 1] based on bullpen usage in last `lookback_days`.
    Simple rule: count pitches thrown by non-starter pitchers for this team
    in the window; normalize so 300+ pitches = 1.0.
    """
    cutoff_hi = pd.Timestamp(as_of_date)
    cutoff_lo = cutoff_hi - pd.Timedelta(days=lookback_days)
    mask = (
        (pd.to_datetime(statcast_df["game_date"]) >= cutoff_lo) &
        (pd.to_datetime(statcast_df["game_date"]) < cutoff_hi) &
        (statcast_df[["home_team", "away_team"]].eq(team).any(axis=1))
    )
    df = statcast_df[mask]
    is_home_pitch = (df["home_team"] == team) & (df["inning_topbot"] == "Top")
    is_away_pitch = (df["away_team"] == team) & (df["inning_topbot"] == "Bot")
    df = df[is_home_pitch | is_away_pitch]
    starters = starter_ids_by_team.get(team, set())
    bp_pitches = df[~df["pitcher"].isin(starters)]
    return float(min(len(bp_pitches) / 300.0, 1.0))


# ===========================================================================
# Starter identification
# ===========================================================================
def infer_starters_by_team(statcast_df: pd.DataFrame) -> Dict[str, set]:
    """
    Infer each team's starting pitchers from Statcast by finding pitchers
    who threw the first pitch of a game (inning==1, outs==0, early in the
    plate appearance).

    Returns {team_abbrev: set(pitcher_ids)}.
    """
    first_pitches = statcast_df[
        (statcast_df["inning"] == 1) &
        (statcast_df["outs_when_up"] == 0)
    ].drop_duplicates(subset=["game_pk", "inning_topbot"])

    result: Dict[str, set] = {}
    for _, row in first_pitches.iterrows():
        # Top of 1 = away batting, home team's pitcher on mound
        if row["inning_topbot"] == "Top":
            team = row["home_team"]
        else:
            team = row["away_team"]
        result.setdefault(team, set()).add(row["pitcher"])
    return result


def get_game_starters(statcast_df: pd.DataFrame,
                      game_pk: int) -> Dict[str, Optional[int]]:
    """Return {'home_sp': pitcher_id, 'away_sp': pitcher_id} for a game_pk."""
    game = statcast_df[statcast_df["game_pk"] == game_pk]
    if game.empty:
        return {"home_sp": None, "away_sp": None}

    first_inn = game[(game["inning"] == 1) & (game["outs_when_up"] == 0)]
    home_sp = None
    away_sp = None
    top1 = first_inn[first_inn["inning_topbot"] == "Top"]
    bot1 = first_inn[first_inn["inning_topbot"] == "Bot"]
    if not top1.empty:
        home_sp = int(top1.iloc[0]["pitcher"])  # home pitcher throws in top of 1
    if not bot1.empty:
        away_sp = int(bot1.iloc[0]["pitcher"])

    return {"home_sp": home_sp, "away_sp": away_sp}


# ===========================================================================
# Umpire effects (v13 — added 2026-04-26)
# ===========================================================================
@lru_cache(maxsize=1)
def _load_umpire_effects() -> pd.DataFrame:
    """Cached load of data/umpire_effects.parquet. Empty frame if absent."""
    from pathlib import Path
    p = Path("./data/umpire_effects.parquet")
    if not p.exists():
        return pd.DataFrame(columns=["ump_id", "ump_name", "n_pitches",
                                     "k_pct_delta", "bb_pct_delta",
                                     "cs_pct_delta"])
    return pd.read_parquet(p)


@lru_cache(maxsize=1)
def _load_umpire_assignments() -> pd.DataFrame:
    """Cached load of data/umpire_assignments.parquet (game_pk -> ump_id)."""
    from pathlib import Path
    p = Path("./data/umpire_assignments.parquet")
    if not p.exists():
        return pd.DataFrame(columns=["game_pk", "ump_id", "ump_name"])
    return pd.read_parquet(p)


def umpire_effects_for_game(game_pk: int) -> Dict[str, float]:
    """Look up the home-plate umpire's K%/BB%/CS% deltas vs league for
    `game_pk`. Returns zeros if the umpire isn't in our database (small-
    sample umps are already shrunk toward zero in the effects table)."""
    assignments = _load_umpire_assignments()
    effects = _load_umpire_effects()
    if assignments.empty or effects.empty:
        return {"ump_k_pct_delta": 0.0, "ump_bb_pct_delta": 0.0,
                "ump_cs_pct_delta": 0.0, "ump_id": -1}
    row = assignments[assignments["game_pk"] == int(game_pk)]
    if row.empty:
        return {"ump_k_pct_delta": 0.0, "ump_bb_pct_delta": 0.0,
                "ump_cs_pct_delta": 0.0, "ump_id": -1}
    ump_id = int(row.iloc[0]["ump_id"])
    eff = effects[effects["ump_id"] == ump_id]
    if eff.empty:
        return {"ump_k_pct_delta": 0.0, "ump_bb_pct_delta": 0.0,
                "ump_cs_pct_delta": 0.0, "ump_id": ump_id}
    e = eff.iloc[0]
    return {
        "ump_k_pct_delta":  float(e["k_pct_delta"]),
        "ump_bb_pct_delta": float(e["bb_pct_delta"]),
        "ump_cs_pct_delta": float(e["cs_pct_delta"]),
        "ump_id":           ump_id,
    }
