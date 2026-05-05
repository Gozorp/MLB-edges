"""
mlb_edge/data_sources/umpire.py
-------------------------------
Plate-umpire assignment fetcher + season-bias lookup.

Two responsibilities:
    1. Get the home-plate ump for a specific game from the MLB Stats API
       live-feed endpoint (officials are populated ~3-4 hours before first
       pitch).
    2. Look up that ump's season-to-date strike-zone bias from a curated
       reference table.  v1 ships with a *placeholder* bias of 0.0 for
       every ump until we backfill the table from historical pitch-by-pitch
       data (see TODO at bottom).  The shape of the API is finalized now so
       the consuming code (live_news.py) doesn't churn when the real biases
       land.

Design notes
============
- The live-feed endpoint is `/api/v1.1/game/{gamePk}/feed/live` — the
  officials list lives at `liveData.boxscore.officials` after the lineups
  are posted.  Before lineup post, the field is an empty list, in which
  case we return None (caller treats as "ump unknown -> neutral").
- Bias is encoded as a *home-team-prob delta* in percentage points.  A
  +0.020 value means "this ump's zone gives a ~2pp boost to whoever has
  the better pitching profile" — sign and magnitude matter for the
  override calculation in live_news.apply_overrides().
- Cache responses on disk so a 14-game slate makes 14 API calls per run
  instead of 14*N for repeated checks.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import requests

log = logging.getLogger(__name__)

LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live"
CACHE_DIR = Path("data/news_cache/umpires")
CACHE_TTL_SECONDS = 60 * 30  # 30 min — officials don't change once posted


@dataclass(frozen=True)
class UmpireAssignment:
    """One row per game.  All fields optional; consumers must handle None."""
    game_pk: int
    plate_ump_id: Optional[int]
    plate_ump_name: Optional[str]
    plate_ump_bias_pp: float          # signed pp adjustment (Tier 0 placeholder = 0.0)
    rationale: str                    # human-readable: "ump <name> bias +2.1pp"


# ---------------------------------------------------------------------------
# Bias table — placeholder for v1
# ---------------------------------------------------------------------------
# Populated by a future offline job that parses historical Statcast called
# strikes vs. expected zone for each ump.  Until that job runs, every ump
# resolves to 0.0 -> the override layer is a no-op for ump bias.
#
# The schema is locked in so live_news.py doesn't have to change when the
# real numbers arrive:
#     UMP_BIAS[plate_ump_id] = signed pp delta to home_team model_prob
#
# Sign convention: positive = ump's zone *favors pitchers* (advantage to the
# team with the more pitcher-friendly profile), negative = favors hitters.
# live_news.apply_overrides() uses the model's xERA gap to decide which side
# the bias accrues to.
UMP_BIAS: Dict[int, float] = {
    # int(person_id): float(pp adjustment)
    # populated by tools/build_ump_bias_table.py once it lands; intentionally
    # empty in v1.
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_path(game_pk: int) -> Path:
    return CACHE_DIR / f"officials_{game_pk}.json"


def _read_cache(game_pk: int) -> Optional[dict]:
    p = _cache_path(game_pk)
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        return json.loads(p.read_text())
    except Exception as e:
        log.debug("ump cache read failed for %s: %s", game_pk, e)
        return None


def _write_cache(game_pk: int, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(game_pk).write_text(json.dumps(payload))
    except Exception as e:
        log.debug("ump cache write failed for %s: %s", game_pk, e)


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------
def _fetch_officials(game_pk: int, timeout: int = 8) -> Optional[list]:
    """Return the raw `officials` list (may be empty) or None on error."""
    cached = _read_cache(game_pk)
    if cached is not None:
        return cached.get("officials", [])

    url = LIVE_FEED_URL.format(gamePk=game_pk)
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("live-feed fetch failed for game %s: %s", game_pk, e)
        return None

    officials = (
        data.get("liveData", {})
            .get("boxscore", {})
            .get("officials", [])
    )
    _write_cache(game_pk, {"officials": officials})
    return officials


def _extract_plate_ump(officials: list) -> tuple[Optional[int], Optional[str]]:
    """Return (id, fullName) of the home-plate ump or (None, None)."""
    for o in officials or []:
        # MLB Stats API uses `officialType` = "Home Plate" for the plate ump.
        if (o.get("officialType") or "").lower() == "home plate":
            ump = o.get("official") or {}
            return ump.get("id"), ump.get("fullName")
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_assignment(game_pk: int) -> UmpireAssignment:
    """Resolve the plate-ump assignment + bias for a single game."""
    officials = _fetch_officials(game_pk)
    plate_id, plate_name = _extract_plate_ump(officials or [])
    bias = float(UMP_BIAS.get(plate_id, 0.0)) if plate_id else 0.0
    if plate_id is None:
        rationale = "ump unassigned (lineups not yet posted)"
    elif bias == 0.0:
        rationale = f"ump {plate_name or plate_id} (bias table not yet built)"
    else:
        sign = "+" if bias >= 0 else ""
        rationale = f"ump {plate_name or plate_id} bias {sign}{bias*100:.2f}pp"
    return UmpireAssignment(
        game_pk=game_pk,
        plate_ump_id=plate_id,
        plate_ump_name=plate_name,
        plate_ump_bias_pp=bias,
        rationale=rationale,
    )


def get_assignments_for_slate(game_pks: list[int]) -> Dict[int, UmpireAssignment]:
    """Bulk-resolve a slate's worth of game_pks. Skips games on error."""
    out: Dict[int, UmpireAssignment] = {}
    for gpk in game_pks:
        try:
            out[gpk] = get_assignment(gpk)
        except Exception as e:
            log.warning("ump resolve failed for %s: %s", gpk, e)
    return out


# ---------------------------------------------------------------------------
# TODO: tools/build_ump_bias_table.py
# ---------------------------------------------------------------------------
# Offline job to populate UMP_BIAS:
#   1. Pull last 2 seasons of Statcast called pitches.
#   2. For each pitch: was it a called strike?  Did it land inside the
#      league-average heart-of-zone polygon?
#   3. For each ump: aggregate (called_strikes_outside_zone + called_balls_inside)
#      / total_called_pitches.  Compare vs. league mean.
#   4. Translate "ump strike-zone tilt" into pp adjustment using the
#      historically observed pitch-call -> R/G coefficient (~0.4 R per
#      0.01 zone diff).  Convert R/G to home_win_prob via Pythagorean.
#   5. Write the dict literal back into this file.
