"""
lineup.py
---------
Per-hitter, lineup-aware offensive signals.

Motivation
----------
The team-level offense features (`team_wrcplus_gap`, `team_woba_gap`, etc.)
ignore three things that move individual-game outcomes:

  1. Which nine guys are actually in the lineup today (stars out, platoons in).
  2. How each hitter matches up vs. the opposing SP's handedness (lefties
     vs. RHP is very different from lefties vs. LHP).
  3. Batting-order weighting — the leadoff hitter will take ~1.12x as many
     PAs as the 9-hole batter, so his quality matters proportionally more.

This module adds a SECOND offensive signal alongside the team aggregates:
  - `lineup_xwoba`        — PA-weighted average of today's 9 batters' xwOBA
                            vs. the SP they're facing (split by handedness)
  - `lineup_wrc_plus`     — same, but wRC+ proxy
  - `lineup_vs_sp_gap`    — home_lineup_xwoba - away_lineup_xwoba
  - `lineup_wrcplus_gap`  — home_lineup_wrc_plus - away_lineup_wrc_plus

Both signals are added to Stage 2 features; XGBoost learns to weight them
against the team aggregates (which stay in the feature set — they encode
season-long talent even when the specific lineup is thin).

Lineup resolution
-----------------
  - Live slate:  MLB Stats API `/schedule?hydrate=lineups`. Lineups appear
                 1-3 hours before first pitch. When missing, fall back to
                 the team's most recent actual starting lineup inferred
                 from Statcast (same 9 batters who hit the first 2 innings
                 in their last game).
  - Historical:  infer from Statcast. For `game_pk`, take the first 9
                 unique batters for each team, ordered by at_bat_number.

Per-hitter stats (hitter_as_of)
-------------------------------
Mirrors `point_in_time.pitcher_as_of`: filter Statcast by
    batter == id, game_date < as_of_date, optional p_throws == vs_hand.
Return xwoba, woba_real, k_pct, bb_pct, hardhit_pct, n_pa.

Minimum sample (default 50 PA vs. the requested handedness) — below that
we fall through to the batter's overall YTD line, then to the team aggregate.
This cascade is the same pattern `fallback_stats.py` uses for pitchers.

Leakage guard
-------------
Every stat filters `game_date < as_of_date` strictly. Never peeks at the
game being predicted or any future game.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Expected PA weighting by batting-order position. Based on 2020-2024 MLB
# avg PAs per game by slot — slot 1 sees about 4.7 PA/G, slot 9 about 3.7.
# Normalized so the 9 weights sum to 9.0 (i.e., unweighted would give 1.0 each).
BATTING_ORDER_WEIGHTS: np.ndarray = np.array([
    1.12,  # 1
    1.09,  # 2
    1.06,  # 3
    1.03,  # 4
    1.00,  # 5
    0.98,  # 6
    0.95,  # 7
    0.92,  # 8
    0.85,  # 9
])
assert abs(BATTING_ORDER_WEIGHTS.sum() - 9.0) < 0.02, "BO weights should sum ~9"


# ---------------------------------------------------------------------------
# Pitcher handedness lookup
# ---------------------------------------------------------------------------
def sp_throws_from_sc(statcast_df: pd.DataFrame,
                      pitcher_id: Optional[int]) -> Optional[str]:
    """
    Return 'L' / 'R' for `pitcher_id` based on any pitch they've thrown in
    the provided Statcast frame. Returns None when the pitcher isn't in the
    frame (e.g., a true call-up whose first MLB pitch is today's game) or
    when the frame lacks the `p_throws` column.

    Caller should treat None as "no hand split available" — the lineup
    cascade will fall through to overall YTD for every batter.
    """
    if pitcher_id is None or "p_throws" not in statcast_df.columns:
        return None
    try:
        hits = statcast_df[statcast_df["pitcher"] == pitcher_id]["p_throws"]
        if hits.empty:
            return None
        val = hits.dropna().iloc[0] if hits.notna().any() else None
        if val in ("L", "R"):
            return str(val)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Per-hitter point-in-time stats
# ---------------------------------------------------------------------------
def hitter_as_of(statcast_df: pd.DataFrame,
                 batter_id: int,
                 as_of_date: pd.Timestamp,
                 vs_hand: Optional[str] = None,
                 min_pa: int = 50) -> Dict[str, float]:
    """
    Point-in-time offensive stats for one batter.

    Parameters
    ----------
    vs_hand : "L", "R", or None.
        Filter to PAs vs. that pitcher handedness. None = all PAs.
    min_pa : minimum plate appearances required for a non-NaN return.
        If the hand-split sample is thin, caller should retry with
        vs_hand=None to get the overall line.

    Returns a dict with `xwoba`, `woba_real`, `k_pct`, `bb_pct`,
    `hardhit_pct`, `wrc_plus`, `n_pa`. Values are NaN when sample is thin.
    """
    mask = (
        (statcast_df["batter"] == batter_id) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    )
    if vs_hand and "p_throws" in statcast_df.columns:
        mask &= (statcast_df["p_throws"] == vs_hand)
    df = statcast_df[mask]
    pa = df[df["events"].notna()]
    n_pa = len(pa)
    if n_pa < min_pa:
        return _nan_hitter_dict()

    # Expected wOBA on contact, via same Savant cols the pitcher path uses
    xwoba_num = df["estimated_woba_using_speedangle"].fillna(0).sum()
    xwoba_den = df["woba_denom"].fillna(0).sum()
    xwoba = xwoba_num / xwoba_den if xwoba_den > 0 else np.nan

    # Real wOBA
    if "woba_value" in df.columns:
        woba_num = df["woba_value"].fillna(0).sum()
        woba_real = woba_num / xwoba_den if xwoba_den > 0 else np.nan
    else:
        woba_real = xwoba

    k_rate = (pa["events"] == "strikeout").sum() / max(n_pa, 1)
    bb_rate = (pa["events"] == "walk").sum() / max(n_pa, 1)

    bip = df.dropna(subset=["launch_speed"])
    hardhit_pct = (bip["launch_speed"] >= 95).mean() * 100.0 if len(bip) > 0 else np.nan

    # wRC+ proxy: wOBA / league_wOBA * 100. Standard Savant-style scaling.
    league_woba = 0.315
    wrc_plus = 100.0 * (woba_real / league_woba) if pd.notna(woba_real) else np.nan

    return {
        "xwoba":       xwoba,
        "woba_real":   woba_real,
        "k_pct":       k_rate * 100.0,
        "bb_pct":      bb_rate * 100.0,
        "hardhit_pct": hardhit_pct,
        "wrc_plus":    wrc_plus,
        "n_pa":        n_pa,
    }


def _nan_hitter_dict() -> Dict[str, float]:
    return {k: np.nan for k in [
        "xwoba", "woba_real", "k_pct", "bb_pct", "hardhit_pct", "wrc_plus",
    ]} | {"n_pa": 0}


# ---------------------------------------------------------------------------
# Lineup resolution
# ---------------------------------------------------------------------------
def infer_lineup_from_statcast(statcast_df: pd.DataFrame,
                                game_pk: int,
                                team: str,
                                is_home: bool) -> List[int]:
    """
    For a historical (or today's already-played) game, extract the starting
    lineup order from Statcast by taking the first 9 unique batters for
    that team in at_bat_number order.

    Returns a list of up to 9 batter IDs. Shorter lists mean the game was
    stopped early or Statcast data was incomplete — caller should handle
    that gracefully (pad with NaN signal).
    """
    need_cols = {"game_pk", "batter", "inning_topbot", "at_bat_number"}
    if not need_cols.issubset(statcast_df.columns):
        return []
    half = "Bot" if is_home else "Top"
    df = statcast_df[
        (statcast_df["game_pk"] == game_pk) &
        (statcast_df["inning_topbot"] == half)
    ].sort_values("at_bat_number")
    if df.empty:
        return []
    # Dedupe preserving first-occurrence order.
    seen: List[int] = []
    for bid in df["batter"].tolist():
        if pd.isna(bid):
            continue
        bid = int(bid)
        if bid not in seen:
            seen.append(bid)
        if len(seen) >= 9:
            break
    return seen


def latest_lineup_for_team(statcast_df: pd.DataFrame,
                            team: str,
                            as_of_date: pd.Timestamp) -> List[int]:
    """
    Fall-back for live slates when the MLB Stats API hasn't posted today's
    lineup yet: find the team's most recent game before `as_of_date` and
    return its starting lineup. Only used when the live API is silent.
    """
    need_cols = {"home_team", "away_team", "game_pk", "game_date",
                  "batter", "inning_topbot", "at_bat_number"}
    if not need_cols.issubset(statcast_df.columns):
        return []
    mask = (
        ((statcast_df["home_team"] == team) | (statcast_df["away_team"] == team)) &
        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))
    )
    df = statcast_df[mask]
    if df.empty:
        return []
    latest_pk = df.sort_values("game_date", ascending=False).iloc[0]["game_pk"]
    latest_row = df[df["game_pk"] == latest_pk].iloc[0]
    is_home = latest_row["home_team"] == team
    return infer_lineup_from_statcast(df, int(latest_pk), team, is_home)


# ---------------------------------------------------------------------------
# Lineup aggregation
# ---------------------------------------------------------------------------
def lineup_aggregate(statcast_df: pd.DataFrame,
                      batter_ids: List[int],
                      as_of_date: pd.Timestamp,
                      vs_sp_hand: Optional[str],
                      team_fallback: Optional[Dict[str, float]] = None,
                      use_hitter_fallback: bool = True,
                      ) -> Dict[str, float]:
    """
    Produce a batting-order-weighted lineup aggregate.

    For each of the up-to-9 batter IDs (in order):
      1. Try hitter_as_of(batter, as_of, vs_sp_hand).
      2. If thin, try hitter_as_of(batter, as_of, vs_hand=None) — overall YTD.
      3. If still thin and use_hitter_fallback, try the MLB Stats API
         season-prior via fallback_stats.hitter_fallback(bid). This catches
         rookies and early-season thin samples without collapsing them to
         their team average.
      4. If still empty, fall back to team_fallback (team aggregate dict).

    Then aggregate via BATTING_ORDER_WEIGHTS (slot 1 gets more weight than
    slot 9). Slots past the end of `batter_ids` contribute nothing — this
    rarely matters since a live lineup is always 9.

    Returns a dict with `lineup_xwoba`, `lineup_wrc_plus`, `lineup_k_pct`,
    `lineup_bb_pct`, `lineup_hardhit_pct`, plus diagnostics:
      - `lineup_n_vs_hand`    : how many batters had a valid hand-split sample
      - `lineup_n_overall`    : how many used overall YTD (no hand split)
      - `lineup_n_fallback`   : how many used the season-prior fallback
      - `lineup_n_team_prior` : how many used the team aggregate
    """
    if not batter_ids:
        return _nan_lineup_dict()

    # Lazy import to avoid a hard dep during unit tests; fallback is optional.
    _hitter_fallback = None
    if use_hitter_fallback:
        try:
            from .fallback_stats import hitter_fallback as _hitter_fallback
        except Exception:  # pragma: no cover
            try:
                from fallback_stats import hitter_fallback as _hitter_fallback
            except Exception:
                _hitter_fallback = None

    agg_xwoba: List[float] = []
    agg_wrcplus: List[float] = []
    agg_k: List[float] = []
    agg_bb: List[float] = []
    agg_hh: List[float] = []
    weights: List[float] = []
    n_vs_hand = 0
    n_overall = 0
    n_fallback = 0
    n_team_prior = 0

    for i, bid in enumerate(batter_ids[:9]):
        w = float(BATTING_ORDER_WEIGHTS[i])

        # Cascade: hand-split -> overall -> season prior -> team fallback.
        stats = hitter_as_of(statcast_df, bid, as_of_date,
                             vs_hand=vs_sp_hand, min_pa=50)
        if pd.notna(stats.get("xwoba")):
            n_vs_hand += 1
        else:
            stats = hitter_as_of(statcast_df, bid, as_of_date,
                                 vs_hand=None, min_pa=50)
            if pd.notna(stats.get("xwoba")):
                n_overall += 1
            elif _hitter_fallback is not None:
                try:
                    stats = _hitter_fallback(int(bid))
                    if pd.notna(stats.get("xwoba")):
                        src = stats.get("_source", "")
                        if src.startswith("season_"):
                            n_fallback += 1
                        else:
                            n_team_prior += 1  # league prior counts as team-ish
                except Exception as e:  # pragma: no cover
                    log.debug("hitter_fallback failed for %s: %s", bid, e)
                    stats = _nan_hitter_dict()

            if not pd.notna(stats.get("xwoba")) and team_fallback is not None:
                stats = {
                    "xwoba":       team_fallback.get("team_xwoba", np.nan),
                    "wrc_plus":    team_fallback.get("team_wrc_plus", np.nan),
                    "k_pct":       team_fallback.get("team_k_pct", np.nan),
                    "bb_pct":      team_fallback.get("team_bb_pct", np.nan),
                    "hardhit_pct": team_fallback.get("team_hardhit_pct", np.nan),
                }
                if pd.notna(stats.get("xwoba")):
                    n_team_prior += 1

        # Skip slots where we got nothing at any level (team_fallback=None
        # and per-batter is thin). Rare; defensive.
        if pd.isna(stats.get("xwoba")):
            continue

        agg_xwoba.append(stats["xwoba"])
        agg_wrcplus.append(stats.get("wrc_plus", np.nan))
        agg_k.append(stats.get("k_pct", np.nan))
        agg_bb.append(stats.get("bb_pct", np.nan))
        agg_hh.append(stats.get("hardhit_pct", np.nan))
        weights.append(w)

    if not weights:
        return _nan_lineup_dict()

    w_arr = np.asarray(weights)
    total_w = w_arr.sum()

    def _wavg(vals: List[float]) -> float:
        arr = np.asarray(vals, dtype=float)
        valid = ~np.isnan(arr)
        if not valid.any():
            return np.nan
        return float((arr[valid] * w_arr[valid]).sum() / w_arr[valid].sum())

    # ---- Lineup shape (top-3 vs bottom-3 concentration) ----
    # Captures whether the lineup is top-heavy (star-anchored, dies in the
    # 6-7-8 hole) or balanced (strings hits together).  Computed from the
    # per-batter xwOBA list BEFORE PA-weighted aggregation collapses it.
    # See mlb_edge/lineup_shape.py for full docstring + interpretation
    # guidance.  Returns NaN if fewer than 6 valid slots — defensive.
    try:
        from .lineup_shape import concentration_index, top_bottom_dropoff
    except ImportError:  # pragma: no cover — fallback for direct-script tests
        from lineup_shape import concentration_index, top_bottom_dropoff
    lineup_conc_idx = concentration_index(agg_xwoba)
    lineup_dropoff  = top_bottom_dropoff(agg_xwoba)
    return {
        "lineup_xwoba":       _wavg(agg_xwoba),
        "lineup_wrc_plus":    _wavg(agg_wrcplus),
        "lineup_k_pct":       _wavg(agg_k),
        "lineup_bb_pct":      _wavg(agg_bb),
        "lineup_hardhit_pct": _wavg(agg_hh),
        "lineup_n_vs_hand":   n_vs_hand,
        "lineup_n_overall":   n_overall,
        "lineup_n_fallback":  n_fallback,
        "lineup_n_team_prior": n_team_prior,
        "lineup_n_slots":     len(weights),
        "lineup_total_w":     total_w,
        "lineup_concentration_idx": lineup_conc_idx,
        "lineup_top_bot_dropoff":   lineup_dropoff,
    }


def _nan_lineup_dict() -> Dict[str, float]:
    return {
        "lineup_xwoba":       np.nan,
        "lineup_wrc_plus":    np.nan,
        "lineup_k_pct":       np.nan,
        "lineup_bb_pct":      np.nan,
        "lineup_hardhit_pct": np.nan,
        "lineup_n_vs_hand":   0,
        "lineup_n_overall":   0,
        "lineup_n_fallback":  0,
        "lineup_n_team_prior": 0,
        "lineup_n_slots":     0,
        "lineup_total_w":     0.0,
        "lineup_concentration_idx": np.nan,
        "lineup_top_bot_dropoff":   np.nan,
    }


# ---------------------------------------------------------------------------
# Live-lineup fetch from MLB Stats API
# ---------------------------------------------------------------------------
def fetch_live_lineups(day: "date") -> Dict[int, Dict[str, List[int]]]:
    """
    Pull probable lineups for a day's schedule.

    Returns: {game_pk: {"home": [ids], "away": [ids]}}. Empty list when a
    team hasn't posted yet — caller should fall through to
    `latest_lineup_for_team` or Statcast inference.
    """
    import requests
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date":    day.isoformat(),
        "hydrate": "lineups,probablePitcher",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("MLB Stats API lineups fetch failed: %s", e)
        return {}

    out: Dict[int, Dict[str, List[int]]] = {}
    for dd in data.get("dates", []):
        for g in dd.get("games", []):
            gpk = g.get("gamePk")
            if gpk is None:
                continue
            lu = g.get("lineups") or {}
            home = [p.get("id") for p in (lu.get("homePlayers") or [])
                    if p.get("id") is not None]
            away = [p.get("id") for p in (lu.get("awayPlayers") or [])
                    if p.get("id") is not None]
            out[int(gpk)] = {"home": home[:9], "away": away[:9]}
    return out


# ---------------------------------------------------------------------------
# Top-level entry — compute both sides' lineup features for one game
# ---------------------------------------------------------------------------
def build_lineup_features(statcast_df: pd.DataFrame,
                           game_pk: int,
                           game_date: pd.Timestamp,
                           home_team: str,
                           away_team: str,
                           home_sp_throws: Optional[str],
                           away_sp_throws: Optional[str],
                           home_lineup: Optional[List[int]] = None,
                           away_lineup: Optional[List[int]] = None,
                           home_team_fallback: Optional[Dict[str, float]] = None,
                           away_team_fallback: Optional[Dict[str, float]] = None,
                           ) -> Dict[str, float]:
    """
    Return the full set of lineup-based features for one game.

    If `home_lineup`/`away_lineup` are not supplied, inference is attempted
    from Statcast (historical games) or falls through to the team's most
    recent lineup (live slates when MLB API hasn't posted yet).
    """
    if home_lineup is None:
        home_lineup = (infer_lineup_from_statcast(statcast_df, game_pk,
                                                   home_team, is_home=True)
                       or latest_lineup_for_team(statcast_df, home_team, game_date))
    if away_lineup is None:
        away_lineup = (infer_lineup_from_statcast(statcast_df, game_pk,
                                                   away_team, is_home=False)
                       or latest_lineup_for_team(statcast_df, away_team, game_date))

    # Home lineup faces the away SP's handedness, and vice versa.
    home_agg = lineup_aggregate(statcast_df, home_lineup, game_date,
                                vs_sp_hand=away_sp_throws,
                                team_fallback=home_team_fallback)
    away_agg = lineup_aggregate(statcast_df, away_lineup, game_date,
                                vs_sp_hand=home_sp_throws,
                                team_fallback=away_team_fallback)

    def _g(a: float, b: float) -> float:
        if pd.isna(a) or pd.isna(b):
            return np.nan
        return float(a - b)

    return {
        "home_lineup_xwoba":    home_agg["lineup_xwoba"],
        "away_lineup_xwoba":    away_agg["lineup_xwoba"],
        "home_lineup_wrc_plus": home_agg["lineup_wrc_plus"],
        "away_lineup_wrc_plus": away_agg["lineup_wrc_plus"],
        "lineup_vs_sp_gap":     _g(home_agg["lineup_xwoba"],
                                    away_agg["lineup_xwoba"]),
        "lineup_wrcplus_gap":   _g(home_agg["lineup_wrc_plus"],
                                    away_agg["lineup_wrc_plus"]),
        "lineup_hardhit_gap":   _g(home_agg["lineup_hardhit_pct"],
                                    away_agg["lineup_hardhit_pct"]),
        # Diagnostics: how much of each lineup came from real hand-split data.
        "home_lineup_n_vs_hand": home_agg["lineup_n_vs_hand"],
        "away_lineup_n_vs_hand": away_agg["lineup_n_vs_hand"],
        "home_lineup_n_slots":   home_agg["lineup_n_slots"],
        "away_lineup_n_slots":   away_agg["lineup_n_slots"],
        # ---- Lineup-shape features (2026-05-12) ----
        # See mlb_edge/lineup_shape.py for thresholds.  Top-heavy lineups
        # (concentration > 1.5) are vulnerable to relief pitching that
        # can navigate the top 3 — bottom of the order becomes dead-zone.
        "home_lineup_concentration_idx": home_agg.get("lineup_concentration_idx", np.nan),
        "away_lineup_concentration_idx": away_agg.get("lineup_concentration_idx", np.nan),
        "home_lineup_top_bot_dropoff":   home_agg.get("lineup_top_bot_dropoff",   np.nan),
        "away_lineup_top_bot_dropoff":   away_agg.get("lineup_top_bot_dropoff",   np.nan),
    }
