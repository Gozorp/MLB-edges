"""
mlb_edge/series_meta_writer.py
==============================
Writes per-slate `docs/data/series_meta_<date>.json` with each game's
series-game label (e.g. "G2 of 3") so the dashboard can disambiguate
which game of a multi-game series each row represents.

User feedback (2026-05-23): the same team matchups (TB @ NYY, STL @ CIN)
appear on consecutive days because MLB plays 3-4 game series.  Without
a series indicator, the dashboard reads as if it's showing the same
game twice on different dates.  This sidecar fixes the display.

Schema (versioned):
{
  "schema_version": 1,
  "generated_at": "2026-05-23T22:54:00Z",
  "slate_date": "2026-05-23",
  "matchups": {
    "TB @ NYY": [
      {"game_number": 1, "label": "G2 of 3"}
    ],
    "STL @ CIN": [
      {"game_number": 1, "label": "G1 of 3"},
      {"game_number": 2, "label": "G2 of 3"}
    ]
  }
}

game_number is the day-of-day index (1 for normal games, 1 + 2 for
doubleheaders).  When the dashboard renders a slate row, it looks up
the row's matchup and takes the Nth label based on which occurrence
in the slate frame it is.

Per Architecture-Session Pre-Flight Prompt v1.0:
  Rule 1   probed: MLB API /api/v1/schedule?hydrate=probablePitcher,team
           returns seriesGameNumber + gamesInSeries fields per game
  Rule 5   single-purpose sidecar writer; pure additive, no existing
           code paths modified
  Rule 6   best-effort throughout; missing schedule, malformed JSON,
           or network failure writes an empty payload rather than
           crashing the calling pipeline
  Rule 11  schema_version field protects downstream consumers from
           silent drift; future field additions bump the version
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATSAPI_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


# ---------------------------------------------------------------------------
# Team-abbreviation normalization
# ---------------------------------------------------------------------------
def _team_abbr(team_dict: dict) -> str:
    """Return the team's abbreviation, with fallbacks for missing data.
    MLB API exposes `abbreviation` on most teams; some older / minor
    records only have `name` or `teamCode`.  We try several fields
    before giving up.
    """
    if not isinstance(team_dict, dict):
        return ""
    for key in ("abbreviation", "teamCode", "fileCode"):
        v = team_dict.get(key)
        if v:
            return str(v).upper()
    name = team_dict.get("name", "")
    if name:
        # Last-resort: take first 3 letters of the team name
        return name[:3].upper()
    return ""


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------
def _fetch_schedule(slate_date: date, timeout_sec: int = 15
                    ) -> Optional[List[dict]]:
    """Fetch the schedule for `slate_date` from MLB Stats API; return
    the list of game dicts or None on failure."""
    url = (f"{STATSAPI_SCHEDULE_URL}"
           f"?sportId=1&date={slate_date.isoformat()}"
           f"&hydrate=probablePitcher,team")
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as r:
            payload = json.loads(r.read())
        dates = payload.get("dates", [])
        if not dates:
            return []
        return dates[0].get("games", [])
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError) as e:
        log.warning("[series_meta] schedule fetch failed: %s", e)
        return None
    except Exception as e:
        log.warning("[series_meta] unexpected error: %s", e)
        return None


def _build_matchups_block(games: List[dict]) -> Dict[str, List[dict]]:
    """Group games by matchup, sort by game_number, attach series label."""
    out: Dict[str, List[dict]] = {}
    for g in games or []:
        teams = g.get("teams", {})
        home = _team_abbr(teams.get("home", {}).get("team", {}))
        away = _team_abbr(teams.get("away", {}).get("team", {}))
        if not home or not away:
            continue
        matchup = f"{away} @ {home}"
        sg_num = g.get("seriesGameNumber")
        in_series = g.get("gamesInSeries")
        game_num = g.get("gameNumber", 1) or 1
        label = None
        if isinstance(sg_num, int) and isinstance(in_series, int) and in_series > 0:
            label = f"G{sg_num} of {in_series}"
        elif isinstance(sg_num, int):
            label = f"G{sg_num}"
        if not label:
            continue
        out.setdefault(matchup, []).append({
            "game_number": int(game_num),
            "label": label,
        })
    # Sort each matchup's games by day-of-day index
    for matchup in out:
        out[matchup].sort(key=lambda e: e["game_number"])
    return out


# ---------------------------------------------------------------------------
# Top-level writer
# ---------------------------------------------------------------------------
def _empty_payload(slate_date: date, reason: str) -> Dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at":   datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "slate_date":     slate_date.isoformat(),
        "matchups":       {},
        "_empty_reason":  reason,
    }


def write_series_meta(slate_date: date,
                      out_dir: str = "docs/data") -> Optional[str]:
    """Write `docs/data/series_meta_<slate_date>.json`.

    Best-effort per Rule 6: any failure logs a warning and returns None.
    Calling pipeline should NOT depend on this file existing.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir,
                                f"series_meta_{slate_date.isoformat()}.json")
        games = _fetch_schedule(slate_date)
        if games is None:
            payload = _empty_payload(slate_date, "MLB API fetch failed")
        elif not games:
            payload = _empty_payload(slate_date, "no games scheduled")
        else:
            matchups = _build_matchups_block(games)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "generated_at":   datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "slate_date":     slate_date.isoformat(),
                "matchups":       matchups,
            }
            log.info("[series_meta] wrote %d matchups for %s",
                     len(matchups), slate_date)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return out_path
    except Exception as e:
        log.warning("[series_meta] top-level write failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI for ad-hoc generation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    p.add_argument("--out-dir", default="docs/data")
    args = p.parse_args()
    sd = datetime.strptime(args.date, "%Y-%m-%d").date()
    path = write_series_meta(sd, args.out_dir)
    print(f"wrote: {path}")
    if path:
        with open(path) as f:
            d = json.load(f)
        print(f"matchups: {len(d.get('matchups', {}))}")
        for m, labels in list(d.get("matchups", {}).items())[:5]:
            print(f"  {m}: {labels}")
