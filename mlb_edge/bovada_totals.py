"""
bovada_totals.py
----------------
Scrape MLB game totals (over/under) from Bovada's public sports-events
"coupon" JSON endpoint.  Bovada is one of the few US-facing books whose
public web JSON is *not* protected by Akamai/PerimeterX bot-detection in
front of the GitHub Actions runner IP ranges, so unlike DraftKings it
actually responds 200 from CI.

Bovada is now the SECONDARY MLB totals source as of 2026-05-23, slotting
in between Pinnacle (primary, sharper) and DraftKings (backup, mostly 403
in CI).  Pinnacle's slate coverage is patchy in the morning -- on 2026-05-23
it only had 6 of 15 games priced when the daily-slate cron fired.  Bovada
posts lines earlier, so the merge in live_totals.fetch_live_totals_odds
fills in the games Pinnacle hasn't gotten to yet.

Endpoint (one HTTP call per fetch, returns the full slate):

    https://www.bovada.lv/services/sports/event/coupon/events/A/description/
        baseball/mlb
        ?marketFilterId=def&liveOnly=false&eventsLimit=50&lang=en

Response shape:
    [
      {
        "path": [...],
        "events": [
          {
            "id": "...",
            "description": "Cardinals @ Reds",
            "startTime": 1779556200000,             # ms epoch UTC
            "competitors": [
              {"name": "Cincinnati Reds",       "home": True},
              {"name": "St. Louis Cardinals",   "home": False},
            ],
            "displayGroups": [
              {
                "description": "Game Lines",
                "markets": [
                  {
                    "description": "Total",
                    "period": {"description": "Game", ...},
                    "outcomes": [
                      {"description": "Over",
                       "price": {"american": "-110",
                                 "decimal": "1.909091",
                                 "handicap": "9.5", ...}},
                      {"description": "Under", "price": {...}},
                    ],
                  },
                  ...
                ],
              },
            ],
          },
          ...
        ],
      }
    ]

We filter to the Game Lines / Total / period.description=="Game" market,
read `handicap` as the line and `american` as the price, and convert
American->decimal locally (mirrors pinnacle_totals._american_to_decimal so
the two scrapers stay schema-compatible).

On any failure (HTTP, JSON parse, schema mismatch) returns an empty
DataFrame and emits a WARNING log line.  Never raises.

Returned DataFrame matches the contract of pinnacle_totals.fetch_pinnacle_totals
and mlb_edge.live_totals._dk_wide_to_long input:
    game_date, home_team, away_team, total_line, over_decimal, under_decimal

`home_team` / `away_team` are 3-letter abbreviations (NYY, LAD, ...) via
mlb_edge.stadiums.normalize_team.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from .stadiums import normalize_team

log = logging.getLogger(__name__)

# Bovada public sports-events "coupon" endpoint.  Returns the full MLB slate
# in a single ~40 KB JSON payload -- markets included inline, no second call
# needed (unlike Pinnacle's matchups + markets split).
BOV_URL = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A/description/"
    "baseball/mlb"
    "?marketFilterId=def&liveOnly=false&eventsLimit=50&lang=en"
)

BOV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.bovada.lv/sports/baseball/mlb",
}

# Rate-limit sleep before the request.  We hit Bovada at most once per daily
# slate cron run; the 1s sleep is just polite-citizen behavior in case the
# scraper ever gets called in a tight loop during dev.
BOV_PRE_REQUEST_SLEEP_SEC = 1.0

# Filter horizon: only emit rows for games starting within the next 36 hours.
# Bovada returns MLB events well into the future (e.g. weekend series posted
# Monday); we want today + tomorrow only so the merge in live_totals doesn't
# pull in lines that don't match anything on the current slate.
BOV_HORIZON_HOURS = 36


def _american_to_decimal(p) -> float:
    """American odds -> decimal odds.  Returns NaN on invalid input.

    positive: decimal = 1 + odds/100
    negative: decimal = 1 + 100/abs(odds)

    Bovada quotes pick'em prices as the string "EVEN" instead of "+100";
    we map that to +100 before the arithmetic.
    """
    if isinstance(p, str):
        s = p.strip().upper()
        if s in ("EVEN", "EV", "PK", "PICK"):
            p = 100
        else:
            try:
                p = float(s)
            except ValueError:
                return np.nan
    try:
        p = float(p)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(p) or abs(p) < 100:
        return np.nan
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / abs(p))


def fetch_bovada_totals() -> pd.DataFrame:
    """Fetch MLB game totals from Bovada.

    Returns DataFrame with columns:
        game_date, home_team, away_team, total_line,
        over_decimal, under_decimal
    Filters to games starting within the next BOV_HORIZON_HOURS (36) hours.
    Empty DataFrame on any failure -- HTTP error, parse error, schema
    mismatch, or no markets -- with a WARNING log.  Never raises.
    """
    try:
        return _fetch_bovada_totals_inner()
    except Exception as e:
        log.warning("[bovada_totals] unexpected error: %s -- returning "
                    "empty", e)
        return pd.DataFrame()


def _fetch_bovada_totals_inner() -> pd.DataFrame:
    # Polite 1s pause before the request.  Bovada's edge is forgiving but we
    # don't want to look like a tight-loop scraper.
    time.sleep(BOV_PRE_REQUEST_SLEEP_SEC)

    try:
        r = requests.get(BOV_URL, headers=BOV_HEADERS, timeout=20)
    except requests.RequestException as e:
        log.warning("[bovada_totals] request failed: %s -- returning empty", e)
        return pd.DataFrame()

    if r.status_code != 200:
        log.warning("[bovada_totals] HTTP %s from Bovada: %s",
                    r.status_code, (r.text or "")[:300])
        return pd.DataFrame()

    try:
        payload = r.json()
    except ValueError as e:
        log.warning("[bovada_totals] JSON parse error: %s -- returning empty",
                    e)
        return pd.DataFrame()

    # Top-level is a list with one element whose `events` key holds the slate.
    if not isinstance(payload, list) or not payload:
        log.warning("[bovada_totals] payload not a non-empty list "
                    "(got %s) -- returning empty", type(payload).__name__)
        return pd.DataFrame()

    events = payload[0].get("events") if isinstance(payload[0], dict) else None
    if not isinstance(events, list) or not events:
        log.warning("[bovada_totals] no events[] in payload -- returning "
                    "empty")
        return pd.DataFrame()

    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=BOV_HORIZON_HOURS)

    rows = []
    for e in events:
        if not isinstance(e, dict):
            continue

        # Game time (ms epoch UTC).  Skip if missing or unparseable.
        st_ms = e.get("startTime")
        try:
            start_utc = datetime.fromtimestamp(float(st_ms) / 1000.0,
                                               tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        if start_utc > horizon:
            continue
        # Allow up to 6 hours in the past so games that have just started
        # but not yet flipped to live=True still get a stale-line snapshot.
        if start_utc < now_utc - timedelta(hours=6):
            continue

        # Team alignment from competitors[].  Bovada uses `home: true/false`.
        comps = e.get("competitors") or []
        home_raw = next((c.get("name") for c in comps
                         if c.get("home") is True), None)
        away_raw = next((c.get("name") for c in comps
                         if c.get("home") is False), None)
        if not home_raw or not away_raw:
            continue

        # Find the Game Lines / Total / Game-period market.
        total_market = None
        for dg in (e.get("displayGroups") or []):
            if (dg.get("description") or "").strip().lower() != "game lines":
                continue
            for mk in (dg.get("markets") or []):
                if (mk.get("description") or "").strip().lower() != "total":
                    continue
                per = mk.get("period") or {}
                # Bovada's Game-period total is the full-game O/U (the
                # secondary "1st 5 Innings" total lives in a different
                # marketCategory, so we don't need to filter on that).
                if (per.get("description") or "").strip().lower() != "game":
                    continue
                # Skip "main: False" alternate-line ladder entries -- only
                # the main line goes through.
                if per.get("main") is False:
                    continue
                total_market = mk
                break
            if total_market is not None:
                break

        if total_market is None:
            continue

        line = np.nan
        over_dec = np.nan
        under_dec = np.nan
        for oc in (total_market.get("outcomes") or []):
            desig = (oc.get("description") or "").strip().lower()
            price = oc.get("price") or {}
            handicap = price.get("handicap")
            try:
                pts = float(handicap) if handicap is not None else np.nan
            except (TypeError, ValueError):
                pts = np.nan
            if np.isfinite(pts):
                line = pts
            dec = _american_to_decimal(price.get("american"))
            if desig == "over":
                over_dec = dec
            elif desig == "under":
                under_dec = dec

        if not (np.isfinite(line) and np.isfinite(over_dec)
                and np.isfinite(under_dec)):
            continue

        home_abbr = normalize_team((home_raw or "").strip())
        away_abbr = normalize_team((away_raw or "").strip())
        if not home_abbr or not away_abbr:
            continue

        rows.append({
            "game_date":     start_utc.date(),
            "home_team":     home_abbr,
            "away_team":     away_abbr,
            "total_line":    line,
            "over_decimal":  over_dec,
            "under_decimal": under_dec,
        })

    if not rows:
        log.warning("[bovada_totals] no full-game totals parsed from %d "
                    "events -- returning empty", len(events))
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Kelly stake needs both sides -- drop rows missing either decimal.
    df = df.dropna(subset=["total_line", "over_decimal", "under_decimal"])
    if df.empty:
        log.warning("[bovada_totals] all rows dropped on decimal/line NaN")
        return df

    # Deduplicate on (home, away, date) in case Bovada lists a doubleheader
    # twice -- keep the first occurrence (typically game 1 of the DH).
    df = df.drop_duplicates(
        subset=["home_team", "away_team", "game_date"], keep="first"
    ).reset_index(drop=True)

    log.info("[bovada_totals] fetched %d MLB totals", len(df))
    return df
