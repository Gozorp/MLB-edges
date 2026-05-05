"""
odds_f5.py
----------
Fetch F5 (first-5-innings) moneyline odds from the-odds-api.

The-odds-api returns F5 markets under different market keys:
  - h2h_1st_5_innings : F5 moneyline (who leads after 5)
  - totals_1st_5_innings : F5 total runs (over/under X)

We only need h2h_1st_5_innings for this pivot.

Caching: F5 odds are cached to a SEPARATE directory from full-game odds so
they don't overwrite each other. Once fetched, never re-requested.
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

import numpy as np
import pandas as pd
import requests

from .config import DATA
from .stadiums import normalize_team

log = logging.getLogger(__name__)

F5_CACHE_DIR = Path("./data/odds_cache/f5_historical")
F5_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Single-snapshot fetcher
# ---------------------------------------------------------------------------
def _cache_path(ts_iso: str) -> Path:
    return F5_CACHE_DIR / (hashlib.md5(ts_iso.encode()).hexdigest()[:14] + ".json")


def fetch_f5_snapshot(api_key: str, ts: datetime) -> Optional[Dict]:
    """Fetch a single historical F5-odds snapshot at UTC timestamp `ts`."""
    iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = _cache_path(iso)
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            log.warning("Corrupt F5 cache at %s; refetching", cache)

    url = f"{DATA.odds_api_base}/historical/sports/{DATA.odds_sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": DATA.odds_regions,
        "markets": "h2h_1st_5_innings",
        "oddsFormat": "american",
        "date": iso,
    }

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            remaining = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-used")
            if remaining is not None:
                log.info("Odds API (F5): %s remaining / %s used", remaining, used)
            if r.status_code == 200:
                data = r.json()
                try:
                    cache.write_text(json.dumps(data))
                except Exception as e:
                    log.warning("F5 cache write failed: %s", e)
                return data
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            log.error("F5 odds %s → %s: %s", iso, r.status_code, r.text[:300])
            return None
        except requests.RequestException as e:
            log.warning("F5 odds request failed (%s); retrying", e)
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Season-level orchestration
# ---------------------------------------------------------------------------
def fetch_f5_for_season(season: int,
                        through: Optional[date] = None,
                        snapshot_hour_utc: int = 22) -> pd.DataFrame:
    """
    Fetch one F5 historical snapshot per day of `season`.

    Returns a flat DataFrame with columns:
      game_id, commence_time, home_team, away_team, book, market, outcome,
      price, last_update
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        log.error("ODDS_API_KEY not set")
        return pd.DataFrame()

    start = date(season, 3, 20)
    end = through or date(season, 10, 5)

    frames = []
    cur = start
    while cur <= end:
        ts = datetime(cur.year, cur.month, cur.day, snapshot_hour_utc, 0, 0)
        payload = fetch_f5_snapshot(api_key, ts)
        if payload:
            df = _flatten_f5_payload(payload)
            if not df.empty:
                df["snapshot_date"] = cur.isoformat()
                frames.append(df)
        cur += timedelta(days=1)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _flatten_f5_payload(payload) -> pd.DataFrame:
    """Same shape as _flatten_odds_payload in data_ingestion but for F5 only."""
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
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
            last_update = bk.get("last_update")
            for mk in bk.get("markets", []):
                market = mk.get("key")
                # The-odds-api returns h2h_1st_5_innings when we ask for that market
                if market != "h2h_1st_5_innings":
                    continue
                for oc in mk.get("outcomes", []):
                    rows.append({
                        **base,
                        "book":        book,
                        "last_update": last_update,
                        "market":      market,
                        "outcome":     oc.get("name"),
                        "price":       oc.get("price"),
                    })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Build F5 odds frame for backtesting
# ---------------------------------------------------------------------------
def build_f5_odds_frame(season: int,
                        through: Optional[date] = None) -> pd.DataFrame:
    """
    Pull historical F5 odds for a season and return a cleaned frame.

    Returns columns:
      home_team_abbr, away_team_abbr, commence_date, outcome_abbr, price, decimal
    """
    raw = fetch_f5_for_season(season, through=through)
    if raw.empty:
        return raw

    raw["home_team_abbr"] = raw["home_team"].apply(normalize_team)
    raw["away_team_abbr"] = raw["away_team"].apply(normalize_team)
    raw["commence_date"] = pd.to_datetime(raw["commence_time"]).dt.date

    n_before = len(raw)
    raw = raw[raw["price"].notna() & (raw["price"].abs() >= 100)].copy()
    dropped = n_before - len(raw)
    if dropped:
        log.warning("Dropped %d F5 odds rows with corrupt American prices", dropped)

    raw["outcome_abbr"] = raw["outcome"].apply(normalize_team)

    # Vectorized American → decimal. Mirrors build_pipeline._american_to_decimal_vec:
    # positive prices map to 1 + p/100, negative to 1 + 100/|p|. NaN stays NaN.
    p = raw["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    raw["decimal"] = dec
    return raw


def merge_games_and_f5_odds(games: pd.DataFrame,
                            f5_odds: pd.DataFrame) -> pd.DataFrame:
    """
    Attach home/away F5 decimal odds (median across books) to each game row.

    Mirrors `build_pipeline.merge_games_and_odds`: we stay in decimal space
    end-to-end (decimal is monotonic on [1, ∞) so median-across-books is
    well-defined, unlike American prices which are discontinuous at pick'em),
    and we resolve home/away via two merges on the match key instead of a
    pivot+apply(axis=1) lookup — the old approach was O(N·M) per game.
    """
    if games.empty or f5_odds.empty:
        return games

    g = games.copy()
    g["game_date_only"] = pd.to_datetime(g["game_date"]).dt.date

    f5 = f5_odds.dropna(subset=["decimal"])

    # One median per (matchup, date, outcome). Keep long form — we split by
    # outcome and merge, no pivot needed.
    med = (f5.groupby(["home_team_abbr", "away_team_abbr",
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
    odds_wide = home_odds.merge(away_odds, on=keys, how="outer")

    g = g.merge(
        odds_wide,
        left_on=["home_team", "away_team", "game_date_only"],
        right_on=keys,
        how="left",
    )
    drop_cols = [c for c in keys + ["game_date_only"] if c in g.columns]
    return g.drop(columns=drop_cols)
