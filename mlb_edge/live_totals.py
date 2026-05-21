"""
live_totals.py
--------------
Live totals (over/under) odds from the-odds-api /current endpoint.

The totals market IS supported on the Starter tier current endpoint, unlike
h2h_1st_5_innings. Each call costs 1 request.
"""
from __future__ import annotations

import logging
import os
import time

import numpy as np
import pandas as pd
import requests

from .config import DATA
from .stadiums import normalize_team

log = logging.getLogger(__name__)


def _american_to_decimal(p: float) -> float:
    """Scalar fallback; the hot path inside _flatten_live_totals is vectorized."""
    if pd.isna(p) or abs(p) < 100:
        return np.nan
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / (-p))


def fetch_live_totals_odds() -> pd.DataFrame:
    """
    Fetch live totals (over/under) odds for upcoming MLB games.

    Returns long-format DataFrame with:
      game_id, commence_time, home_team, away_team, book, outcome,
      price, point, decimal, commence_date,
      home_team_abbr, away_team_abbr

    NOTE (2026-05-21): the Odds API subscription was cancelled by the user.
    Kalshi (the new moneyline primary; see mlb_edge/kalshi_odds.py) does
    NOT offer MLB totals contracts — its KXMLBGAME series is binary
    game-winner only.  When ODDS_API_KEY is unset, this function returns
    an empty DataFrame and emits a loud ODDS_API_KEY_MISSING log line so
    the totals cron's dead-state is legible.  A follow-up change to
    main_totals.py will convert the existing `if raw.empty: return`
    early-exit into a graceful-degrade path that still emits pred_runs
    in the picks_totals CSV (with blank fair_prob / edge_pp / EV columns).
    Until that lands, an empty result here causes main_totals to skip
    writing the CSV for that slate.
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        log.error("[live_totals] ODDS_API_KEY_MISSING — totals pipeline "
                  "cannot fetch O/U lines.  Kalshi (the new moneyline "
                  "primary) does NOT carry totals; main_totals will skip "
                  "writing today's picks_totals CSV.  See file header for "
                  "the planned graceful-degrade follow-up.")
        return pd.DataFrame()

    url = f"{DATA.odds_api_base}/sports/{DATA.odds_sport}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     DATA.odds_regions,
        "markets":     "totals",
        "oddsFormat":  "american",
        "bookmakers":  ",".join(DATA.odds_bookmakers),
    }

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            remaining = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-used")
            if remaining is not None:
                log.info("Odds API (totals live): %s remaining / %s used",
                         remaining, used)
            if r.status_code == 200:
                return _flatten_live_totals(r.json())
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            log.error("Live totals %s: %s", r.status_code, r.text[:300])
            return pd.DataFrame()
        except requests.RequestException as e:
            log.warning("Live totals request failed: %s", e)
            time.sleep(2 ** attempt)
    return pd.DataFrame()


def _flatten_live_totals(payload) -> pd.DataFrame:
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
                if mk.get("key") != "totals":
                    continue
                for oc in mk.get("outcomes", []):
                    rows.append({
                        **base,
                        "book":    book,
                        "outcome": oc.get("name"),   # "Over" or "Under"
                        "price":   oc.get("price"),
                        "point":   oc.get("point"),  # the line, e.g. 8.5
                    })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["home_team_abbr"] = df["home_team"].apply(normalize_team)
    df["away_team_abbr"] = df["away_team"].apply(normalize_team)
    df["commence_date"]  = pd.to_datetime(df["commence_time"]).dt.date
    df = df[df["price"].notna() & (df["price"].abs() >= 100)].copy()

    # Vectorized American → decimal. Mirrors odds_totals.build_totals_frame and
    # odds_f5.build_f5_odds_frame: positive prices map to 1 + p/100, negative
    # to 1 + 100/|p|. NaN stays NaN.
    p = df["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    df["decimal"] = dec

    return df.dropna(subset=["decimal", "point"])


def median_totals_by_game(long_odds: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse per-book prices to a single consensus line + median Over/Under
    decimals per game. Matches the backtest's merge_games_and_totals logic.

    Returns wide-format DataFrame with columns:
      home_team, away_team, commence_date,
      total_line, over_decimal, under_decimal

    Implementation mirrors `odds_totals.merge_games_and_totals`: three-merge
    pattern (lines → over_dec → under_dec) instead of pivot_table + rename.
    Same algo, produces identical values, and keeps the whole codebase using
    one idiom for per-outcome decimal aggregation.
    """
    if long_odds.empty:
        return pd.DataFrame()

    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]

    # Step 1: consensus line per (matchup, date) — median across ALL book rows.
    lines = (long_odds.groupby(keys, sort=False)["point"]
                       .median().reset_index()
                       .rename(columns={"point": "total_line"}))

    # Step 2: keep only book-rows posting within 0.25 runs of the consensus.
    tt = long_odds.merge(lines, on=keys, how="inner")
    tt = tt[(tt["point"] - tt["total_line"]).abs() <= 0.25].copy()

    # Step 3: median Over and Under decimal at the consensus line.
    med = (tt.groupby(keys + ["outcome"], sort=False)["decimal"]
             .median().reset_index())
    med = med[(med["decimal"] >= 1.05) & (med["decimal"] <= 10.0)].copy()

    over_dec = (med.loc[med["outcome"] == "Over", keys + ["decimal"]]
                   .rename(columns={"decimal": "over_decimal"}))
    under_dec = (med.loc[med["outcome"] == "Under", keys + ["decimal"]]
                    .rename(columns={"decimal": "under_decimal"}))

    wide = (lines
            .merge(over_dec, on=keys, how="left")
            .merge(under_dec, on=keys, how="left"))

    # Caller consumes team names as abbreviations via `home_team`/`away_team`;
    # rename to drop the `_abbr` suffix so the downstream merge key is short.
    out = wide.rename(columns={
        "home_team_abbr": "home_team",
        "away_team_abbr": "away_team",
    })
    return out[["home_team", "away_team", "commence_date",
                "total_line", "over_decimal", "under_decimal"]].dropna()
