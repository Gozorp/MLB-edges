"""
mlb_edge/injury_news.py
-----------------------
Tier 2 v1 — deterministic injury / lineup-scratch detection from the
MLB Stats API.  Fills the gap that ``live_news.py`` left open: the SP
late-scratch detector catches pitching changes, but a star position
player going on the IL or getting a day-off doesn't show up anywhere
in the existing pipeline.

Two independent fetchers:

  1. fetch_il_placements(slate_date, lookback_days=10)
       Pulls /api/v1/transactions and parses the description text for
       NEW MLB-level IL placements (not transfers between IL types,
       which are stale news).  Returns {team_abbr: [ILRecord]}.

  2. fetch_lineup_snapshot(slate_date)
       Pulls /api/v1/schedule?hydrate=lineups and returns the current
       starting-lineup composition per game_pk: {game_pk: {"home":
       [player_ids], "away": [player_ids]}}.  Empty until lineups post
       ~3-4 hours before first pitch.  detect_scratches() diffs this
       against an anchor saved earlier in the day.

Tier 2 stays *deterministic* — every input is structured JSON.  No HTML
scraping, no LLM extraction.  The fragile sources (beat-reporter
scrapes, manager pressers) are deferred to Tier 2.5 once we've
validated that Tier 2 v1 actually moves prediction quality.

Override semantics
==================
On a regular-player IL placement OR an in-day lineup scratch:
  - News rule fires: -1.5pp toward the affected team
  - Tier demotion: 1 step (PLATINUM -> GOLD, etc.)
  - Magnitudes scale with `is_regular` (60+ PA = real bat, not bench)

Cumulative when multiple regulars are out: -3pp for two scratches, -4.5pp
for three+.  Capped to avoid runaway adjustments.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

log = logging.getLogger(__name__)

TRANSACTIONS_URL = "https://statsapi.mlb.com/api/v1/transactions"
SCHEDULE_URL     = "https://statsapi.mlb.com/api/v1/schedule"
LINEUP_ANCHOR_DIR = Path("data/news_cache/lineup_anchors")

# Pattern matching "TEAM placed POSITION PLAYER NAME on the X-day injured list"
_IL_PLACE_RX = re.compile(
    r"^(?P<team>[\w .\-']+?)\s+placed\s+(?:[\w]+\s+)?(?P<player>[\w .\-']+?)\s+"
    r"on\s+the\s+(?P<days>\d+)-day\s+injured\s+list",
    re.IGNORECASE,
)

# 30 MLB team-name -> abbreviation map.  Filters out minor-league txs.
MLB_TEAMS = {
    "Arizona Diamondbacks":  "AZ",
    "Atlanta Braves":        "ATL",
    "Baltimore Orioles":     "BAL",
    "Boston Red Sox":        "BOS",
    "Chicago Cubs":          "CHC",
    "Chicago White Sox":     "CWS",
    "Cincinnati Reds":       "CIN",
    "Cleveland Guardians":   "CLE",
    "Colorado Rockies":      "COL",
    "Detroit Tigers":        "DET",
    "Houston Astros":        "HOU",
    "Kansas City Royals":    "KC",
    "Los Angeles Angels":    "LAA",
    "Los Angeles Dodgers":   "LAD",
    "Miami Marlins":         "MIA",
    "Milwaukee Brewers":     "MIL",
    "Minnesota Twins":       "MIN",
    "New York Mets":         "NYM",
    "New York Yankees":      "NYY",
    "Athletics":             "OAK",
    "Oakland Athletics":     "OAK",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates":    "PIT",
    "San Diego Padres":      "SD",
    "San Francisco Giants":  "SF",
    "Seattle Mariners":      "SEA",
    "St. Louis Cardinals":   "STL",
    "Tampa Bay Rays":        "TB",
    "Texas Rangers":         "TEX",
    "Toronto Blue Jays":     "TOR",
    "Washington Nationals":  "WSH",
}


# ---------------------------------------------------------------------------
# Data records
# ---------------------------------------------------------------------------
@dataclass
class ILRecord:
    """One IL placement event."""
    team_abbr: str
    player_id: Optional[int]
    player_name: str
    transaction_date: str   # ISO date string
    il_days: int            # 7, 10, 15, 60 ...
    description: str


@dataclass
class ScratchRecord:
    """A player who was in the anchor lineup but not the current."""
    game_pk: int
    side: str               # "home" or "away"
    player_id: int
    player_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Fetcher 1 — IL transactions
# ---------------------------------------------------------------------------
def fetch_il_placements(slate_date: date,
                         lookback_days: int = 10,
                         timeout: int = 12) -> Dict[str, List[ILRecord]]:
    """Return ``{team_abbr: [ILRecord, ...]}`` for NEW IL placements
    in the prior `lookback_days` days, MLB-only (minor-league txs
    filtered out)."""
    start = (slate_date - timedelta(days=lookback_days)).isoformat()
    end   = slate_date.isoformat()
    try:
        r = requests.get(TRANSACTIONS_URL,
                         params={"startDate": start, "endDate": end},
                         timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("transactions fetch failed: %s", e)
        return {}

    out: Dict[str, List[ILRecord]] = {}
    for tx in data.get("transactions", []):
        desc = (tx.get("description") or "").strip()
        if not desc:
            continue
        # Match: "TEAM placed PLAYER on the X-day injured list..."
        m = _IL_PLACE_RX.match(desc)
        if not m:
            continue
        team_name = m.group("team").strip()
        if team_name not in MLB_TEAMS:
            continue
        team_abbr = MLB_TEAMS[team_name]
        rec = ILRecord(
            team_abbr=team_abbr,
            player_id=(tx.get("person") or {}).get("id"),
            player_name=m.group("player").strip(),
            transaction_date=tx.get("date") or "",
            il_days=int(m.group("days")),
            description=desc,
        )
        out.setdefault(team_abbr, []).append(rec)
    return out


# ---------------------------------------------------------------------------
# Fetcher 2 — lineup snapshot
# ---------------------------------------------------------------------------
def fetch_lineup_snapshot(slate_date: date,
                           timeout: int = 12) -> Dict[int, Dict[str, List[int]]]:
    """Return ``{game_pk: {"home": [player_ids], "away": [player_ids]}}``
    based on the current state of /schedule?hydrate=lineups.  Empty
    lists for games whose lineups haven't posted yet."""
    try:
        r = requests.get(SCHEDULE_URL,
                         params={"sportId": 1,
                                 "date":    slate_date.isoformat(),
                                 "hydrate": "lineups,team"},
                         timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("schedule lineups fetch failed: %s", e)
        return {}

    out: Dict[int, Dict[str, List[int]]] = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            gpk = g.get("gamePk")
            if not gpk:
                continue
            lineups = g.get("lineups") or {}
            home_ids = [p.get("id") for p in lineups.get("homePlayers", [])
                        if p.get("id")]
            away_ids = [p.get("id") for p in lineups.get("awayPlayers", [])
                        if p.get("id")]
            out[gpk] = {"home": home_ids, "away": away_ids}
    return out


# ---------------------------------------------------------------------------
# Anchor (first-of-day lineup snapshot persistence)
# ---------------------------------------------------------------------------
def _anchor_path(slate_date: date) -> Path:
    return LINEUP_ANCHOR_DIR / f"lineup_anchor_{slate_date.isoformat()}.json"


def load_lineup_anchor(slate_date: date) -> Dict[int, Dict[str, List[int]]]:
    p = _anchor_path(slate_date)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        return {int(k): v for k, v in raw.items()}
    except Exception as e:
        log.warning("lineup anchor read failed: %s", e)
        return {}


def save_lineup_anchor(slate_date: date,
                        snapshot: Dict[int, Dict[str, List[int]]]) -> None:
    if not snapshot:
        return
    LINEUP_ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _anchor_path(slate_date).write_text(json.dumps(
            {str(k): v for k, v in snapshot.items()}, indent=2))
    except Exception as e:
        log.warning("lineup anchor write failed: %s", e)


def detect_scratches(anchor: Dict[int, Dict[str, List[int]]],
                     current: Dict[int, Dict[str, List[int]]]
                     ) -> Dict[int, Dict[str, List[int]]]:
    """For each game_pk, return ``{home: [missing_ids], away: [missing_ids]}``.

    A "scratch" = a player_id present in the anchor but not in current.
    Only fires when both anchor and current have at least one player
    on that side (avoid false positives from "anchor was full, current
    is empty because lineup got pulled").
    """
    out: Dict[int, Dict[str, List[int]]] = {}
    for gpk, anchor_sides in anchor.items():
        current_sides = current.get(gpk, {})
        scratches: Dict[str, List[int]] = {"home": [], "away": []}
        for side in ("home", "away"):
            anchor_set: Set[int]  = set(anchor_sides.get(side, []))
            current_set: Set[int] = set(current_sides.get(side, []))
            # Skip silly diffs from one side being empty.
            if not anchor_set or not current_set:
                continue
            missing = sorted(anchor_set - current_set)
            if missing:
                scratches[side] = missing
        if scratches["home"] or scratches["away"]:
            out[gpk] = scratches
    return out


# ---------------------------------------------------------------------------
# Regular-player gate — only fire override when the missing player is a real
# bat, not a 30-PA bench piece.
# ---------------------------------------------------------------------------
_REGULAR_PA_THRESHOLD = 60


def _load_batter_pa_lookup() -> Dict[int, int]:
    """Build {player_id: season_pa} from the most recent Savant batter
    expected-stats CSV.  Used to gate the override magnitude."""
    import glob, csv
    csvs = sorted(glob.glob("data/savant_extra/savant_expected-stats-batter_*.csv"))
    if not csvs:
        return {}
    out: Dict[int, int] = {}
    try:
        with open(csvs[-1]) as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get("player_id")
                pa  = row.get("pa")
                if pid and pa:
                    try:
                        out[int(pid)] = int(pa)
                    except (TypeError, ValueError):
                        continue
    except Exception as e:
        log.debug("batter PA lookup failed: %s", e)
    return out


def is_regular(player_id: int,
               pa_lookup: Optional[Dict[int, int]] = None) -> bool:
    """True if the player has at least _REGULAR_PA_THRESHOLD PA this season."""
    if pa_lookup is None:
        pa_lookup = _load_batter_pa_lookup()
    return pa_lookup.get(player_id, 0) >= _REGULAR_PA_THRESHOLD


# ---------------------------------------------------------------------------
# Per-team summary used by live_news to apply overrides
# ---------------------------------------------------------------------------
@dataclass
class InjurySummary:
    """One row per team for tonight's slate."""
    team_abbr: str
    n_il_placements: int = 0
    il_player_names: List[str] = field(default_factory=list)
    n_lineup_scratches: int = 0
    scratch_player_ids: List[int] = field(default_factory=list)
    # number of "regular" (60+ PA) scratches — drives the magnitude
    n_regular_scratches: int = 0


def summarize_for_team(team_abbr: str,
                        il_placements: Dict[str, List[ILRecord]],
                        scratches_for_team: List[int],
                        pa_lookup: Dict[int, int]) -> InjurySummary:
    summary = InjurySummary(team_abbr=team_abbr)
    for rec in il_placements.get(team_abbr, []):
        summary.n_il_placements += 1
        summary.il_player_names.append(rec.player_name)
    for pid in scratches_for_team:
        summary.n_lineup_scratches += 1
        summary.scratch_player_ids.append(pid)
        if is_regular(pid, pa_lookup):
            summary.n_regular_scratches += 1
    return summary
