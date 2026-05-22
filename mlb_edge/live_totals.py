"""
live_totals.py
--------------
Live totals (over/under) odds for MLB.

Source-priority chain (2026-05-22):
    1. Pinnacle guest Arcadia JSON       (PRIMARY, no auth, CI-friendly)
    2. DraftKings public eventgroup JSON (BACKUP -- often 403 in CI due to
       Akamai bot detection, still works from local laptop runs)
    3. the-odds-api.com /current endpoint (LEGACY, subscription cancelled
       2026-05-21; returns empty unless ODDS_API_KEY is somehow re-set)
    4. empty DataFrame -> main_totals enters pred_runs-only mode.

Pinnacle/DK return *wide* (one-row-per-game) frames; the-odds-api returns a
*long* per-book frame.  We pivot the wide frames into long format via
_pin_wide_to_long / _dk_wide_to_long so the downstream median_totals_by_game
function doesn't have to special-case which book the line came from.
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

    Source-priority chain (2026-05-22):
        1. Pinnacle guest Arcadia JSON       (PRIMARY, no auth)
        2. DraftKings public eventgroup JSON (BACKUP -- mostly 403 in CI due
           to Akamai bot detection, but still works from local runs)
        3. the-odds-api.com /current endpoint (LEGACY, subscription cancelled
           -- will return empty unless ODDS_API_KEY is somehow re-set)
        4. empty DataFrame -> main_totals enters pred_runs-only mode.

    Pinnacle and DK both return *wide* DataFrames (one row per game with
    total_line + over/under decimals).  We pivot them to the long format the
    downstream median_totals_by_game() expects so the rest of the pipeline
    doesn't have to special-case which book it came from.

    Returns long-format DataFrame with:
      game_id, commence_time, home_team, away_team, book, outcome,
      price, point, decimal, commence_date,
      home_team_abbr, away_team_abbr
    """
    # ---- Source 1: Pinnacle (primary, no auth, CI-friendly) ----------------
    try:
        from .pinnacle_totals import fetch_pinnacle_totals
        pin_wide = fetch_pinnacle_totals()
    except Exception as e:
        log.warning("[live_totals] Pinnacle fetch crashed: %s -- falling "
                    "through to DraftKings", e)
        pin_wide = pd.DataFrame()

    if not pin_wide.empty:
        log.info("[live_totals] Pinnacle returned %d games -- using Pinnacle "
                 "as totals source", len(pin_wide))
        return _pin_wide_to_long(pin_wide)

    # ---- Source 2: DraftKings (backup, often 403 in CI) --------------------
    try:
        from .draftkings_totals import fetch_dk_totals
        dk_wide = fetch_dk_totals()
    except Exception as e:
        log.warning("[live_totals] DraftKings fetch crashed: %s -- falling "
                    "through to the-odds-api legacy chain", e)
        dk_wide = pd.DataFrame()

    if not dk_wide.empty:
        log.info("[live_totals] DraftKings returned %d games -- using DK as "
                 "totals source", len(dk_wide))
        return _dk_wide_to_long(dk_wide)

    # ---- Source 3: the-odds-api.com (legacy, cancelled 2026-05-21) ---------
    # Kept in the chain for the case where the user re-enables the
    # subscription, or some other key shows up in .env.  The empty-key path
    # below short-circuits to `return pd.DataFrame()` with a single info log
    # line so the daily cron stays quiet rather than spamming errors.
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        log.info("[live_totals] Pinnacle empty, DK empty AND ODDS_API_KEY "
                 "unset -- totals pipeline returns empty; main_totals will "
                 "enter pred_runs-only mode.")
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


def _pin_wide_to_long(pin_wide: pd.DataFrame) -> pd.DataFrame:
    """Expand Pinnacle's wide (one-row-per-game) DataFrame to the long format
    median_totals_by_game expects.  Schema-identical to _dk_wide_to_long;
    only the synthetic book name and game_id prefix change so downstream
    logging makes it obvious which source the line came from.

    Input columns:  game_date, home_team, away_team, total_line,
                    over_decimal, under_decimal   (team codes already normalized)
    """
    if pin_wide.empty:
        return pd.DataFrame()
    rows = []
    for _, r in pin_wide.iterrows():
        commence = pd.to_datetime(r["game_date"])
        base = {
            "game_id":       f"pin:{r['home_team']}:{r['away_team']}:{r['game_date']}",
            "commence_time": commence.isoformat(),
            "home_team":     r["home_team"],
            "away_team":     r["away_team"],
            "book":          "pinnacle",
            "point":         float(r["total_line"]),
        }
        rows.append({**base, "outcome": "Over",
                     "price": np.nan,
                     "decimal": float(r["over_decimal"])})
        rows.append({**base, "outcome": "Under",
                     "price": np.nan,
                     "decimal": float(r["under_decimal"])})
    df = pd.DataFrame(rows)
    df["home_team_abbr"] = df["home_team"].apply(normalize_team)
    df["away_team_abbr"] = df["away_team"].apply(normalize_team)
    df["commence_date"]  = pd.to_datetime(df["commence_time"]).dt.date
    return df.dropna(subset=["decimal", "point"])


def _dk_wide_to_long(dk_wide: pd.DataFrame) -> pd.DataFrame:
    """Expand DK's wide (one-row-per-game) DataFrame to the long format
    median_totals_by_game expects.

    Input columns:  game_date, home_team, away_team, total_line,
                    over_decimal, under_decimal   (team codes already normalized)
    Output mirrors _flatten_live_totals: each game emits two rows
    (Over + Under) under a synthetic book="draftkings".  median_totals_by_game
    then collapses to a wide consensus frame -- which for DK alone is the same
    line/decimal pair we started with, just routed through the same merge code
    path the-odds-api used.  Keeps downstream code single-track.
    """
    if dk_wide.empty:
        return pd.DataFrame()
    rows = []
    for _, r in dk_wide.iterrows():
        commence = pd.to_datetime(r["game_date"])
        base = {
            "game_id":       f"dk:{r['home_team']}:{r['away_team']}:{r['game_date']}",
            "commence_time": commence.isoformat(),
            "home_team":     r["home_team"],
            "away_team":     r["away_team"],
            "book":          "draftkings",
            "point":         float(r["total_line"]),
        }
        # Over row.  `price` is unused downstream (median_totals_by_game keys
        # off `decimal`); we set it to NaN to mark "DK didn't provide a fresh
        # American number on this synthesized row".  decimal is what counts.
        rows.append({**base, "outcome": "Over",
                     "price": np.nan,
                     "decimal": float(r["over_decimal"])})
        rows.append({**base, "outcome": "Under",
                     "price": np.nan,
                     "decimal": float(r["under_decimal"])})
    df = pd.DataFrame(rows)
    df["home_team_abbr"] = df["home_team"].apply(normalize_team)
    df["away_team_abbr"] = df["away_team"].apply(normalize_team)
    df["commence_date"]  = pd.to_datetime(df["commence_time"]).dt.date
    return df.dropna(subset=["decimal", "point"])


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

    # Vectorized American -> decimal. Mirrors odds_totals.build_totals_frame and
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
    pattern (lines -> over_dec -> under_dec) instead of pivot_table + rename.
    Same algo, produces identical values, and keeps the whole codebase using
    one idiom for per-outcome decimal aggregation.
    """
    if long_odds.empty:
        return pd.DataFrame()

    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]

    # Step 1: consensus line per (matchup, date) -- median across ALL book rows.
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
