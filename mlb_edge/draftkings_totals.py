"""
draftkings_totals.py
--------------------
Scrape MLB game totals (over/under) from DraftKings' public eventgroup JSON
endpoint.

THIS API IS NOT OFFICIALLY DOCUMENTED FOR PUBLIC USE.  DK could change the
URL/schema/IDs at any time.  Whenever the structure changes:
  - this module returns an EMPTY DataFrame
  - logs a loud warning naming what failed (HTTP, JSON parse, schema)
  - the totals predict pipeline falls through to pred_runs-only mode
    (see main_totals.py graceful-degrade path, 2026-05-21).

The endpoint pattern is:

    https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups
        /{EVENT_GROUP_ID}/categories/{CATEGORY_ID}/subcategories/{SUBCATEGORY_ID}

Where:
    EVENT_GROUP_ID = 84240   # MLB
    CATEGORY_ID    = 492     # "Game" (props vs game-line)
    SUBCATEGORY_ID = 9525    # "Total Runs" (game totals)

These IDs occasionally rotate.  If the call 404s, try DK's web UI in a browser
and inspect the network tab to find the current trio, then update the
constants below.

Public/no-auth: GET only, ~150KB response, runs once per day from
mlb_edge.live_totals.fetch_live_totals_odds().  We add a 1s polite delay
between retries but don't pace beyond that since this is one call per day.

Returned DataFrame matches the contract of mlb_edge.live_totals.median_totals_by_game:
    game_date, home_team, away_team, total_line, over_decimal, under_decimal

`home_team` / `away_team` are 3-letter abbreviations (NYY, LAD, ...) per
mlb_edge.stadiums.normalize_team.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd
import requests

from .stadiums import normalize_team, TEAM_ALIASES

log = logging.getLogger(__name__)

# DK eventgroup IDs for MLB game totals.  See module docstring for rotation
# guidance if these stop returning 200.
DK_EVENT_GROUP_ID = 84240        # MLB
DK_CATEGORY_ID    = 492          # Game
DK_SUBCATEGORY_ID = 9525         # Total Runs

DK_BASE_URL = (
    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups"
    f"/{DK_EVENT_GROUP_ID}/categories/{DK_CATEGORY_ID}"
    f"/subcategories/{DK_SUBCATEGORY_ID}"
)

# DK sits behind Akamai; bare requests UA gets 403.  Use a desktop Chrome UA.
DK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
}

# Polite delay between retry attempts.  One call per day means we never come
# close to rate limits; the delay is just for transient 429/5xx backoff.
DK_RETRY_DELAY_SEC = 1.0


def _american_to_decimal(p) -> float:
    """American → decimal odds.  Returns NaN on invalid input."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(p) or abs(p) < 100:
        return np.nan
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / abs(p))


def _normalize_dk_team(name: str) -> str:
    """DK team-name string → canonical 3-letter code via stadiums.TEAM_ALIASES.

    DK uses full team names ("New York Yankees", "Athletics", etc.) — the same
    spellings the-odds-api used, so the existing TEAM_ALIASES map covers most
    cases.  Anything that falls through is passed to normalize_team() which
    returns the original string unchanged (so it shows up as a no-match in the
    downstream merge and gets dropped — explicit, debuggable).
    """
    if not name:
        return ""
    return normalize_team(name.strip())


def fetch_dk_totals(timeout: int = 20, max_retries: int = 3) -> pd.DataFrame:
    """Fetch MLB game totals from DraftKings.

    Returns long-format DataFrame matching live_totals.median_totals_by_game's
    output:
        game_date, home_team, away_team, total_line, over_decimal, under_decimal

    On any failure (HTTP, JSON parse, schema mismatch) returns an empty
    DataFrame and emits a WARNING log line so the totals cron's dead-state is
    legible.  Never raises.
    """
    try:
        payload = _fetch_dk_payload(timeout=timeout, max_retries=max_retries)
    except Exception as e:
        log.warning("[draftkings_totals] fetch failed: %s — returning empty",
                    e)
        return pd.DataFrame()

    if not payload:
        return pd.DataFrame()

    try:
        return _parse_dk_payload(payload)
    except Exception as e:
        log.warning("[draftkings_totals] parse failed (DK schema may have "
                    "changed?): %s — returning empty", e)
        return pd.DataFrame()


