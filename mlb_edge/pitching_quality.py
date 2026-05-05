"""
mlb_edge/pitching_quality.py
----------------------------
Composite Pitching Quality Index (PQI) — the "whole-game pitching" signal.

WHY THIS EXISTS
===============
The existing pipeline scores SPs in detail (via xERA, xFIP, FIP, Stuff+ on
top of `parlay_builder._lookup_sp`) and tracks bullpen *fatigue* (72-hour
high-leverage pitch counts via `bullpen_fatigue_blocker`).  But neither
captures bullpen *quality* — the question "if the SP exits in the 6th
with a 1-run lead, will this team's bullpen hold it?"

The 4/28-5/1 LAD failure mode is partly explained by this gap: LAD's SP
edge was real, but the F5/Full divergence was the model's signal that
"something" was happening late.  Adding PQI lets the grader explicitly
weight SP performance against the bullpen actually expected to throw
the last 3-4 innings.

WHAT IT COMPUTES
================
For each team in a matchup:

    PQI(team) = SP_quality * SP_inning_share
              + BP_quality * BP_inning_share

where:

  SP_quality      0-100 scale derived from xERA / xFIP / K-BB%; higher = better
  BP_quality      0-100 scale, leverage-weighted bullpen aggregate
  SP_inning_share projected_SP_IP / 9.0  (~0.55-0.67 in 2026 MLB)
  BP_inning_share 1.0 - SP_inning_share

Bullpen quality is the weighted mean of the team's active relievers'
quality, with weights from leverage role (closer > setup > middle >
mop-up) and a fatigue *penalty* (recent appearances reduce contribution).

The differential signal `pqi_diff(home, away)` becomes a new grading
input: when PQI strongly disagrees with the picked side, the parlay
grade is dampened (the late-game-pitching-degradation guard).

DATA SOURCES (ordered preference)
=================================
1. FanGraphs sp-dashboard (via fangraphs_scraper) — preferred for xERA
2. MLB Stats API per-pitcher season stats — fallback for ERA/FIP/K-BB%
3. Local bullpen_tracker pitch_logs — required for fatigue input

When a source is missing, we fall back gracefully and lower the
confidence of the PQI for that team (the `__data_quality` attribute).

PUBLIC API
==========
    compute_team_pqi(team, sp_id, slate_date) -> PQIResult
    pqi_diff(home, home_sp_id, away, away_sp_id, slate_date) -> float
    pqi_grade_modifier(pqi_diff_value, picked_side) -> int
        Returns +1 / 0 / -1 to add to parlay-grade score.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Team abbreviation -> MLB Stats API team ID
# ---------------------------------------------------------------------------
# Used by build_reliever_profiles to fetch each team's roster.  Includes
# legacy aliases so callers using either "ATH"/"OAK" or "CHW"/"CWS" all
# resolve correctly (matches the conventions in stadiums.normalize_team).
TEAM_ID: Dict[str, int] = {
    "LAA": 108, "ARI": 109, "BAL": 110, "BOS": 111, "CHC": 112,
    "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117,
    "KC":  118, "LAD": 119, "WSH": 120, "NYM": 121, "ATH": 133,
    "OAK": 133, "PIT": 134, "SD":  135, "SEA": 136, "SF":  137,
    "STL": 138, "TB":  139, "TEX": 140, "TOR": 141, "MIN": 142,
    "PHI": 143, "ATL": 144, "CHW": 145, "CWS": 145, "MIA": 146,
    "NYY": 147, "MIL": 158,
}


# ---------------------------------------------------------------------------
# Constants & calibration
# ---------------------------------------------------------------------------
# League-average FIP in 2025-26 hovers ~4.10.  We map FIP onto a 0-100
# quality scale where:
#     FIP 2.50 -> 90  (elite)
#     FIP 3.50 -> 70  (above average)
#     FIP 4.10 -> 50  (league average)
#     FIP 5.00 -> 30  (below average)
#     FIP 6.00+ -> 10 (poor)
# Linear-piecewise fit; saturates at 0 and 100.
LEAGUE_AVG_FIP = 4.10
QUALITY_MIN = 0.0
QUALITY_MAX = 100.0
QUALITY_CENTER = 50.0  # value at league-average FIP

# Leverage role weights.  Set conservatively — closer dominates the
# weighted-mean but doesn't completely shadow setup/middle.
LEVERAGE_WEIGHTS = {
    "closer":  1.00,
    "setup":   0.70,
    "middle":  0.40,
    "mopup":   0.20,
    "unknown": 0.40,   # fall-back when role classification fails
}

# Fatigue penalty — multiplicative factor on RP contribution.
# 0 days rest (pitched yesterday)        : 0.6  (60% of normal contribution)
# 1 day rest                              : 0.85
# 2 days rest                             : 0.95
# 3+ days rest                            : 1.00
# Pitched 2 of last 3 days                : 0.5 regardless
# Pitched 3 of last 3 days                : 0.2
FATIGUE_FACTOR_BY_DAYS_REST = {0: 0.60, 1: 0.85, 2: 0.95}
FATIGUE_FACTOR_2_OF_3 = 0.50
FATIGUE_FACTOR_3_OF_3 = 0.20

# Projected SP innings — the model's expectation for how deep the SP goes.
# We use a simple proxy: average of (recent_avg_IP_per_GS, 5.5).  A truly
# fresh SP gets 5.5 IP; a recent short-rest SP gets less.  For now we
# use a static 5.5 default and let the per-pitcher overrides take effect
# when game-log data is available.
DEFAULT_SP_PROJECTED_IP = 5.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RelieverProfile:
    """One reliever's contribution to a bullpen quality calc."""
    pitcher_id: int
    name: str
    fip: Optional[float]            # season FIP
    k_bb_pct: Optional[float]       # K% - BB%, in [0, 1]
    role: str                       # "closer" | "setup" | "middle" | "mopup" | "unknown"
    days_rest: int                  # days since last appearance
    appearances_last_3d: int        # 0-3
    leverage_weight: float = 0.0    # set in __post_init__-equivalent
    fatigue_factor: float = 1.0     # set in __post_init__-equivalent

    def quality_score(self) -> float:
        """Map FIP/K-BB% to 0-100 quality, then apply role weight + fatigue."""
        if self.fip is not None:
            base = _fip_to_quality(self.fip)
        elif self.k_bb_pct is not None:
            base = _k_bb_to_quality(self.k_bb_pct)
        else:
            base = QUALITY_CENTER
        return base


