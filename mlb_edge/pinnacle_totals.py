"""
pinnacle_totals.py
------------------
Scrape MLB game totals (over/under) from Pinnacle's public "guest Arcadia"
API.  This is the read-only/anonymous endpoint that Pinnacle's own marketing
site (www.pinnacle.com) calls from the browser -- no auth, no cookies, no
captcha.

Pinnacle is sharp-bettor-friendly and historically far less aggressive about
bot-blocking than DraftKings, which made DK unworkable from GitHub Actions
runners (Akamai 403 on every CI run).  Pinnacle is now the PRIMARY MLB totals
source as of 2026-05-22.

Endpoints used (only two HTTP calls per fetch, regardless of slate size):

    1. https://guest.api.arcadia.pinnacle.com/0.1/leagues/246/matchups
       ?withSpecials=false
         -- list of today's MLB matchups (sport id 3, MLB league id 246).
         Each matchup has participants[] (home/away alignment), id, startTime.

    2. https://guest.api.arcadia.pinnacle.com/0.1/leagues/246/markets/straight
         -- ALL "straight" markets for every matchup in league 246 in a single
         response (~800-1200 entries, ~600 KB).  We filter to
         type == "total" AND period == 0 AND isAlternate is False -- that's
         the main full-game over/under line.

NOTE: the task brief identified sport_id 246; that's actually the LEAGUE id
for MLB.  The sport id for Baseball is 3.  We use the league endpoint, which
filters to MLB only and skips the extra fan-out for NPB / KBO / CPBL.

`X-API-Key` header below is the public anonymous key visible in browser
devtools; it rotates rarely.  The endpoints work without it too but the API
gateway is faster with the key set.

On any failure (HTTP, JSON parse, schema mismatch) returns an empty
DataFrame and emits a WARNING log line.  Never raises.

Returned DataFrame matches the contract of
mlb_edge.live_totals._dk_wide_to_long input:
    game_date, home_team, away_team, total_line, over_decimal, under_decimal

`home_team` / `away_team` are 3-letter abbreviations (NYY, LAD, ...) via
mlb_edge.stadiums.normalize_team.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd
import requests

from .stadiums import normalize_team

log = logging.getLogger(__name__)

# Pinnacle "guest Arcadia" -- public read-only API used by the marketing site.
PIN_BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
PIN_MLB_LEAGUE_ID = 246          # MLB league id (sport id for Baseball is 3)

PIN_MATCHUPS_URL = (
    f"{PIN_BASE}/leagues/{PIN_MLB_LEAGUE_ID}/matchups?withSpecials=false"
)
PIN_MARKETS_URL = (
    f"{PIN_BASE}/leagues/{PIN_MLB_LEAGUE_ID}/markets/straight"
)

PIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.pinnacle.com/",
    "X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R",
    "X-Device-UUID": "00000000-0000-0000-0000-000000000000",
}

# Polite backoff for transient 5xx (NOT 4xx -- 4xx is permanent, we just log
# and bail).  One retry max per the task brief.
PIN_RETRY_DELAY_SEC = 2.0


def _american_to_decimal(p) -> float:
    """American odds -> decimal odds.  Returns NaN on invalid input.

    positive: decimal = 1 + odds/100
    negative: decimal = 1 + 100/abs(odds)
    """
    try:
        p = float(p)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(p) or abs(p) < 100:
        return np.nan
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / abs(p))


def _pin_get(url: str, timeout: int = 20) -> Optional[dict]:
    """GET a Pinnacle Arcadia URL.  Returns parsed JSON or None on failure.

    Retries once on 5xx with a 2s backoff (per task brief).  4xx is treated as
    permanent -- log and return None.  Connection errors get one retry too.
    """
    for attempt in range(2):  # initial + 1 retry
        try:
            r = requests.get(url, headers=PIN_HEADERS, timeout=timeout)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    log.warning("[pinnacle_totals] JSON parse error from %s: "
                                "%s", url, e)
                    return None
            if 500 <= r.status_code < 600 and attempt == 0:
                log.info("[pinnacle_totals] HTTP %s -- backing off 2s",
                         r.status_code)
                time.sleep(PIN_RETRY_DELAY_SEC)
                continue
            log.warning("[pinnacle_totals] HTTP %s from %s: %s",
                        r.status_code, url, (r.text or "")[:300])
            return None
        except requests.RequestException as e:
            if attempt == 0:
                log.info("[pinnacle_totals] request exception (%s) -- "
                         "backing off 2s", e)
                time.sleep(PIN_RETRY_DELAY_SEC)
                continue
            log.warning("[pinnacle_totals] request failed after retry: %s", e)
            return None
    return None


def fetch_pinnacle_totals() -> pd.DataFrame:
    """Fetch MLB game totals from Pinnacle.

    Returns DataFrame with columns:
        game_date, home_team, away_team, total_line,
        over_decimal, under_decimal
    Empty DataFrame on any failure -- HTTP error, parse error, schema mismatch,
    or no markets -- with a WARNING log.  Never raises.
    """
    try:
        return _fetch_pinnacle_totals_inner()
    except Exception as e:
        log.warning("[pinnacle_totals] unexpected error: %s -- returning "
                    "empty", e)
        return pd.DataFrame()


def _fetch_pinnacle_totals_inner() -> pd.DataFrame:
    # ---- Step 1: matchups (gives us teams + startTime per matchupId) -------
    matchups = _pin_get(PIN_MATCHUPS_URL)
    if not matchups:
        log.warning("[pinnacle_totals] matchups endpoint returned no data")
        return pd.DataFrame()
    if not isinstance(matchups, list):
        log.warning("[pinnacle_totals] matchups payload unexpected type: %s",
                    type(matchups).__name__)
        return pd.DataFrame()

    # Build matchup_id -> {home, away, startTime} lookup, dropping anything
    # without both home and away participants.
    matchup_meta: dict = {}
    for m in matchups:
        mid = m.get("id")
        if mid is None:
            continue
        parts = m.get("participants") or []
        home = next((p.get("name") for p in parts
                     if p.get("alignment") == "home"), None)
        away = next((p.get("name") for p in parts
                     if p.get("alignment") == "away"), None)
        if not home or not away:
            continue
        matchup_meta[mid] = {
            "home_raw":   home,
            "away_raw":   away,
            "start_time": m.get("startTime"),
        }

    if not matchup_meta:
        log.warning("[pinnacle_totals] no usable matchups parsed from payload")
        return pd.DataFrame()

    # ---- Step 2: all straight markets for the league in one shot ----------
    markets = _pin_get(PIN_MARKETS_URL)
    if not markets:
        log.warning("[pinnacle_totals] markets endpoint returned no data")
        return pd.DataFrame()
    if not isinstance(markets, list):
        log.warning("[pinnacle_totals] markets payload unexpected type: %s",
                    type(markets).__name__)
        return pd.DataFrame()

    # ---- Step 3: pull the main full-game total per matchup -----------------
    # Filter: type == "total", period == 0 (full game, not first-5),
    # isAlternate == False (the consensus line, not the alt-line ladder).
    rows = []
    for mk in markets:
        if mk.get("type") != "total":
            continue
        if mk.get("period") != 0:
            continue
        if mk.get("isAlternate", False):
            continue
        mid = mk.get("matchupId")
        meta = matchup_meta.get(mid)
        if not meta:
            continue
        prices = mk.get("prices") or []
        if len(prices) < 2:
            continue
        over_dec = np.nan
        under_dec = np.nan
        line = np.nan
        for pr in prices:
            desig = (pr.get("designation") or "").strip().lower()
            pts = pr.get("points")
            try:
                pts = float(pts) if pts is not None else np.nan
            except (TypeError, ValueError):
                pts = np.nan
            if np.isfinite(pts):
                line = pts
            dec = _american_to_decimal(pr.get("price"))
            if desig == "over":
                over_dec = dec
            elif desig == "under":
                under_dec = dec
        if not np.isfinite(line):
            continue
        home_abbr = normalize_team((meta["home_raw"] or "").strip())
        away_abbr = normalize_team((meta["away_raw"] or "").strip())
        if not home_abbr or not away_abbr:
            continue
        # Pinnacle startTime is ISO-8601 UTC like '2026-05-22T23:40:00Z'.
        # The date part is what the slate frame keys off.
        try:
            game_date = pd.to_datetime(meta["start_time"]).date()
        except Exception:
            continue
        rows.append({
            "game_date":     game_date,
            "home_team":     home_abbr,
            "away_team":     away_abbr,
            "total_line":    line,
            "over_decimal":  over_dec,
            "under_decimal": under_dec,
        })

    if not rows:
        log.warning("[pinnacle_totals] no main full-game totals matched any "
                    "matchup -- returning empty")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Kelly stake needs both sides -- drop rows missing either decimal.
    df = df.dropna(subset=["total_line", "over_decimal", "under_decimal"])
    if df.empty:
        log.warning("[pinnacle_totals] all rows dropped on decimal/line NaN")
        return df
    df = df.reset_index(drop=True)
    log.info("[pinnacle_totals] parsed %d MLB totals rows from Pinnacle",
             len(df))
    return df


# Alias for parity with mlb_edge.live_totals.fetch_live_totals_odds.
fetch_live_totals_odds = fetch_pinnacle_totals