def _fetch_dk_payload(timeout: int = 20,
                      max_retries: int = 3) -> Optional[dict]:
    """HTTP GET the DK eventgroup JSON.  Returns dict or None on failure."""
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(DK_BASE_URL, headers=DK_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                log.info("[draftkings_totals] HTTP %s (attempt %d) — backing "
                         "off", r.status_code, attempt + 1)
                time.sleep(DK_RETRY_DELAY_SEC * (attempt + 1))
                continue
            log.warning("[draftkings_totals] HTTP %s: %s",
                        r.status_code, (r.text or "")[:300])
            return None
        except requests.RequestException as e:
            last_err = e
            time.sleep(DK_RETRY_DELAY_SEC * (attempt + 1))
    if last_err:
        log.warning("[draftkings_totals] all retries failed: %s", last_err)
    return None


def _parse_dk_payload(payload: dict) -> pd.DataFrame:
    """Transform the DK eventgroup JSON into a totals DataFrame.

    DK's schema (as of 2026-05):

        {
          "eventGroup": {
            "offerCategories": [
              { "offerSubcategoryDescriptors": [
                  { "offerSubcategory": {
                      "offers": [
                        [ { "outcomes": [
                              {"label": "Over",  "oddsAmerican": "-110", "line": 8.5},
                              {"label": "Under", "oddsAmerican":  "-110", "line": 8.5}
                            ], "eventId": 12345 }
                        ]
                      ]
                  }}
              ]}
            ],
            "events": [
              { "eventId": 12345,
                "name": "Cubs @ Cardinals",
                "startDate": "2026-05-22T23:45:00.0000000Z",
                "teamName1": "Chicago Cubs",     # AWAY
                "teamName2": "St. Louis Cardinals"  # HOME
              }
            ]
          }
        }

    `teamName1` is AWAY and `teamName2` is HOME in DK's convention
    (matches the displayed "Away @ Home" name).
    """
    event_group = payload.get("eventGroup") or {}
    events = event_group.get("events") or []
    if not events:
        log.info("[draftkings_totals] no events in payload")
        return pd.DataFrame()

    # Build event-id → meta lookup.
    event_meta = {}
    for ev in events:
        eid = ev.get("eventId")
        if eid is None:
            continue
        event_meta[eid] = {
            "start_date":  ev.get("startDate"),
            "team_name_1": ev.get("teamName1"),  # away in DK convention
            "team_name_2": ev.get("teamName2"),  # home
            "name":        ev.get("name"),
        }

    # Walk to the totals subcategory's offers.
    offers_by_event = {}
    for cat in event_group.get("offerCategories") or []:
        for sub_desc in cat.get("offerSubcategoryDescriptors") or []:
            sub = sub_desc.get("offerSubcategory") or {}
            for offer_group in sub.get("offers") or []:
                # `offer_group` is a list of offer dicts (DK groups multiple
                # alt-lines into one event but the primary line is offer[0]).
                if not isinstance(offer_group, list):
                    continue
                for offer in offer_group:
                    eid = offer.get("eventId")
                    if eid is None:
                        continue
                    outcomes = offer.get("outcomes") or []
                    if len(outcomes) < 2:
                        continue
                    # Prefer the first offer we see for each event — DK puts
                    # the main game-line offer ahead of alt-line offers.
                    if eid in offers_by_event:
                        continue
                    offers_by_event[eid] = outcomes

    if not offers_by_event:
        log.info("[draftkings_totals] no totals offers parsed from payload")
        return pd.DataFrame()

    rows = []
    for eid, outcomes in offers_by_event.items():
        meta = event_meta.get(eid)
        if not meta:
            continue
        over_dec = np.nan
        under_dec = np.nan
        line = np.nan
        for oc in outcomes:
            label = (oc.get("label") or "").strip().lower()
            am = oc.get("oddsAmerican")
            # `line` may be on outcome or on parent — DK puts it on outcome.
            ln = oc.get("line")
            try:
                ln = float(ln) if ln is not None else np.nan
            except (TypeError, ValueError):
                ln = np.nan
            if np.isfinite(ln):
                line = ln
            dec = _american_to_decimal(am)
            if label == "over":
                over_dec = dec
            elif label == "under":
                under_dec = dec
        if not np.isfinite(line):
            continue
        away_abbr = _normalize_dk_team(meta.get("team_name_1") or "")
        home_abbr = _normalize_dk_team(meta.get("team_name_2") or "")
        if not away_abbr or not home_abbr:
            continue
        start = meta.get("start_date")
        # DK uses ISO-8601 UTC; the date part is sufficient for matching the
        # slate frame (which is also keyed by UTC date).
        try:
            game_date = pd.to_datetime(start).date()
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
        log.info("[draftkings_totals] all rows filtered (team-name or "
                 "line/decimal NaN)")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Drop rows missing either decimal — Kelly stake needs both sides.
    df = df.dropna(subset=["total_line", "over_decimal", "under_decimal"])
    log.info("[draftkings_totals] parsed %d MLB totals rows from DK", len(df))
    return df.reset_index(drop=True)


# Alias for parity with mlb_edge.live_totals.fetch_live_totals_odds — same
# return contract, just sourced from DK instead of the-odds-api.
fetch_live_totals_odds = fetch_dk_totals