@dataclass
class BullpenQualityResult:
    team: str
    quality: float                       # 0-100, weighted mean
    n_relievers: int
    n_fatigued: int                      # count w/ fatigue_factor < 0.85
    high_leverage_quality: float = 0.0   # closer + setup mean
    raw_relievers: List[RelieverProfile] = field(default_factory=list)


@dataclass
class PQIResult:
    team: str
    sp_id: int
    sp_name: str = ""
    sp_quality: float = QUALITY_CENTER
    bp_quality: float = QUALITY_CENTER
    sp_projected_ip: float = DEFAULT_SP_PROJECTED_IP
    pqi: float = QUALITY_CENTER
    data_quality: str = "ok"             # "ok" | "partial" | "missing"
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Quality-score mappers
# ---------------------------------------------------------------------------
def _fip_to_quality(fip: float) -> float:
    """Linear-piecewise FIP -> 0-100 quality score.

    Anchors:
        FIP 2.50 -> 90
        FIP 4.10 -> 50  (league average)
        FIP 6.00 -> 10
    """
    if fip <= 2.50:
        return min(QUALITY_MAX, 90.0 + (2.50 - fip) * 10.0)
    if fip <= LEAGUE_AVG_FIP:
        # Linear from (2.50, 90) to (4.10, 50)
        return 90.0 - (fip - 2.50) / (LEAGUE_AVG_FIP - 2.50) * 40.0
    if fip <= 6.00:
        # Linear from (4.10, 50) to (6.00, 10)
        return 50.0 - (fip - LEAGUE_AVG_FIP) / (6.00 - LEAGUE_AVG_FIP) * 40.0
    return max(QUALITY_MIN, 10.0 - (fip - 6.00) * 5.0)


def _k_bb_to_quality(k_bb_pct: float) -> float:
    """Linear K-BB% -> 0-100 quality score.

    K-BB% in MLB 2026 averages ~13-14%; elite ~25%.

    Anchors:
        25% -> 90
        14% -> 50
        5%  -> 20
    """
    pct = k_bb_pct * 100.0 if k_bb_pct < 1.0 else k_bb_pct
    if pct >= 25:
        return min(QUALITY_MAX, 90.0 + (pct - 25) * 1.0)
    if pct >= 14:
        return 50.0 + (pct - 14) / (25 - 14) * 40.0
    if pct >= 5:
        return 20.0 + (pct - 5) / (14 - 5) * 30.0
    return max(QUALITY_MIN, pct * 4.0)


