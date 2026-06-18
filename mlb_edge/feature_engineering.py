"""
feature_engineering.py
----------------------
Construct the feature matrix for the two-stage model.

The organizing principle is **feature isolation**:
  - Stage 1 features must contain ONLY starting-pitcher signals.
  - Stage 2 features add bullpen, offense, context, and one engineered
    feature: the Stage 1 model's F5 prediction.

This prevents the gradient booster from "washing out" SP impact under a
flood of correlated offensive/defensive stats.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import data_ingestion as di
from .config import (
    BACKUP_CATCHER_SP_PENALTY,
    BULLPEN_BACKTOBACK_APPEARANCES,
    BULLPEN_FATIGUE_LOOKBACK_DAYS,
    BULLPEN_PITCHES_FATIGUE_THRESHOLD,
    CARRY_FT_PER_10F,
    TEMP_BASELINE_F,
    TRAVEL_TZ_PENALTY,
    UMP_ACE_ERA_PLUS_THRESHOLD,
    UMP_ACE_ZONE_EXPANSION,
    UMP_DIVISIONAL_DAMPENING,
)

log = logging.getLogger(__name__)


# ===========================================================================
# 1. Starting pitcher features (Stage 1 exclusive)
# ===========================================================================
SP_FEATURE_COLS: List[str] = [
    "sp_xera", "sp_xwoba_allowed", "sp_k_bb_pct", "sp_siera", "sp_fip",
    "sp_recent_xfip", "sp_ip_per_start", "sp_hardhit_pct_allowed",
]


def build_sp_features(pitcher_season: pd.DataFrame,
                      pitcher_id: int) -> Dict[str, float]:
    """
    Extract the canonical SP feature dict for a single pitcher.

    We return a dict (not a Series) so the caller can build the per-game
    row by subtracting opposing-pitcher values directly.
    """
    row = pitcher_season[pitcher_season["fg_id"] == pitcher_id] if "fg_id" in pitcher_season.columns else pitcher_season.iloc[0:0]
    if row.empty:
        return {c: np.nan for c in SP_FEATURE_COLS}

    r = row.iloc[0]
    return {
        "sp_xera":             _safe(r, "xera"),
        "sp_xwoba_allowed":    _safe(r, "xwOBA"),
        "sp_k_bb_pct":         _safe(r, "k_bb_pct"),
        "sp_siera":            _safe(r, "siera"),
        "sp_fip":              _safe(r, "fip"),
        "sp_recent_xfip":      _safe(r, "xfip"),            # fallback to season xFIP
        "sp_ip_per_start":     _safe(r, "ip") / max(_safe(r, "GS", 1.0), 1.0),
        "sp_hardhit_pct_allowed": _safe(r, "hardhit_pct"),
    }


def sp_gap_features(home_sp: Dict[str, float],
                    away_sp: Dict[str, float]) -> Dict[str, float]:
    """
    Compute (opp - home) gaps from the home team's perspective.
    A positive gap means the opposing SP is worse — an edge for home.

    For rate stats where HIGH = BAD for the pitcher (xERA, xwOBA_allowed, FIP,
    HardHit%), we compute (away - home). For K-BB% where HIGH = GOOD, we
    flip the sign so the convention "positive = home edge" is preserved.
    """
    inv = lambda k: away_sp.get(k, np.nan) - home_sp.get(k, np.nan)   # high=bad
    fwd = lambda k: home_sp.get(k, np.nan) - away_sp.get(k, np.nan)   # high=good

    return {
        "sp_xera_gap":           inv("sp_xera"),
        "sp_xwoba_allowed_gap":  inv("sp_xwoba_allowed"),
        "sp_fip_gap":            inv("sp_fip"),
        "sp_siera_gap":          inv("sp_siera"),
        "sp_k_bb_pct_gap":       fwd("sp_k_bb_pct"),
        "sp_recent_form_gap":    inv("sp_recent_xfip"),
        "sp_hardhit_gap":        inv("sp_hardhit_pct_allowed"),
        "sp_stamina_gap":        fwd("sp_ip_per_start"),
    }


# ===========================================================================
# 2. Team-level offense (Stage 2)
# ===========================================================================
def build_offense_features(team_batting: pd.DataFrame,
                           home_team: str,
                           away_team: str) -> Dict[str, float]:
    """wRC+, xwOBA, BB/K plate discipline, HardHit%."""
    def _pull(team: str) -> Dict[str, float]:
        r = team_batting[team_batting.get("team") == team]
        if r.empty:
            return {}
        r = r.iloc[0]
        return {
            "wrc_plus":    _safe(r, "wrc_plus", 100.0),
            "woba":        _safe(r, "woba", 0.315),
            "bb_pct":      _safe(r, "bb_pct"),
            "k_pct":       _safe(r, "k_pct"),
            "hardhit_pct": _safe(r, "hardhit_pct"),
        }

    h, a = _pull(home_team), _pull(away_team)
    return {
        "team_wrcplus_gap":  h.get("wrc_plus", 100) - a.get("wrc_plus", 100),
        "team_woba_gap":     h.get("woba", 0.315) - a.get("woba", 0.315),
        "team_bbk_gap":      (h.get("bb_pct", 8) - h.get("k_pct", 22)) -
                             (a.get("bb_pct", 8) - a.get("k_pct", 22)),
        "team_hardhit_gap":  h.get("hardhit_pct", 38) - a.get("hardhit_pct", 38),
    }


# ===========================================================================
# 3. Bullpen aggregate + fatigue
# ===========================================================================
def aggregate_bullpen_xfip(pitcher_season: pd.DataFrame,
                           bullpen_ids: List[int]) -> float:
    """IP-weighted bullpen xFIP. Returns NaN if we have no data."""
    bp = pitcher_season[pitcher_season["fg_id"].isin(bullpen_ids)] if "fg_id" in pitcher_season.columns else pitcher_season.iloc[0:0]
    if bp.empty or "xfip" not in bp.columns or "ip" not in bp.columns:
        return np.nan
    bp = bp.dropna(subset=["xfip", "ip"])
    if bp["ip"].sum() == 0:
        return np.nan
    return np.average(bp["xfip"], weights=bp["ip"])


def bullpen_fatigue_score(recent_appearances: pd.DataFrame,
                          as_of: date) -> float:
    """
    Score ∈ [0, 1] where 1 means the high-leverage bullpen is gassed.

    `recent_appearances` schema:
        pitcher_id, game_date, pitches, high_leverage (bool)
    """
    if recent_appearances is None or recent_appearances.empty:
        return 0.0

    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=BULLPEN_FATIGUE_LOOKBACK_DAYS)
    recent = recent_appearances[recent_appearances["game_date"] >= cutoff]
    if recent.empty:
        return 0.0

    hi_lev = recent[recent.get("high_leverage", False)]
    if hi_lev.empty:
        return 0.0

    # How many high-leverage arms threw on consecutive days?
    consec = (
        hi_lev.groupby("pitcher_id")["game_date"]
        .apply(lambda s: _max_consecutive_days(sorted(s.dt.date.unique())))
        .reset_index(name="consec")
    )
    gassed = (consec["consec"] >= BULLPEN_BACKTOBACK_APPEARANCES).sum()

    # Pitch-count load too
    load = hi_lev.groupby("pitcher_id")["pitches"].sum()
    high_load = (load >= BULLPEN_PITCHES_FATIGUE_THRESHOLD).sum()

    # Combine; clamp to [0, 1]
    raw = 0.15 * gassed + 0.10 * high_load
    return float(min(raw, 1.0))


def _max_consecutive_days(dates: List[date]) -> int:
    if not dates:
        return 0
    best = cur = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


# ===========================================================================
# 4. Environmental & situational overlays
# ===========================================================================
def park_adjustment(park_factors: Dict[str, float],
                    park_id: str) -> Dict[str, float]:
    """park_factors: {park_id: {'runs': 1.02, 'hr': 1.15}}"""
    pf = park_factors.get(park_id, {"runs": 1.00, "hr": 1.00})
    return {"park_runs_factor": pf["runs"], "park_hr_factor": pf["hr"]}


def weather_carry_adjustment(temp_f: float, wind_out_mph: float = 0.0) -> float:
    """
    Approximate HR-distance inflator.
    +3.5 ft per +10°F above 70°F baseline, + 1.0 ft per 1 mph wind out to CF.
    Returns a multiplicative park_hr_factor adjustment (around 1.0).
    """
    temp_ft = CARRY_FT_PER_10F * (temp_f - TEMP_BASELINE_F) / 10.0
    wind_ft = max(wind_out_mph, 0.0) * 1.0
    total_extra_ft = temp_ft + wind_ft
    # Rough rule: each +10 ft of carry ≈ +4% HR rate.
    return 1.0 + 0.004 * total_extra_ft


def travel_penalty(tz_diff: int, is_opener: bool, is_quick_turnaround: bool) -> float:
    """Returns a multiplicative haircut (e.g. 0.90) for the traveling team."""
    if abs(tz_diff) >= 2 and (is_opener or is_quick_turnaround):
        return 1.0 + TRAVEL_TZ_PENALTY  # TRAVEL_TZ_PENALTY is negative
    return 1.0


def umpire_ace_adjustment(era_plus: float, is_divisional: bool) -> float:
    """
    Matthew-effect zone expansion for aces. Divisional games halve the effect
    because batters are more familiar with the arsenal.
    """
    if era_plus < UMP_ACE_ERA_PLUS_THRESHOLD:
        return 1.0
    bump = UMP_ACE_ZONE_EXPANSION * (UMP_DIVISIONAL_DAMPENING if is_divisional else 1.0)
    return 1.0 + bump


def catcher_penalty(is_backup_catcher: bool) -> float:
    return 1.0 + BACKUP_CATCHER_SP_PENALTY if is_backup_catcher else 1.0


# ===========================================================================
# 5. Regression / luck flags
# ===========================================================================
def pitcher_luck_flag(era: float, xera: float) -> float:
    """Returns era - xera. Negative = lucky, positive regression expected."""
    if pd.isna(era) or pd.isna(xera):
        return np.nan
    return era - xera


def pythag_vs_actual_gap(runs_scored: float, runs_allowed: float,
                         wins: int, losses: int, exp: float = 1.83) -> float:
    """
    Bill James Pythagorean expectation vs actual W%.
    Positive gap = team overperforming (regression candidate).
    exp = 1.83 (classical Pythag exponent).
    """
    if runs_scored <= 0 or runs_allowed <= 0:
        return np.nan
    pythag_w_pct = runs_scored**exp / (runs_scored**exp + runs_allowed**exp)
    actual = wins / max(wins + losses, 1)
    return actual - pythag_w_pct


# ===========================================================================
# 6. Master per-game assembly
# ===========================================================================
def build_game_row(game_meta: Dict,
                   pitcher_season: pd.DataFrame,
                   team_batting: pd.DataFrame,
                   bullpen_roster: Dict[str, List[int]],
                   bullpen_recent: pd.DataFrame,
                   park_factors: Dict,
                   context: Dict,
                   statcast: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """
    Assemble a single per-game feature row. Everything that downstream Stage 1
    and Stage 2 models consume lives here.

    game_meta keys:
        game_id, game_date, home_team, away_team, home_sp_id, away_sp_id,
        park_id, temp_f, wind_out_mph, is_divisional, tz_diff, is_opener,
        is_quick_turnaround, home_backup_catcher, away_backup_catcher,
        home_era_plus, away_era_plus

    statcast (optional): Statcast DataFrame. When present, Tier-1 pitcher
        enrichments (rest, velo drop, handedness matchup) are computed and
        merged into sp_gaps. If None, the three new gap columns come out
        as NaN so the model falls back to the original 8 SP features only.
    """
    # SP features ---------------------------------------------------------
    home_sp = build_sp_features(pitcher_season, game_meta["home_sp_id"])
    away_sp = build_sp_features(pitcher_season, game_meta["away_sp_id"])
    sp_gaps = sp_gap_features(home_sp, away_sp)

    # Tier-1 SP enrichments (rest days, velocity drop, handedness matchup).
    # Local import so feature_engineering has no hard dep on the enrichment
    # module at import-time — older callers that don't pass `statcast` see
    # the same schema with NaN values for the three new gaps.
    if statcast is not None and not statcast.empty:
        from .pitcher_enrichments import build_pitcher_enrichments
        enrichments = build_pitcher_enrichments(
            statcast=statcast,
            home_sp_id=game_meta.get("home_sp_id"),
            away_sp_id=game_meta.get("away_sp_id"),
            home_team_abbr=game_meta["home_team"],
            away_team_abbr=game_meta["away_team"],
            game_date=pd.Timestamp(game_meta["game_date"]).date(),
        )
        sp_gaps.update(enrichments)
    else:
        sp_gaps.update({
            "sp_rest_gap":      np.nan,
            "sp_velo_drop_gap": np.nan,
            "sp_vs_lineup_gap": np.nan,
        })

    # Offense -------------------------------------------------------------
    off = build_offense_features(team_batting,
                                 game_meta["home_team"],
                                 game_meta["away_team"])

    # Bullpen -------------------------------------------------------------
    home_bp_xfip = aggregate_bullpen_xfip(pitcher_season,
                                          bullpen_roster.get(game_meta["home_team"], []))
    away_bp_xfip = aggregate_bullpen_xfip(pitcher_season,
                                          bullpen_roster.get(game_meta["away_team"], []))
    home_fat = bullpen_fatigue_score(
        bullpen_recent[bullpen_recent.get("team") == game_meta["home_team"]],
        game_meta["game_date"],
    )
    away_fat = bullpen_fatigue_score(
        bullpen_recent[bullpen_recent.get("team") == game_meta["away_team"]],
        game_meta["game_date"],
    )

    # Park + weather ------------------------------------------------------
    park = park_adjustment(park_factors, game_meta.get("park_id", ""))
    carry = weather_carry_adjustment(game_meta.get("temp_f", TEMP_BASELINE_F),
                                     game_meta.get("wind_out_mph", 0.0))
    park["park_hr_factor"] *= carry

    # Situational overlays (multiplicative, applied to SP efficiency) -----
    home_ump = umpire_ace_adjustment(game_meta.get("home_era_plus", 100),
                                     game_meta.get("is_divisional", False))
    away_ump = umpire_ace_adjustment(game_meta.get("away_era_plus", 100),
                                     game_meta.get("is_divisional", False))
    home_cat = catcher_penalty(game_meta.get("home_backup_catcher", False))
    away_cat = catcher_penalty(game_meta.get("away_backup_catcher", False))

    # Regression flags ----------------------------------------------------
    home_sp_luck = pitcher_luck_flag(
        pitcher_season.loc[pitcher_season["fg_id"] == game_meta["home_sp_id"], "era"].mean(),
        home_sp["sp_xera"],
    )
    away_sp_luck = pitcher_luck_flag(
        pitcher_season.loc[pitcher_season["fg_id"] == game_meta["away_sp_id"], "era"].mean(),
        away_sp["sp_xera"],
    )

    # Assemble flat row ---------------------------------------------------
    return {
        "game_id":     game_meta["game_id"],
        "game_date":   pd.Timestamp(game_meta["game_date"]),
        "home_team":   game_meta["home_team"],
        "away_team":   game_meta["away_team"],
        # Stage 1 features
        **sp_gaps,
        # Stage 2 features (excluding the f5 output, added after Stage 1 predicts)
        **off,
        "bullpen_siera_gap":    (away_bp_xfip - home_bp_xfip) if pd.notna(home_bp_xfip) and pd.notna(away_bp_xfip) else np.nan,
        "bullpen_fatigue_gap":  home_fat - away_fat,  # we want OUR bullpen less tired
        "park_runs_factor":     park["park_runs_factor"],
        "park_hr_factor":       park["park_hr_factor"],
        "home_ump_boost":       home_ump,
        "away_ump_boost":       away_ump,
        "home_catcher_penalty": home_cat,
        "away_catcher_penalty": away_cat,
        "home_sp_luck":         home_sp_luck,
        "away_sp_luck":         away_sp_luck,
        # Context
        "is_divisional":        int(game_meta.get("is_divisional", False)),
        "tz_diff":              game_meta.get("tz_diff", 0),
        "is_opener":            int(game_meta.get("is_opener", False)),
        "is_quick_turnaround":  int(game_meta.get("is_quick_turnaround", False)),
    }


# ===========================================================================
# Helpers
# ===========================================================================
def _safe(row, key: str, default=np.nan):
    if key not in row.index:
        return default
    v = row[key]
    return default if pd.isna(v) else v


def add_rolling_features(games: pd.DataFrame,
                         window_days: int = 30) -> pd.DataFrame:
    """
    Add rolling-window team features. CRITICAL: we `.shift(1)` before the
    rolling aggregation to prevent the model from seeing the game's own
    outcome as an input feature. This is where leakage most commonly creeps in.
    """
    g = games.sort_values("game_date").copy()
    for col in ["home_team_wrcplus_L30", "away_team_wrcplus_L30"]:
        if col in g.columns:
            continue
    # Example: per-team rolling wRC+ on prior-30-day games. Users should wire
    # their own per-team game log in here; the skeleton is intentionally sparse
    # so it doesn't guess at upstream data shape.
    return g
