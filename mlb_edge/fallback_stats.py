"""
fallback_stats.py
-----------------
Fill missing slate features from MLB Stats API season stats when the
Statcast-derived point-in-time aggregates are too thin (rookies, recent
call-ups, teams with small 2026 samples).

The YTD Statcast path returns NaN for any pitcher with fewer than ~100
pitches tracked in the current season, and for team aggregates built on a
similarly thin batted-ball sample. Early in the season that pattern applies
to roughly a third of the slate.

This module provides alternative dictionaries with the SAME field names as
`point_in_time.pitcher_as_of` and `team_batting_as_of`, but derived from
the previous season's total (or the one before that if needed), with a
league-average prior if even that is missing.

Derivations (from MLB Stats API `stats=season` blocks):
  sp_xera ≈ FIP = (13*HR + 3*BB - 2*K) / IP + cFIP. cFIP ≈ 3.10 for 2024-25.
            Falls back to ERA when IP too small to trust FIP.
  sp_xwoba_allowed ≈ league xwOBA + 0.020*(FIP - league_FIP).
  sp_k_bb_pct = 100 * (K - BB) / battersFaced.
  sp_siera = FIP proxy (matches existing point_in_time behavior).
  sp_fip   = FIP.
  sp_recent_xfip = FIP (no recency signal from season stats).
  sp_hardhit_pct_allowed = league average (MLB API doesn't expose EV).
  sp_ip_per_start = inningsPitched / gamesStarted.
  sp_era_xera_gap = era - FIP (positive = unlucky / due to regress down).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

import numpy as np
import requests

log = logging.getLogger(__name__)

BASE = "https://statsapi.mlb.com/api/v1"

# ---------------------------------------------------------------------------
# Team abbreviation -> MLB Stats API team ID
# ---------------------------------------------------------------------------
TEAM_ID: Dict[str, int] = {
    "ARI": 109, "AZ":  109,                 # pipeline uses either
    "ATL": 144, "BAL": 110, "BOS": 111,
    "CHC": 112, "CWS": 145, "CHW": 145,
    "CIN": 113, "CLE": 114, "COL": 115,
    "DET": 116, "HOU": 117, "KC":  118, "KCR": 118,
    "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158,
    "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133, "ATH": 133,
    "PHI": 143, "PIT": 134, "SD":  135, "SDP": 135,
    "SEA": 136, "SF":  137, "SFG": 137,
    "STL": 138, "TB":  139, "TBR": 139,
    "TEX": 140, "TOR": 141, "WSH": 120, "WAS": 120,
}

# League-average priors — 2024-2025 MLB benchmarks. Used when a pitcher has
# no prior-season stats (true debutants) or a team's 2025 totals are missing.
LEAGUE_PRIOR = {
    # Pitcher (per-SP)
    "sp_xera":                4.20,
    "sp_xwoba_allowed":       0.318,
    "sp_k_bb_pct":            12.0,      # league avg K-BB% ~11-13%
    "sp_fip":                 4.20,
    "sp_siera":               4.20,
    "sp_recent_xfip":         4.20,
    "sp_hardhit_pct_allowed": 38.0,
    "sp_ip_per_start":        5.2,
    "sp_era_xera_gap":        0.0,
    # Team offense (league average)
    "team_wrc_plus":          100.0,
    "team_xwoba":             0.313,
    "team_bb_pct":            8.5,
    "team_k_pct":             22.5,
    "team_hardhit_pct":       38.0,
    # Bullpen
    "bullpen_xera":           4.10,
    # Individual hitter (per-player) — same benchmarks as team but used
    # for batters with no prior history (true MLB debutants).
    "hitter_xwoba":           0.313,
    "hitter_woba_real":       0.313,
    "hitter_wrc_plus":        100.0,
    "hitter_k_pct":           22.5,
    "hitter_bb_pct":          8.5,
    "hitter_hardhit_pct":     38.0,
    # League OPS used to derive wRC+ proxy from season OPS.
    "_league_OPS":            0.714,
    # FIP constant that makes FIP scale match ERA.
    "_cFIP":                  3.10,
    "_league_FIP":            4.20,
}


def _get(path: str, **params) -> dict:
    url = f"{BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                log.warning("MLB API %s failed: %s", path, e)
                return {}
            time.sleep(1 + attempt)
    return {}


# ---------------------------------------------------------------------------
# Result caches
# ---------------------------------------------------------------------------
# A backtest over a full season calls these functions hundreds of times on
# the same players / teams. MLB Stats API responses for season-total stats
# are idempotent (the 2025 totals don't change when we re-ask), so memoize
# aggressively. Call `clear_caches()` between runs if you need to force a
# re-fetch (e.g. testing a new code path).
_PITCHER_CACHE: Dict[tuple, Dict[str, float]] = {}
_TEAM_OFFENSE_CACHE: Dict[tuple, Dict[str, float]] = {}
_BULLPEN_CACHE: Dict[tuple, Dict[str, float]] = {}
_HITTER_CACHE: Dict[tuple, Dict[str, float]] = {}


def clear_caches() -> None:
    _PITCHER_CACHE.clear()
    _TEAM_OFFENSE_CACHE.clear()
    _BULLPEN_CACHE.clear()
    _HITTER_CACHE.clear()


# ---------------------------------------------------------------------------
# Pitcher fallback
# ---------------------------------------------------------------------------
def _derive_pitcher_fields(stat: dict) -> Dict[str, float]:
    """Take an MLB Stats API `stat` block and return a dict of the sp_* fields
    the model expects."""
    try:
        ip = float(stat.get("inningsPitched", "0") or 0)
    except ValueError:
        ip = 0.0
    if ip < 10:
        return {}

    hr = int(stat.get("homeRuns", 0) or 0)
    bb = int(stat.get("baseOnBalls", 0) or 0)
    k  = int(stat.get("strikeOuts", 0) or 0)
    bf = int(stat.get("battersFaced", 0) or 0)
    gs = int(stat.get("gamesStarted", 0) or 0)
    era_raw = stat.get("era")
    try:
        era = float(era_raw) if era_raw not in (None, "-.--") else np.nan
    except (TypeError, ValueError):
        era = np.nan

    fip = (13 * hr + 3 * bb - 2 * k) / ip + LEAGUE_PRIOR["_cFIP"]
    # xwOBA-allowed from FIP via empirical MLB regression
    xwoba = LEAGUE_PRIOR["sp_xwoba_allowed"] + 0.020 * (fip - LEAGUE_PRIOR["_league_FIP"])
    # K-BB%
    k_bb = 100.0 * (k - bb) / bf if bf > 0 else LEAGUE_PRIOR["sp_k_bb_pct"]
    # IP per start
    ip_per_start = ip / gs if gs > 0 else LEAGUE_PRIOR["sp_ip_per_start"]
    # Luck: ERA - FIP (positive = unlucky, due to regress down)
    era_xera_gap = (era - fip) if not np.isnan(era) else 0.0

    return {
        "sp_xera":                fip,     # xERA ≈ FIP for backfill
        "sp_xwoba_allowed":       xwoba,
        "sp_k_bb_pct":            k_bb,
        "sp_k_pct":               100.0 * k / max(bf, 1),
        "sp_bb_pct":              100.0 * bb / max(bf, 1),
        "sp_hardhit_pct_allowed": LEAGUE_PRIOR["sp_hardhit_pct_allowed"],
        "sp_siera":               fip,
        "sp_fip":                 fip,
        "sp_recent_xfip":         fip,
        "sp_ip_per_start":        ip_per_start,
        "sp_era_xera_gap":        era_xera_gap,
        "sp_n_pitches":           bf,       # rough proxy
    }


def pitcher_fallback(player_id: int,
                     prefer_seasons: Optional[list] = None
                     ) -> Dict[str, float]:
    """Return a full sp_* dict for `player_id`, drawn from the most recent
    season with ≥ 10 IP. Returns league-average priors when no useful
    history exists."""
    prefer_seasons = prefer_seasons or [2025, 2024, 2023]
    cache_key = (player_id, tuple(prefer_seasons))
    if cache_key in _PITCHER_CACHE:
        return dict(_PITCHER_CACHE[cache_key])
    for season in prefer_seasons:
        r = _get(f"/people/{player_id}/stats",
                 stats="season", group="pitching", season=season)
        stats_blocks = r.get("stats", [])
        if not stats_blocks:
            continue
        splits = stats_blocks[0].get("splits", [])
        if not splits:
            continue
        derived = _derive_pitcher_fields(splits[0].get("stat", {}))
        if derived:
            derived["_source"] = f"season_{season}"
            _PITCHER_CACHE[cache_key] = dict(derived)
            return derived

    # Fallback: league-average rookie prior
    result = {
        "sp_xera":                LEAGUE_PRIOR["sp_xera"],
        "sp_xwoba_allowed":       LEAGUE_PRIOR["sp_xwoba_allowed"],
        "sp_k_bb_pct":            LEAGUE_PRIOR["sp_k_bb_pct"],
        "sp_k_pct":               22.0,
        "sp_bb_pct":              8.5,
        "sp_hardhit_pct_allowed": LEAGUE_PRIOR["sp_hardhit_pct_allowed"],
        "sp_siera":               LEAGUE_PRIOR["sp_siera"],
        "sp_fip":                 LEAGUE_PRIOR["sp_fip"],
        "sp_recent_xfip":         LEAGUE_PRIOR["sp_recent_xfip"],
        "sp_ip_per_start":        LEAGUE_PRIOR["sp_ip_per_start"],
        "sp_era_xera_gap":        0.0,
        "sp_n_pitches":           0,
        "_source":                "league_prior",
    }
    _PITCHER_CACHE[cache_key] = dict(result)
    return result


# ---------------------------------------------------------------------------
# Team offense fallback
# ---------------------------------------------------------------------------
def _derive_team_fields(stat: dict) -> Dict[str, float]:
    """Map MLB Stats API team hitting block → team_* features."""
    try:
        pa = int(stat.get("plateAppearances", 0) or 0)
    except ValueError:
        pa = 0
    if pa < 500:
        return {}

    k  = int(stat.get("strikeOuts", 0) or 0)
    bb = int(stat.get("baseOnBalls", 0) or 0)
    ops = float(stat.get("ops", 0) or 0)
    obp = float(stat.get("obp", 0) or 0)
    slg = float(stat.get("slg", 0) or 0)

    # wRC+ proxy: OPS+ = 100 * OPS / league_avg_OPS. 2025 league OPS ≈ .714.
    wrc_plus = 100.0 * ops / 0.714 if ops > 0 else LEAGUE_PRIOR["team_wrc_plus"]
    # xwOBA proxy: approximate from OBP+SLG weighting used in the linear
    # xwOBA coefficients (close enough for a prior).
    xwoba = 0.69 * obp + 0.31 * slg if (obp + slg) > 0 else LEAGUE_PRIOR["team_xwoba"]
    k_pct = 100.0 * k / max(pa, 1)
    bb_pct = 100.0 * bb / max(pa, 1)

    return {
        "team_wrc_plus":     wrc_plus,
        "team_xwoba":        xwoba,
        "team_k_pct":        k_pct,
        "team_bb_pct":       bb_pct,
        "team_hardhit_pct":  LEAGUE_PRIOR["team_hardhit_pct"],
    }


def team_offense_fallback(team_abbr: str,
                          prefer_seasons: Optional[list] = None
                          ) -> Dict[str, float]:
    """Return team_* dict from 2025 season stats (or league prior)."""
    prefer_seasons = prefer_seasons or [2025, 2024]
    cache_key = (team_abbr, tuple(prefer_seasons))
    if cache_key in _TEAM_OFFENSE_CACHE:
        return dict(_TEAM_OFFENSE_CACHE[cache_key])

    team_id = TEAM_ID.get(team_abbr)
    if team_id is None:
        log.warning("No team_id mapping for %s; using league prior", team_abbr)
        result = {k: LEAGUE_PRIOR[k] for k in
                  ("team_wrc_plus", "team_xwoba", "team_bb_pct",
                   "team_k_pct", "team_hardhit_pct")} | {"_source": "league_prior"}
        _TEAM_OFFENSE_CACHE[cache_key] = dict(result)
        return result

    for season in prefer_seasons:
        r = _get(f"/teams/{team_id}/stats",
                 stats="season", group="hitting", season=season)
        stats_blocks = r.get("stats", [])
        if not stats_blocks:
            continue
        splits = stats_blocks[0].get("splits", [])
        if not splits:
            continue
        derived = _derive_team_fields(splits[0].get("stat", {}))
        if derived:
            derived["_source"] = f"season_{season}"
            _TEAM_OFFENSE_CACHE[cache_key] = dict(derived)
            return derived

    result = {k: LEAGUE_PRIOR[k] for k in
              ("team_wrc_plus", "team_xwoba", "team_bb_pct",
               "team_k_pct", "team_hardhit_pct")} | {"_source": "league_prior"}
    _TEAM_OFFENSE_CACHE[cache_key] = dict(result)
    return result


def bullpen_fallback(team_abbr: str,
                     prefer_seasons: Optional[list] = None
                     ) -> Dict[str, float]:
    """Return bullpen_* dict. We use team-level pitching stats minus starter
    contribution as a coarse proxy for bullpen quality."""
    prefer_seasons = prefer_seasons or [2025, 2024]
    cache_key = (team_abbr, tuple(prefer_seasons))
    if cache_key in _BULLPEN_CACHE:
        return dict(_BULLPEN_CACHE[cache_key])

    team_id = TEAM_ID.get(team_abbr)
    if team_id is None:
        result = {"bullpen_xera": LEAGUE_PRIOR["bullpen_xera"], "_source": "league_prior"}
        _BULLPEN_CACHE[cache_key] = dict(result)
        return result

    for season in prefer_seasons:
        r = _get(f"/teams/{team_id}/stats",
                 stats="season", group="pitching", season=season)
        stats_blocks = r.get("stats", [])
        if not stats_blocks:
            continue
        splits = stats_blocks[0].get("splits", [])
        if not splits:
            continue
        s = splits[0].get("stat", {})
        try:
            ip = float(s.get("inningsPitched", "0") or 0)
            hr = int(s.get("homeRuns", 0) or 0)
            bb = int(s.get("baseOnBalls", 0) or 0)
            k  = int(s.get("strikeOuts", 0) or 0)
        except (ValueError, TypeError):
            continue
        if ip < 200:
            continue
        team_fip = (13 * hr + 3 * bb - 2 * k) / ip + LEAGUE_PRIOR["_cFIP"]
        # Bullpen typically runs ~0.30 lower FIP than team average (relief
        # split is usually better than starters). Small correction.
        bullpen_fip = team_fip - 0.30
        result = {"bullpen_xera": bullpen_fip, "_source": f"season_{season}"}
        _BULLPEN_CACHE[cache_key] = dict(result)
        return result

    result = {"bullpen_xera": LEAGUE_PRIOR["bullpen_xera"], "_source": "league_prior"}
    _BULLPEN_CACHE[cache_key] = dict(result)
    return result


# ---------------------------------------------------------------------------
# Individual hitter fallback
# ---------------------------------------------------------------------------
# Mirrors `pitcher_fallback` but for batters. Used by `lineup.lineup_aggregate`
# as the last rung of its cascade: when a batter has too few YTD PAs in the
# Statcast feed AND too few overall-YTD PAs, we want the prior season's line
# rather than the team aggregate — a thin-sample rookie who hit .320 last year
# should not be collapsed to "league-average hitter for the Dodgers."
#
# Output schema MATCHES `lineup.hitter_as_of`:
#   xwoba, woba_real, k_pct, bb_pct, hardhit_pct, wrc_plus, n_pa
#
# So `lineup_aggregate` can treat this return value identically to an
# `hitter_as_of` return. The cascade becomes:
#   hand-split YTD -> overall YTD -> hitter_fallback (season prior) -> team agg
# Derivation notes (from MLB Stats API `stats=season` hitting blocks):
#   xwoba     -- not exposed; approximated as 0.69*OBP + 0.31*SLG (same
#                linear-coef proxy used in team_offense_fallback).
#   woba_real -- same proxy (Stats API doesn't return true wOBA).
#   wrc_plus  -- 100 * OPS / league_OPS (league_OPS = 0.714 for 2024-25).
#   k_pct     -- 100 * K / PA
#   bb_pct    -- 100 * BB / PA
#   hardhit_pct -- league average (Stats API season-totals block has no EV).
def _derive_hitter_fields(stat: dict, min_pa: int = 100) -> Dict[str, float]:
    """Turn an MLB Stats API hitting `stat` block into a dict matching the
    schema of `lineup.hitter_as_of`. Returns {} if the sample is too thin."""
    try:
        pa = int(stat.get("plateAppearances", 0) or 0)
    except (TypeError, ValueError):
        pa = 0
    if pa < min_pa:
        return {}

    try:
        k   = int(stat.get("strikeOuts", 0) or 0)
        bb  = int(stat.get("baseOnBalls", 0) or 0)
        obp = float(stat.get("obp", 0) or 0)
        slg = float(stat.get("slg", 0) or 0)
        ops = float(stat.get("ops", 0) or 0)
    except (TypeError, ValueError):
        return {}

    # xwOBA proxy: same linear blend used for teams. Not perfect — real
    # xwOBA also leans on expected-BA and contact quality — but it's the
    # best we can do from season totals and stays consistent in scale.
    if obp + slg > 0:
        xwoba = 0.69 * obp + 0.31 * slg
    else:
        xwoba = LEAGUE_PRIOR["hitter_xwoba"]
    woba_real = xwoba  # Stats API doesn't return true wOBA

    wrc_plus = 100.0 * ops / LEAGUE_PRIOR["_league_OPS"] if ops > 0 else \
               LEAGUE_PRIOR["hitter_wrc_plus"]
    k_pct = 100.0 * k / pa
    bb_pct = 100.0 * bb / pa

    return {
        "xwoba":       xwoba,
        "woba_real":   woba_real,
        "k_pct":       k_pct,
        "bb_pct":      bb_pct,
        "hardhit_pct": LEAGUE_PRIOR["hitter_hardhit_pct"],
        "wrc_plus":    wrc_plus,
        "n_pa":        pa,
    }


def hitter_fallback(batter_id: int,
                    prefer_seasons: Optional[list] = None,
                    min_pa: int = 100,
                    ) -> Dict[str, float]:
    """Return a full hitter dict for `batter_id`, drawn from the most recent
    prior season with >= min_pa PAs. Returns league-average priors for true
    debutants.

    Schema matches `lineup.hitter_as_of`:
        xwoba, woba_real, k_pct, bb_pct, hardhit_pct, wrc_plus, n_pa
    plus a `_source` string for audit trails.
    """
    prefer_seasons = prefer_seasons or [2025, 2024, 2023]
    cache_key = (batter_id, tuple(prefer_seasons), min_pa)
    if cache_key in _HITTER_CACHE:
        return dict(_HITTER_CACHE[cache_key])

    for season in prefer_seasons:
        r = _get(f"/people/{batter_id}/stats",
                 stats="season", group="hitting", season=season)
        stats_blocks = r.get("stats", [])
        if not stats_blocks:
            continue
        splits = stats_blocks[0].get("splits", [])
        if not splits:
            continue
        derived = _derive_hitter_fields(splits[0].get("stat", {}), min_pa=min_pa)
        if derived:
            derived["_source"] = f"season_{season}"
            _HITTER_CACHE[cache_key] = dict(derived)
            return derived

    # True debutant: use league priors. A rookie call-up with 0 career PAs
    # should score as a league-average hitter, not as NaN.
    result = {
        "xwoba":       LEAGUE_PRIOR["hitter_xwoba"],
        "woba_real":   LEAGUE_PRIOR["hitter_woba_real"],
        "k_pct":       LEAGUE_PRIOR["hitter_k_pct"],
        "bb_pct":      LEAGUE_PRIOR["hitter_bb_pct"],
        "hardhit_pct": LEAGUE_PRIOR["hitter_hardhit_pct"],
        "wrc_plus":    LEAGUE_PRIOR["hitter_wrc_plus"],
        "n_pa":        0,
        "_source":     "league_prior",
    }
    _HITTER_CACHE[cache_key] = dict(result)
    return result