# ---------------------------------------------------------------------------
# Fatigue calculations
# ---------------------------------------------------------------------------
def _compute_fatigue_factor(days_rest: int, appearances_last_3d: int) -> float:
    """Blend the days-rest penalty with the rolling-3-day appearance count.

    The 2/3 and 3/3 rules dominate when they fire — a reliever who
    pitched 3 of the last 3 days is essentially unavailable regardless
    of yesterday's specific count.
    """
    if appearances_last_3d >= 3:
        return FATIGUE_FACTOR_3_OF_3
    if appearances_last_3d >= 2:
        return FATIGUE_FACTOR_2_OF_3
    return FATIGUE_FACTOR_BY_DAYS_REST.get(days_rest, 1.0)


def _classify_role(rp_stat_row: dict) -> str:
    """Heuristic role classification from season stats.

    A real-world pipeline would hit a roster-role registry (FanGraphs
    has one, depth charts at depthchart.com etc).  Without that, we
    proxy from saves + holds + games-finished:

        SV >= 5         -> closer
        H + GF >= 10    -> setup (high-leverage / late innings)
        G >= 15         -> middle
        else            -> mopup / unknown
    """
    sv = rp_stat_row.get("saves", 0) or 0
    h  = rp_stat_row.get("holds", 0) or 0
    gf = rp_stat_row.get("gamesFinished", 0) or 0
    g  = rp_stat_row.get("gamesPlayed", 0) or 0
    if sv >= 5:
        return "closer"
    if (h + gf) >= 10:
        return "setup"
    if g >= 15:
        return "middle"
    return "unknown"


# ---------------------------------------------------------------------------
# Bullpen aggregate
# ---------------------------------------------------------------------------
def compute_bullpen_quality(
    relievers: List[RelieverProfile],
) -> BullpenQualityResult:
    """Aggregate a list of RelieverProfile into a single bullpen quality score.

    Weighted-mean formula:
        Q = sum(role_weight * fatigue_factor * quality_score) /
            sum(role_weight)

    Note: the *denominator* uses the un-fatigue-adjusted role weight so
    that fatigue *reduces* the bullpen's contribution rather than just
    redistributing weight to other relievers.  This is critical: if the
    closer is unavailable, the bullpen is genuinely worse, not "the
    setup man's role weight goes up."
    """
    if not relievers:
        return BullpenQualityResult(team="(unknown)",
                                     quality=QUALITY_CENTER,
                                     n_relievers=0, n_fatigued=0)

    num = 0.0
    den = 0.0
    n_fatigued = 0
    high_lev_qualities: List[float] = []
    for rp in relievers:
        qual = rp.quality_score()
        contribution = rp.leverage_weight * rp.fatigue_factor * qual
        num += contribution
        den += rp.leverage_weight
        if rp.fatigue_factor < 0.85:
            n_fatigued += 1
        if rp.role in ("closer", "setup"):
            high_lev_qualities.append(qual)

    quality = (num / den) if den > 0 else QUALITY_CENTER
    hl_quality = (sum(high_lev_qualities) / len(high_lev_qualities)
                  if high_lev_qualities else quality)
    return BullpenQualityResult(
        team="",  # caller sets
        quality=quality,
        n_relievers=len(relievers),
        n_fatigued=n_fatigued,
        high_leverage_quality=hl_quality,
        raw_relievers=relievers,
    )


