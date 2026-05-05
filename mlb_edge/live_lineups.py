"""
live_lineups.py
---------------
Pull confirmed starting pitchers + batting orders from the MLB Stats API at
T-30 minutes (or whenever this is invoked — the API publishes lineups when
the manager turns them in, typically 30-90 min before first pitch).

Output schema (one row per slot per team):
    game_pk, home_abbr, away_abbr, side, batter_id, batter_name, bat_hand,
    order_slot, sp_id, sp_name, sp_throws

Two endpoints:
    /api/v1/schedule         — gives game_pk + probable pitchers
    /api/v1.1/game/<pk>/feed/live  — gives confirmed lineups once posted

Cross-references the result with `recursive_weight_update.apply_blowout_penalties`
in two ways:
    1. Tells us whether to trust today's player-level features (e.g., a
       previously-confirmed star that's been scratched should drop the
       team's wRC+/handedness signal).
    2. Provides the `pick_winner` audit material that the post-slate
       `auto_weight_update.py` uses to pair predictions with outcomes.

Designed to fail soft: if lineups aren't posted yet, returns the probable
pitcher only and flags `lineup_confirmed=False`. The model treats unconfirmed
lineups as a soft demotion (PLATINUM → GOLD) so we don't fire on stale info.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

from .stadiums import normalize_team

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"


@dataclass
class LineupSlot:
    game_pk: int
    home_abbr: str
    away_abbr: str
    side: str          # "home" | "away"
    batter_id: int
    batter_name: str
    bat_hand: str      # "R" | "L" | "S"
    order_slot: int    # 1-9
    sp_id: Optional[int]
    sp_name: Optional[str]
    sp_throws: Optional[str]
    lineup_confirmed: bool


@dataclass
class GameMeta:
    game_pk: int
    home_abbr: str
    away_abbr: str
    home_sp_id: Optional[int]
    home_sp_name: Optional[str]
    home_sp_throws: Optional[str]
    away_sp_id: Optional[int]
    away_sp_name: Optional[str]
    away_sp_throws: Optional[str]
    home_lineup: List[LineupSlot] = field(default_factory=list)
    away_lineup: List[LineupSlot] = field(default_factory=list)
    home_lineup_confirmed: bool = False
    away_lineup_confirmed: bool = False
    first_pitch_utc: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Schedule + per-game live feed
# ---------------------------------------------------------------------------
def _fetch_schedule(date_str: str) -> List[Dict]:
    try:
        r = requests.get(
            SCHEDULE_URL,
            params={
                "sportId": 1,
                "date": date_str,
                "hydrate": "probablePitcher,lineups",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("Schedule fetch failed for %s: %s", date_str, e)
        return []
    out = []
    for d in data.get("dates", []):
        out.extend(d.get("games", []))
    return out


def _fetch_game_feed(game_pk: int) -> Optional[Dict]:
    try:
        r = requests.get(GAME_FEED_URL.format(pk=game_pk), timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("Game feed fetch failed for %s: %s", game_pk, e)
        return None


# ---------------------------------------------------------------------------
# Lineup parsing
# ---------------------------------------------------------------------------
def _parse_batting_order(box_team: Dict, players: Dict) -> List[Dict]:
    """Pulls the 9-slot batting order from a boxscore.teams.<side> block.

    `box_team["battingOrder"]` is a list of player IDs as strings (e.g.
    ["592450", "592663", ...]). We hydrate each one via the top-level
    `players` map for handedness + name.
    """
    orders = box_team.get("battingOrder") or []
    rows = []
    for slot, pid_str in enumerate(orders, start=1):
        pid_key = f"ID{pid_str}"
        info = players.get(pid_key, {})
        person = info.get("person", {})
        rows.append({
            "batter_id": int(pid_str),
            "batter_name": person.get("fullName", ""),
            "bat_hand": (info.get("batSide", {}).get("code") or "R"),
            "order_slot": slot,
        })
    return rows


def _extract_meta(game: Dict) -> GameMeta:
    """Extract probable-pitcher info from a single schedule game block."""
    teams = game.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_full = home.get("team", {}).get("name", "")
    away_full = away.get("team", {}).get("name", "")
    home_pp = home.get("probablePitcher") or {}
    away_pp = away.get("probablePitcher") or {}

    fp_iso = game.get("gameDate", "")
    fp_utc = None
    if fp_iso:
        try:
            fp_utc = datetime.fromisoformat(fp_iso.replace("Z", "+00:00"))
        except ValueError:
            pass

    return GameMeta(
        game_pk=int(game["gamePk"]),
        home_abbr=normalize_team(home_full),
        away_abbr=normalize_team(away_full),
        home_sp_id=home_pp.get("id"),
        home_sp_name=home_pp.get("fullName"),
        home_sp_throws=None,   # filled from feed later
        away_sp_id=away_pp.get("id"),
        away_sp_name=away_pp.get("fullName"),
        away_sp_throws=None,
        first_pitch_utc=fp_utc,
    )


def _hydrate_lineup(meta: GameMeta) -> GameMeta:
    """Fill the lineup slots from the live game feed (if posted)."""
    feed = _fetch_game_feed(meta.game_pk)
    if not feed:
        return meta

    box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
    home_block = box.get("home", {})
    away_block = box.get("away", {})
    home_players = home_block.get("players", {})
    away_players = away_block.get("players", {})

    # Pitcher hand/throws — pull from the players blob if present
    def _throws(block: Dict, sp_id: Optional[int]) -> Optional[str]:
        if sp_id is None:
            return None
        info = block.get("players", {}).get(f"ID{sp_id}", {})
        return (info.get("pitchHand", {}) or {}).get("code")

    meta.home_sp_throws = _throws(home_block, meta.home_sp_id)
    meta.away_sp_throws = _throws(away_block, meta.away_sp_id)

    home_rows = _parse_batting_order(home_block, home_players)
    away_rows = _parse_batting_order(away_block, away_players)
    meta.home_lineup_confirmed = len(home_rows) == 9
    meta.away_lineup_confirmed = len(away_rows) == 9

    meta.home_lineup = [
        LineupSlot(
            game_pk=meta.game_pk, home_abbr=meta.home_abbr, away_abbr=meta.away_abbr,
            side="home", **row,
            sp_id=meta.home_sp_id, sp_name=meta.home_sp_name,
            sp_throws=meta.home_sp_throws,
            lineup_confirmed=meta.home_lineup_confirmed,
        ) for row in home_rows
    ]
    meta.away_lineup = [
        LineupSlot(
            game_pk=meta.game_pk, home_abbr=meta.home_abbr, away_abbr=meta.away_abbr,
            side="away", **row,
            sp_id=meta.away_sp_id, sp_name=meta.away_sp_name,
            sp_throws=meta.away_sp_throws,
            lineup_confirmed=meta.away_lineup_confirmed,
        ) for row in away_rows
    ]
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_slate_lineups(date_str: str) -> pd.DataFrame:
    """Single call — returns long-format DF (one row per batter per team).

    Columns:
        game_pk, home_abbr, away_abbr, side, batter_id, batter_name,
        bat_hand, order_slot, sp_id, sp_name, sp_throws, lineup_confirmed
    """
    games = _fetch_schedule(date_str)
    if not games:
        return pd.DataFrame()

    rows: List[Dict] = []
    for g in games:
        meta = _hydrate_lineup(_extract_meta(g))
        for slot in (meta.home_lineup + meta.away_lineup):
            rows.append(slot.__dict__)
        # When the lineup hasn't dropped, still emit a 1-row "SP-only" stub
        # so the orchestrator can see the matchup exists.
        if not meta.home_lineup and not meta.away_lineup:
            rows.append({
                "game_pk": meta.game_pk,
                "home_abbr": meta.home_abbr,
                "away_abbr": meta.away_abbr,
                "side": None, "batter_id": None, "batter_name": None,
                "bat_hand": None, "order_slot": None,
                "sp_id": meta.home_sp_id, "sp_name": meta.home_sp_name,
                "sp_throws": meta.home_sp_throws,
                "lineup_confirmed": False,
            })
    return pd.DataFrame(rows)


def fetch_slate_meta(date_str: str) -> List[GameMeta]:
    """One GameMeta per game on the slate. Useful when the orchestrator wants
    structured per-game data rather than a long-format batter table."""
    return [_hydrate_lineup(_extract_meta(g)) for g in _fetch_schedule(date_str)]


def has_late_scratch(prior_lineup: pd.DataFrame, current_lineup: pd.DataFrame) -> List[Dict]:
    """Compare a stored lineup snapshot to the current pull. Returns the list
    of (team, scratched_player) tuples — used by the orchestrator to decide
    whether to demote a tier when a starter sits.

    `prior_lineup` and `current_lineup` are both long-format outputs from
    `fetch_slate_lineups()`.
    """
    if prior_lineup.empty or current_lineup.empty:
        return []
    keys = ["game_pk", "side"]
    prior_set = set(zip(prior_lineup["game_pk"], prior_lineup["side"],
                        prior_lineup["batter_id"]))
    cur_set = set(zip(current_lineup["game_pk"], current_lineup["side"],
                      current_lineup["batter_id"]))
    missing = prior_set - cur_set
    out: List[Dict] = []
    for game_pk, side, bid in missing:
        match = prior_lineup[(prior_lineup["game_pk"] == game_pk)
                             & (prior_lineup["side"] == side)
                             & (prior_lineup["batter_id"] == bid)]
        if not match.empty:
            r = match.iloc[0]
            out.append({"game_pk": game_pk, "side": side,
                        "batter_id": bid,
                        "batter_name": r["batter_name"],
                        "team": r["home_abbr"] if side == "home" else r["away_abbr"]})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--summary", action="store_true",
                   help="print one-line per game (matchup + SP)")
    args = p.parse_args()

    if args.summary:
        for m in fetch_slate_meta(args.date):
            print(f"{m.away_abbr} @ {m.home_abbr}: "
                  f"{m.away_sp_name or '?'} ({m.away_sp_throws or '?'}) vs "
                  f"{m.home_sp_name or '?'} ({m.home_sp_throws or '?'}) "
                  f"[home_conf={m.home_lineup_confirmed}, "
                  f"away_conf={m.away_lineup_confirmed}]")
    else:
        df = fetch_slate_lineups(args.date)
        if df.empty:
            print("No games.")
        else:
            print(df.to_string(index=False))
