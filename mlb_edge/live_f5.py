"""
live_f5.py
----------
Live F5 moneyline odds from the-odds-api /current endpoint.

Unlike the /historical endpoint (which doesn't support h2h_1st_5_innings),
the /sports/baseball_mlb/odds endpoint DOES return F5 markets when the API
key has paid-tier access. This is the only way to get real F5 pricing for
the paper-tracker flow.

Each call costs 1 request (not 10 like historical).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional

import numpy as np
import pandas as pd
import requests

from .config import DATA
from .stadiums import normalize_team

log = logging.getLogger(__name__)


def fetch_live_f5_odds() -> pd.DataFrame:
    """
    Fetch live F5 moneyline odds for all upcoming MLB games in the next ~3 days.

    Returns long-format DataFrame with:
      game_id, commence_time, home_team, away_team, book, outcome, price, decimal

    Team names are normalized to our 3-letter abbreviations.
    Returns empty DataFrame on any failure — caller should handle gracefully.
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        log.error("ODDS_API_KEY not set")
        return pd.DataFrame()

    url = f"{DATA.odds_api_base}/sports/{DATA.odds_sport}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     DATA.odds_regions,
        "markets":     "h2h_1st_5_innings",
        "oddsFormat":  "american",
        "bookmakers":  ",".join(DATA.odds_bookmakers),
    }

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            remaining = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-used")
            if remaining is not None:
                log.info("Odds API (F5 live): %s remaining / %s used", remaining, used)
            if r.status_code == 200:
                return _flatten_live_f5(r.json())
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            log.error("Live F5 odds %s: %s", r.status_code, r.text[:300])
            return pd.DataFrame()
        except requests.RequestException as e:
            log.warning("Live F5 request failed: %s", e)
            time.sleep(2 ** attempt)
    return pd.DataFrame()


def _flatten_live_f5(payload) -> pd.DataFrame:
    """Convert the-odds-api payload to a long-format DataFrame."""
    rows = []
    for g in payload or []:
        base = {
            "game_id":       g.get("id"),
            "commence_time": g.get("commence_time"),
            "home_team":     g.get("home_team"),
            "away_team":     g.get("away_team"),
        }
        for bk in g.get("bookmakers", []):
            book = bk.get("key")
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h_1st_5_innings":
                    continue
                for oc in mk.get("outcomes", []):
                    rows.append({
                        **base,
                        "book":    book,
                        "outcome": oc.get("name"),
                        "price":   oc.get("price"),
                    })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["home_team_abbr"] = df["home_team"].apply(normalize_team)
    df["away_team_abbr"] = df["away_team"].apply(normalize_team)
    df["outcome_abbr"]   = df["outcome"].apply(normalize_team)
    df["commence_date"]  = pd.to_datetime(df["commence_time"]).dt.date

    # Sanity filter: drop NaN and sub-pick'em (|price| < 100) quotes.
    df = df[df["price"].notna() & (df["price"].abs() >= 100)].copy()

    # Vectorized American → decimal (same math as odds_f5.build_f5_odds_frame).
    p = df["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    df["decimal"] = dec
    return df


def median_f5_by_game(long_odds: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse per-book prices to a single median-decimal per (game, team).
    Returns wide format with one row per game:

        home_team (abbr), away_team (abbr), commence_date,
        home_f5_decimal, away_f5_decimal

    NOTE: output columns `home_team`/`away_team` hold the 3-letter abbreviation
    (the normalization done in `_flatten_live_f5`), so the caller's slate frame
    must also be on abbreviated team names. This matches what
    `build_pipeline.build_slate_frame` produces.

    Implementation: two merges on (home_abbr, away_abbr, date) — mirrors the
    O(N log N) pattern in `build_pipeline.merge_games_and_odds`. The old
    pivot+iterrows form was O(N²) and allocated a Python dict per row.
    """
    if long_odds.empty:
        return pd.DataFrame()

    med = (long_odds.groupby(["home_team_abbr", "away_team_abbr",
                              "commence_date", "outcome_abbr"],
                             sort=False)["decimal"]
                    .median().reset_index())
    med = med[(med["decimal"] >= 1.05) & (med["decimal"] <= 10.0)].copy()

    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]
    home_odds = (med.loc[med["outcome_abbr"] == med["home_team_abbr"],
                         keys + ["decimal"]]
                    .rename(columns={"decimal": "home_f5_decimal"}))
    away_odds = (med.loc[med["outcome_abbr"] == med["away_team_abbr"],
                         keys + ["decimal"]]
                    .rename(columns={"decimal": "away_f5_decimal"}))

    # inner merge is equivalent to outer + dropna here: a row survives only
    # if BOTH sides priced the game.
    out = home_odds.merge(away_odds, on=keys, how="inner")

    # Preserve the legacy column contract: `home_team`/`away_team` (not
    # `*_abbr`) for downstream joins in tracker_f5.run_predict, which merges
    # against the slate's home_team/away_team columns.
    return out.rename(columns={
        "home_team_abbr": "home_team",
        "away_team_abbr": "away_team",
    })[["home_team", "away_team", "commence_date",
        "home_f5_decimal", "away_f5_decimal"]]
