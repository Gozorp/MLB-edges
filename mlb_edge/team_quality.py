"""
mlb_edge/team_quality.py
------------------------
Team-quality grading modifier for the parlay grader.

WHY THIS EXISTS
===============
The model is fundamentally pitcher-driven: it weights SP xERA, bullpen
quality (PQI), and park factors heavily.  It does NOT directly use:

    - team season W-L record
    - team offensive output (wRC+, runs per game)
    - team last-10 form

This blind spot showed up on the 2026-05-04 CHW @ LAA matchup:
Soriano's 0.84 ERA dominated the SP edge and the model picked LAA at
A grade.  But CHW (16-18, .471) is structurally a better team than
LAA (13-22, .371) — and CHW's offense was good enough to capitalize
when Soriano had a bad night.  CWS won 5-0 in the 8th.

The market made the same mistake (LAA -163 / CHW +135), so this isn't
just a model issue — but it IS a place where the parlay grader can
add a small dampening signal to catch the structural gap.

WHAT IT COMPUTES
================
For each team in a matchup:

    Profile:
        wins, losses, win_pct
        runs_per_game (offensive)
        runs_allowed_per_game (defensive — informational)
        last_10_wins (recent form)

The modifier is a +/-1 added to the grade when the picked team is
materially weaker than the opponent on win% or recent form:

    Win% gap >= 0.070 (7pp): ±1 modifier
    Last-10 gap >= 3 games: ±0.5 modifier (rounds to ±1 when combined
        with win% in same direction)

Capped at +/-1 total — same as PQI.  Team quality is one signal; not
allowed to dominate the grade.

PUBLIC API
==========
    fetch_team_quality(team_id, season) -> TeamQualityProfile
    compute_team_quality_modifier(home_profile, away_profile,
                                   picked_side) -> (int, str)
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calibration thresholds
# ---------------------------------------------------------------------------
WIN_PCT_GAP_THRESHOLD = 0.070     # 7 percentage points = "structural" gap
LAST_10_GAP_THRESHOLD = 3         # 3 games out of 10 = "form" gap
RPG_GAP_THRESHOLD = 0.7           # 0.7 runs/game = meaningful offensive gap

# Map abbreviation -> MLB Stats API team ID (mirrors pitching_quality.TEAM_ID)
TEAM_ID = {
    "LAA": 108, "ARI": 109, "BAL": 110, "BOS": 111, "CHC": 112,
    "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117,
    "KC":  118, "LAD": 119, "WSH": 120, "NYM": 121, "ATH": 133,
    "OAK": 133, "PIT": 134, "SD":  135, "SEA": 136, "SF":  137,
    "STL": 138, "TB":  139, "TEX": 140, "TOR": 141, "MIN": 142,
    "PHI": 143, "ATL": 144, "CHW": 145, "CWS": 145, "MIA": 146,
    "NYY": 147, "MIL": 158,
}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class TeamQualityProfile:
    team_id: int
    abbr: str
    wins: int
    losses: int
    win_pct: float
    runs_per_game: float       # offensive output (runs scored / G)
    runs_allowed_per_game: float
    last_10_wins: int = 5      # default = neutral
    last_10_losses: int = 5
    games_played: int = 0

    @property
    def last_10_win_pct(self) -> float:
        denom = self.last_10_wins + self.last_10_losses
        return (self.last_10_wins / denom) if denom > 0 else 0.5


# ---------------------------------------------------------------------------
# Data ingestion (MLB Stats API)
# ---------------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 8) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug("[team_quality] fetch failed %s: %s", url, e)
        return None


def fetch_team_quality(team_id: int,
                       season: int,
                       abbr: str = "") -> Optional[TeamQualityProfile]:
    """Pull season W-L + offense from MLB Stats API.

    Returns None if any required field is missing.
    """
    # Standings — wins, losses, win pct
    standings_url = (
        f"https://statsapi.mlb.com/api/v1/standings?leagueId=103,104"
        f"&season={season}&standingsTypes=regularSeason"
    )
    s_data = _fetch_json(standings_url)
    wins = losses = None
    games_played = None
    last10_w = last10_l = None
    if s_data:
        for record in s_data.get("records", []):
            for tr in record.get("teamRecords", []):
                if tr.get("team", {}).get("id") == team_id:
                    wins = tr.get("wins")
                    losses = tr.get("losses")
                    games_played = tr.get("gamesPlayed")
                    # Last-10 record: nested under records.splitRecords with type='lastTen'
                    for split in tr.get("records", {}).get("splitRecords", []):
                        if split.get("type") == "lastTen":
                            last10_w = split.get("wins")
                            last10_l = split.get("losses")
                            break
                    break
            if wins is not None:
                break

    if wins is None or losses is None:
        return None

    win_pct = wins / (wins + losses) if (wins + losses) > 0 else 0.0

    # Team batting (offensive runs/game)
    batting_url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?"
        f"stats=season&group=hitting&season={season}"
    )
    b_data = _fetch_json(batting_url)
    runs_per_game = 0.0
    if b_data:
        try:
            stat = b_data["stats"][0]["splits"][0]["stat"]
            r = float(stat.get("runs", 0))
            g = float(stat.get("gamesPlayed", 0))
            runs_per_game = r / g if g > 0 else 0.0
        except (KeyError, IndexError, ValueError, TypeError):
            pass

    # Team pitching (runs allowed/game)
    pitching_url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?"
        f"stats=season&group=pitching&season={season}"
    )
    p_data = _fetch_json(pitching_url)
    runs_allowed_per_game = 0.0
    if p_data:
        try:
            stat = p_data["stats"][0]["splits"][0]["stat"]
            r = float(stat.get("runs", 0))
            g = float(stat.get("gamesPlayed", 0))
            runs_allowed_per_game = r / g if g > 0 else 0.0
        except (KeyError, IndexError, ValueError, TypeError):
            pass

    return TeamQualityProfile(
        team_id=team_id,
        abbr=abbr,
        wins=wins,
        losses=losses,
        win_pct=win_pct,
        runs_per_game=runs_per_game,
        runs_allowed_per_game=runs_allowed_per_game,
        last_10_wins=last10_w if last10_w is not None else 5,
        last_10_losses=last10_l if last10_l is not None else 5,
        games_played=games_played or (wins + losses),
    )


# ---------------------------------------------------------------------------
# Modifier computation
# ---------------------------------------------------------------------------
def compute_team_quality_modifier(home: TeamQualityProfile,
                                   away: TeamQualityProfile,
                                   picked_side: str,
                                   home_abbr: str,
                                   away_abbr: str,
                                   ) -> Tuple[int, str]:
    """Return (modifier, reason) for the picked side based on team quality.

    Logic:
      Compute three signals each comparing picked side vs opponent:
        - season win% gap
        - last-10 form gap
        - offensive RPG gap

      Each signal contributes +1/0/-1 toward the picked side.
      Average the contributions, round to nearest integer in [-1, +1].

    The result is added to the parlay grade as a small dampening /
    confirming signal — not allowed to dominate the grade.
    """
    if picked_side == home_abbr:
        picked, opponent = home, away
    elif picked_side == away_abbr:
        picked, opponent = away, home
    else:
        return 0, "team_quality: pick side mismatch"

    contributions = []
    notes = []

    # Win% signal
    win_pct_gap = picked.win_pct - opponent.win_pct
    if abs(win_pct_gap) >= WIN_PCT_GAP_THRESHOLD:
        sig = 1 if win_pct_gap > 0 else -1
        contributions.append(sig)
        notes.append(f"win% gap {win_pct_gap:+.3f}")

    # Last-10 form signal
    l10_gap = picked.last_10_win_pct - opponent.last_10_win_pct
    if abs(l10_gap) >= (LAST_10_GAP_THRESHOLD / 10.0):
        sig = 1 if l10_gap > 0 else -1
        contributions.append(sig)
        notes.append(f"L10 gap {l10_gap:+.2f}")

    # Offensive RPG signal
    rpg_gap = picked.runs_per_game - opponent.runs_per_game
    if abs(rpg_gap) >= RPG_GAP_THRESHOLD:
        sig = 1 if rpg_gap > 0 else -1
        contributions.append(sig)
        notes.append(f"RPG gap {rpg_gap:+.2f}")

    if not contributions:
        return 0, "team_quality: signals all within noise"

    # Average contributions, round to nearest int in [-1, +1]
    avg = sum(contributions) / len(contributions)
    if avg >= 0.5:
        mod = 1
    elif avg <= -0.5:
        mod = -1
    else:
        mod = 0

    if mod == 0:
        return 0, f"team_quality: signals net out (contributions={contributions})"

    sign = "confirms" if mod > 0 else "AGAINST"
    return mod, f"team_quality {sign} pick ({'; '.join(notes)})"


# ---------------------------------------------------------------------------
# Top-level helper for parlay_builder
# ---------------------------------------------------------------------------
def team_quality_modifier_for_matchup(matchup: str,
                                       picked_side: str,
                                       slate_date: Optional[date] = None,
                                       ) -> Tuple[int, str]:
    """Resolve a matchup string + pick into a team-quality modifier.

    Returns (0, '') silently when:
      - matchup string is malformed
      - team profiles can't be fetched (network / sandbox)
      - signals are all within noise
    """
    if slate_date is None:
        slate_date = date.today()
    if "@" not in matchup:
        return 0, ""

    away_abbr, home_abbr = (s.strip() for s in matchup.split("@", 1))
    home_id = TEAM_ID.get(home_abbr)
    away_id = TEAM_ID.get(away_abbr)
    if home_id is None or away_id is None:
        return 0, ""

    season = slate_date.year
    home_profile = fetch_team_quality(home_id, season, abbr=home_abbr)
    away_profile = fetch_team_quality(away_id, season, abbr=away_abbr)
    if home_profile is None or away_profile is None:
        return 0, ""

    return compute_team_quality_modifier(
        home_profile, away_profile, picked_side, home_abbr, away_abbr,
    )
