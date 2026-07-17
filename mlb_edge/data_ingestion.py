"""
data_ingestion.py
-----------------
Real implementations for pybaseball + the-odds-api historical endpoint.

Caching is aggressive — every Statcast pull and every odds snapshot is
written to disk. Historical odds calls are expensive ($$$ per request on
the paid tier) so we never re-request a cached date.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

try:
    from pybaseball import statcast, cache as pb_cache
    pb_cache.enable()
    _HAS_PYBASEBALL = True
except ImportError:
    _HAS_PYBASEBALL = False

from .config import DATA

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_path(prefix: str, key: str, ext: str = "parquet") -> Path:
    digest = hashlib.md5(key.encode()).hexdigest()[:12]
    base = Path(DATA.statcast_cache_dir) / prefix
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{digest}.{ext}"


def _load_parquet(path: Path) -> Optional[pd.DataFrame]:
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception as e:
            log.warning("Corrupt cache %s (%s); refetching", path, e)
    return None


def _save_parquet(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        log.warning("Cache write failed %s: %s", path, e)


# ---------------------------------------------------------------------------
# Statcast (via pybaseball)
# ---------------------------------------------------------------------------
def fetch_statcast_range(start: date, end: date,
                         chunk_days: int = 10) -> pd.DataFrame:
    """
    Pull pitch-by-pitch Statcast data for a date range. Chunks the request
    into `chunk_days`-sized pieces so a failure partway through doesn't lose
    everything, and each chunk caches independently.
    """
    if not _HAS_PYBASEBALL:
        raise RuntimeError("pybaseball not installed")

    chunks: List[pd.DataFrame] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        key = f"statcast_{cur.isoformat()}_{chunk_end.isoformat()}"
        cache = _cache_path("statcast_chunk", key)
        cached = _load_parquet(cache)
        if cached is not None:
            chunks.append(cached)
        else:
            log.info("Fetching Statcast %s → %s", cur, chunk_end)
            try:
                df = statcast(start_dt=cur.isoformat(), end_dt=chunk_end.isoformat())
                if df is not None and not df.empty:
                    _save_parquet(df, cache)
                    chunks.append(df)
                else:
                    log.warning("Empty Statcast %s → %s", cur, chunk_end)
            except Exception as e:
                log.error("Statcast fetch failed %s → %s: %s", cur, chunk_end, e)
        cur = chunk_end + timedelta(days=1)

    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def fetch_season_statcast(season: int) -> pd.DataFrame:
    """Full-season Statcast (regular season window, Mar 20 → Oct 5)."""
    return fetch_statcast_range(date(season, 3, 20), date(season, 10, 5))


def fetch_ytd_statcast(through: date) -> pd.DataFrame:
    """Statcast from Mar 20 of `through.year` up to and including `through`."""
    return fetch_statcast_range(date(through.year, 3, 20), through)


# ---------------------------------------------------------------------------
# MLB Stats API — free, no auth, used for live probable-pitcher lookups
# ---------------------------------------------------------------------------
def fetch_schedule_mlb_api(day: date) -> List[Dict]:
    """
    Return the day's MLB schedule with probable pitchers attached.
    Fetches from statsapi.mlb.com (free, no key).
    """
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": day.isoformat(),
        "hydrate": "probablePitcher,lineups,weather,team",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error("MLB Stats API fetch failed: %s", e)
        return []

    games = []
    slate_iso = day.isoformat()
    for dd in data.get("dates", []):
        for g in dd.get("games", []):
            # Skip games whose officialDate differs from the
            # requested slate date.  MLB returns postponed games
            # under their original gameDate in the schedule
            # endpoint, but their officialDate is the reschedule
            # date.  Without this check, rained-out games leak
            # into picks_<date>_diag.csv even though they're
            # booked for a different day (verified 2026-05-23
            # with TB@NYY officialDate=9/22 and DET@BAL
            # officialDate=5/24, both rained out from 5/23).
            official = g.get("officialDate")
            if official and official != slate_iso:
                log.info(
                    "[schedule] skip gamePk=%s: officialDate=%s"
                    " != slate %s (rescheduled / postponed)",
                    g.get("gamePk"), official, slate_iso,
                )
                continue
            games.append({
                "game_pk":        g.get("gamePk"),
                "game_date":      g.get("gameDate"),
                "status":         g.get("status", {}).get("detailedState"),
                "home_team":      g.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation"),
                "away_team":      g.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation"),
                "home_team_name": g.get("teams", {}).get("home", {}).get("team", {}).get("name"),
                "away_team_name": g.get("teams", {}).get("away", {}).get("team", {}).get("name"),
                "home_sp_id":     (g.get("teams", {}).get("home", {}).get("probablePitcher") or {}).get("id"),
                "away_sp_id":     (g.get("teams", {}).get("away", {}).get("probablePitcher") or {}).get("id"),
                "home_sp_name":   (g.get("teams", {}).get("home", {}).get("probablePitcher") or {}).get("fullName"),
                "away_sp_name":   (g.get("teams", {}).get("away", {}).get("probablePitcher") or {}).get("fullName"),
                "venue":          g.get("venue", {}).get("name"),
                # Per-game identity (2026-07-17): gameNumber/doubleHeader let
                # downstream code tell DH game 1 from game 2 — matchup
                # strings alone collide and have caused a whole bug family.
                "game_number":    g.get("gameNumber") or 1,
                "double_header":  (g.get("doubleHeader") or "N"),
            })
    return games


# ---------------------------------------------------------------------------
# The Odds API — both live and historical
# ---------------------------------------------------------------------------
class OddsClient:
    """
    Wrapper around the-odds-api.com /v4.

    Historical endpoint notes:
      - Snapshots are timestamped in UTC ISO format with minute precision.
      - Each call to /historical/... costs 10 requests from your quota.
      - We cache every snapshot to disk keyed by (timestamp, sport).
      - The API returns the MOST RECENT snapshot at or before the requested
        timestamp, not an interpolation.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            log.warning("ODDS_API_KEY not set — odds calls will fail")
        self.cache_dir = Path(DATA.odds_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, kind: str, key: str) -> Path:
        sub = self.cache_dir / kind
        sub.mkdir(parents=True, exist_ok=True)
        return sub / (hashlib.md5(key.encode()).hexdigest()[:14] + ".json")

    def _get(self, endpoint: str, params: Dict) -> Optional[Dict]:
        url = f"{DATA.odds_api_base}{endpoint}"
        params = {"apiKey": self.api_key, **params}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=20)
                remaining = r.headers.get("x-requests-remaining")
                used = r.headers.get("x-requests-used")
                if remaining is not None:
                    log.info("Odds API: %s remaining / %s used", remaining, used)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = 2 ** attempt
                    log.warning("Odds API %s → %s; retrying in %ss", endpoint, r.status_code, wait)
                    time.sleep(wait)
                    continue
                log.error("Odds API %s → %s: %s", endpoint, r.status_code, r.text[:300])
                return None
            except requests.RequestException as e:
                log.warning("Odds API request failed: %s (attempt %d)", e, attempt + 1)
                time.sleep(2 ** attempt)
        return None

    # --- Live ------------------------------------------------------------
    def current_lines(self) -> pd.DataFrame:
        """Current market snapshot. 1 request per call."""
        params = {
            "regions": DATA.odds_regions,
            "markets": DATA.odds_markets,
            "oddsFormat": "american",
            "bookmakers": ",".join(DATA.odds_bookmakers),
        }
        data = self._get(f"/sports/{DATA.odds_sport}/odds", params)
        return _flatten_odds_payload(data) if data else pd.DataFrame()

    # --- Historical ------------------------------------------------------
    def historical_snapshot(self, timestamp_utc: datetime) -> pd.DataFrame:
        """
        Historical odds snapshot at the given UTC timestamp.

        Costs 10 requests from quota per uncached call.
        Cached permanently once fetched.
        """
        iso = timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        cache = self._cache_path("historical", iso)
        if cache.exists():
            try:
                data = json.loads(cache.read_text())
                return _flatten_odds_payload(data)
            except Exception:
                log.warning("Corrupt odds cache at %s; refetching", cache)

        params = {
            "regions": DATA.odds_regions,
            "markets": DATA.odds_markets,
            "oddsFormat": "american",
            "date": iso,
        }
        data = self._get(f"/historical/sports/{DATA.odds_sport}/odds", params)
        if data is None:
            return pd.DataFrame()

        try:
            cache.write_text(json.dumps(data))
        except Exception as e:
            log.warning("Odds cache write failed: %s", e)

        return _flatten_odds_payload(data)

    def historical_for_season(self, season: int,
                              through: Optional[date] = None,
                              snapshot_hour_utc: int = 22) -> pd.DataFrame:
        """
        Fetch one historical snapshot per game-day for a season, at the given
        UTC hour (default 22:00 = 6pm ET, which is before most games start so
        we capture closing-line-adjacent odds for early games and opening
        odds for late games — a reasonable tradeoff at 10 requests/day).

        For tighter closing-line capture, call `historical_snapshot` directly
        per-game at commence_time - 15min, at the cost of ~10x more requests.

        season : year (2020+)
        through: if provided, stop at this date (useful for YTD seasons)

        Returns concatenated long-format odds DataFrame.
        """
        start = date(season, 3, 20)
        # MLB season stretches from late March to early October, plus playoffs.
        end = through or date(season, 10, 5)

        frames = []
        cur = start
        while cur <= end:
            snapshot_time = datetime(cur.year, cur.month, cur.day,
                                     snapshot_hour_utc, 0, 0)
            df = self.historical_snapshot(snapshot_time)
            if not df.empty:
                df["snapshot_date"] = cur.isoformat()
                frames.append(df)
            cur += timedelta(days=1)

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _flatten_odds_payload(payload) -> pd.DataFrame:
    """
    Convert the Odds API nested per-bookmaker JSON into a long-format DF:
      game_id, commence_time, home_team, away_team, book, market, outcome,
      price, point, last_update
    """
    # Historical endpoint wraps games in .data; live returns list directly.
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]

    rows = []
    for g in payload or []:
        base = {
            "game_id":        g.get("id"),
            "commence_time":  g.get("commence_time"),
            "home_team":      g.get("home_team"),
            "away_team":      g.get("away_team"),
        }
        for bk in g.get("bookmakers", []):
            book = bk.get("key")
            last_update = bk.get("last_update")
            for mk in bk.get("markets", []):
                market = mk.get("key")
                for oc in mk.get("outcomes", []):
                    rows.append({
                        **base,
                        "book":        book,
                        "last_update": last_update,
                        "market":      market,
                        "outcome":     oc.get("name"),
                        "price":       oc.get("price"),
                        "point":       oc.get("point"),
                    })
    return pd.DataFrame(rows)