# ---------------------------------------------------------------------------
# Reliever-data ingestion (MLB Stats API)
# ---------------------------------------------------------------------------
def fetch_active_relievers(team_id: int,
                            season: int) -> List[Dict]:
    """Fetch each active-roster pitcher's season stats from MLB Stats API.

    Returns raw dicts with the fields we care about (id, name, FIP-like
    estimators, K%/BB%, saves/holds/games-played).  Caller turns these
    into RelieverProfile objects after joining recent-usage data.

    Network failure or unavailable data returns an empty list; PQI
    computation degrades gracefully.
    """
    import urllib.request
    import json

    out: List[Dict] = []
    try:
        # Roster
        url = (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
               f"?rosterType=active")
        with urllib.request.urlopen(url, timeout=10) as r:
            roster = json.loads(r.read())
        pitcher_ids = [p["person"]["id"] for p in roster.get("roster", [])
                       if p.get("position", {}).get("code") in ("1",)]
    except Exception as e:
        log.warning("[pqi] roster fetch failed for team %s: %s", team_id, e)
        return []

    # Per-pitcher season stats
    for pid in pitcher_ids:
        try:
            url = (f"https://statsapi.mlb.com/api/v1/people/{pid}/stats?"
                   f"stats=season&season={season}&group=pitching")
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read())
            splits = data.get("stats", [{}])[0].get("splits", [])
            if not splits:
                continue
            stat = splits[0]["stat"]
            # Filter out starters: GS / G > 0.5 -> primarily a starter
            g = int(stat.get("gamesPlayed") or 0)
            gs = int(stat.get("gamesStarted") or 0)
            if g == 0 or (gs / g if g > 0 else 0) > 0.5:
                continue
            try:
                k9 = float(stat.get("strikeoutsPer9Inn") or 0)
                bb9 = float(stat.get("walksPer9Inn") or 0)
                k_bb_per9 = k9 - bb9
                # Approximate K-BB% as (K-BB) / 9 / 4.3 (BF/IP factor)
                k_bb_pct = max(0.0, k_bb_per9 / (9 * 4.3 / 100))
            except Exception:
                k_bb_pct = None
            out.append({
                "pitcher_id": pid,
                "name": (splits[0].get("player", {}).get("fullName")
                         or stat.get("playerName") or str(pid)),
                "fip": _safe_float(stat.get("fip")),
                "k_bb_pct": k_bb_pct,
                "saves": int(stat.get("saves") or 0),
                "holds": int(stat.get("holds") or 0),
                "gamesFinished": int(stat.get("gamesFinished") or 0),
                "gamesPlayed": g,
                "ip": _safe_float(stat.get("inningsPitched")),
            })
        except Exception:
            continue
    return out


def _safe_float(v) -> Optional[float]:
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


# ---------------------------------------------------------------------------
# Recent-usage join (fatigue input)
# ---------------------------------------------------------------------------
def _load_recent_usage(slate_date: date) -> Dict[int, Tuple[int, int]]:
    """Load (days_rest, appearances_last_3d) per pitcher_id from the
    bullpen_tracker pitch_logs cache.

    Returns empty dict on failure; caller treats missing entries as
    "fully rested" (days_rest=3, appearances_last_3d=0).
    """
    parquet_path = Path("data/pitch_logs/recent_72h.parquet")
    if not parquet_path.exists():
        return {}
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        log.warning("[pqi] could not load %s: %s", parquet_path, e)
        return {}
    if df.empty or "pitcher_id" not in df.columns:
        return {}
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
    out: Dict[int, Tuple[int, int]] = {}
    for pid, g in df.groupby("pitcher_id"):
        dates = sorted(set(g["game_date"]))
        if not dates:
            continue
        last = dates[-1]
        days_rest = (slate_date - last).days
        days_rest = max(0, days_rest)
        # Count appearances in last 3 days
        cutoff = slate_date - timedelta(days=3)
        recent = [d for d in dates if cutoff <= d < slate_date]
        out[int(pid)] = (days_rest, len(recent))
    return out


def build_reliever_profiles(team_id: int,
                             slate_date: date) -> List[RelieverProfile]:
    """Top-level helper: pull reliever season stats + recent usage and
    return a list of RelieverProfile ready for compute_bullpen_quality."""
    season = slate_date.year
    raw = fetch_active_relievers(team_id, season)
    usage = _load_recent_usage(slate_date)

    profiles: List[RelieverProfile] = []
    for r in raw:
        pid = r["pitcher_id"]
        days_rest, n_recent = usage.get(pid, (3, 0))
        role = _classify_role(r)
        profiles.append(RelieverProfile(
            pitcher_id=pid,
            name=r["name"],
            fip=r.get("fip"),
            k_bb_pct=r.get("k_bb_pct"),
            role=role,
            days_rest=days_rest,
            appearances_last_3d=n_recent,
            leverage_weight=LEVERAGE_WEIGHTS.get(role, LEVERAGE_WEIGHTS["unknown"]),
            fatigue_factor=_compute_fatigue_factor(days_rest, n_recent),
        ))
    return profiles


# ---------------------------------------------------------------------------
# SP-quality lookup (delegates to existing parlay_builder lookup chain)
# ---------------------------------------------------------------------------
def _sp_quality_from_xera(xera: Optional[float]) -> float:
    """Map xERA to 0-100 quality.  Use same anchor scale as FIP."""
    if xera is None:
        return QUALITY_CENTER
    return _fip_to_quality(xera)


def _sp_projected_ip(recent_avg_ip: Optional[float]) -> float:
    """Project SP IP based on recent form.  Defaults to 5.5 when no data."""
    if recent_avg_ip is None:
        return DEFAULT_SP_PROJECTED_IP
    # Cap between 4.0 and 7.0 to keep BP_share in a reasonable range
    return max(4.0, min(7.0, recent_avg_ip))


# ---------------------------------------------------------------------------
# Top-level PQI computation
# ---------------------------------------------------------------------------
def compute_team_pqi(team: str,
                     team_id: int,
                     sp_xera: Optional[float],
                     sp_recent_avg_ip: Optional[float],
                     slate_date: date) -> PQIResult:
    """Compute PQI for one team.

    Args:
        team:           team abbreviation (for the result label)
        team_id:        MLB Stats API team ID (used to fetch roster)
        sp_xera:        SP's expected ERA (from FanGraphs / Savant)
        sp_recent_avg_ip: SP's recent average innings per start (None ok)
        slate_date:     slate date (for fatigue lookback)
    """
    notes: List[str] = []

    # SP side
    sp_quality = _sp_quality_from_xera(sp_xera)
    sp_ip = _sp_projected_ip(sp_recent_avg_ip)
    sp_share = sp_ip / 9.0
    bp_share = 1.0 - sp_share

    # Bullpen side
    rps = build_reliever_profiles(team_id, slate_date)
    bp_result = compute_bullpen_quality(rps)
    if bp_result.n_relievers == 0:
        notes.append("bullpen data unavailable; using league-average proxy")
        data_quality = "missing" if sp_xera is None else "partial"
    else:
        data_quality = "ok"
        if bp_result.n_fatigued >= 2:
            notes.append(
                f"{bp_result.n_fatigued} relievers fatigued (factor < 0.85)"
            )

    pqi = sp_quality * sp_share + bp_result.quality * bp_share

    return PQIResult(
        team=team,
        sp_id=0,
        sp_quality=sp_quality,
        bp_quality=bp_result.quality,
        sp_projected_ip=sp_ip,
        pqi=pqi,
        data_quality=data_quality,
        notes=notes,
    )


def pqi_diff(home_team: str,
             home_team_id: int,
             home_sp_xera: Optional[float],
             home_sp_recent_ip: Optional[float],
             away_team: str,
             away_team_id: int,
             away_sp_xera: Optional[float],
             away_sp_recent_ip: Optional[float],
             slate_date: date) -> Tuple[float, PQIResult, PQIResult]:
    """Compute (home_pqi - away_pqi).  Positive = home favored on PQI."""
    home = compute_team_pqi(home_team, home_team_id,
                             home_sp_xera, home_sp_recent_ip, slate_date)
    away = compute_team_pqi(away_team, away_team_id,
                             away_sp_xera, away_sp_recent_ip, slate_date)
    return (home.pqi - away.pqi), home, away


# ---------------------------------------------------------------------------
# Grading-modifier hook for parlay_builder
# ---------------------------------------------------------------------------
# Calibration thresholds (from rough sensitivity check on 4/28-5/3 data):
#   |pqi_diff| < 3.0   -> noise; no signal
#   |pqi_diff| 3-8     -> weak agreement (+/- 1)
#   |pqi_diff| > 8     -> strong agreement (+/- 1, capped at 1)
# We deliberately cap at +/-1 — PQI is one signal among several and
# shouldn't dominate the grade.
PQI_NOISE_THRESHOLD = 3.0


def pqi_grade_modifier(pqi_diff_value: float,
                        picked_side: str,
                        home_team: str,
                        away_team: str) -> Tuple[int, str]:
    """Return (modifier, reason) where modifier ∈ {-1, 0, +1}.

    + 1   PQI confirms picked side
    - 1   PQI contradicts picked side (late-game-degradation flag)
      0   PQI is too close to call (within noise threshold)
    """
    abs_diff = abs(pqi_diff_value)
    if abs_diff < PQI_NOISE_THRESHOLD:
        return 0, "PQI within noise threshold (no modifier)"

    pqi_winner = home_team if pqi_diff_value > 0 else away_team
    sign = "+1" if pqi_winner == picked_side else "-1"
    label = ("confirms" if pqi_winner == picked_side
             else "AGAINST (late-game-degradation)")
    note = f"PQI {label} pick (Δ={pqi_diff_value:+.1f})"
    return (1 if pqi_winner == picked_side else -1), note
