"""
odds_totals.py
--------------
Extract totals (over/under run) market from the-odds-api cached snapshots.

The-odds-api returns both h2h and totals markets in the same API call (since
our config requests `markets=h2h,totals`), but build_pipeline.build_odds_frame
only keeps h2h. This module reads the same cached snapshots and extracts the
totals side — zero new API cost.

Totals payload shape per game:
  market: "totals"
  outcomes:
    - name: "Over", price: <american>, point: <total, e.g. 8.5>
    - name: "Under", price: <american>, point: <same total>

We aggregate across books to a median per-game total line + median Over and
Under prices. Same defensive filtering (|price| >= 100, decimal in [1.05, 10])
as the h2h flow.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from . import data_ingestion as di
from .stadiums import normalize_team

log = logging.getLogger(__name__)


def _american_to_decimal(p: float) -> float:
    """Scalar fallback; the hot path in build_totals_frame is vectorized."""
    if pd.isna(p) or abs(p) < 100:
        return np.nan
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / (-p))


def build_totals_frame(season: int,
                       through: Optional[date] = None,
                       snapshot_hour_utc: int = 22) -> pd.DataFrame:
    """
    Extract totals-market rows from the same cached historical snapshots that
    h2h uses. Returns long format with columns:
        home_team_abbr, away_team_abbr, commence_date,
        book, outcome (Over/Under), price (American), point (line), decimal
    """
    client = di.OddsClient()
    raw = client.historical_for_season(season, through=through,
                                       snapshot_hour_utc=snapshot_hour_utc)
    if raw.empty:
        log.error("No cached historical data for season %d", season)
        return pd.DataFrame()

    # Filter to totals market only. The raw DataFrame already contains all
    # markets the API returned — we just pick the totals rows.
    totals = raw[raw["market"] == "totals"].copy()
    if totals.empty:
        log.warning("No totals rows in cached data for season %d "
                    "(config may not have requested them)", season)
        return pd.DataFrame()

    totals["home_team_abbr"] = totals["home_team"].apply(normalize_team)
    totals["away_team_abbr"] = totals["away_team"].apply(normalize_team)
    totals["commence_date"] = pd.to_datetime(totals["commence_time"]).dt.date

    # Sanity filter on American prices for totals markets (almost always -110ish)
    n_before = len(totals)
    totals = totals[totals["price"].notna() & (totals["price"].abs() >= 100)].copy()
    dropped = n_before - len(totals)
    if dropped:
        log.warning("Dropped %d totals rows with corrupt prices", dropped)

    # Vectorized American → decimal. Mirrors odds_f5.build_f5_odds_frame and
    # build_pipeline._american_to_decimal_vec: positive prices map to
    # 1 + p/100, negative to 1 + 100/|p|. NaN stays NaN.
    p = totals["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    totals["decimal"] = dec

    # outcome = "Over" or "Under" (don't pass through normalize_team — those
    # are generic strings, not team names)
    return totals[[
        "home_team_abbr", "away_team_abbr", "commence_date",
        "book", "outcome", "price", "point", "decimal",
    ]].dropna(subset=["decimal", "point"])


def merge_games_and_totals(games: pd.DataFrame,
                           totals: pd.DataFrame) -> pd.DataFrame:
    """
    Attach median total line + median Over/Under prices to each game.

    Methodology:
      1. Within each (home, away, date), compute the MEDIAN `point` across all
         book-outcome rows. This is the "consensus line" (typically 8.5 or 9.0).
      2. Keep only rows where the book posted that exact consensus line (or
         within 0.25 runs of it — books sometimes differ by half a run).
      3. Within those rows, compute median Over decimal and median Under
         decimal separately.
      4. Attach (total_line, over_decimal, under_decimal) to each game.

    Implementation mirrors `odds_f5.merge_games_and_f5_odds`: we stay in
    decimal space end-to-end (decimal is monotonic on [1, ∞) so median-across-
    books is well-defined, unlike American prices which are discontinuous at
    pick'em), and we resolve Over/Under via two merges on the match key
    instead of a pivot + apply(axis=1) lookup — the old approach was O(N·M)
    per game.
    """
    if games.empty or totals.empty:
        return games

    g = games.copy()
    g["game_date_only"] = pd.to_datetime(g["game_date"]).dt.date

    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]

    # Step 1: consensus line per (matchup, date) — median across ALL book rows.
    lines = (totals.groupby(keys, sort=False)["point"]
                    .median().reset_index()
                    .rename(columns={"point": "total_line"}))

    # Step 2: keep only book-rows posting within 0.25 runs of the consensus.
    tt = totals.merge(lines, on=keys, how="inner")
    tt = tt[(tt["point"] - tt["total_line"]).abs() <= 0.25].copy()

    # Step 3: median Over and Under decimal at the consensus line.
    med = (tt.groupby(keys + ["outcome"], sort=False)["decimal"]
             .median().reset_index())
    med = med[(med["decimal"] >= 1.05) & (med["decimal"] <= 10.0)].copy()

    over_dec = (med.loc[med["outcome"] == "Over", keys + ["decimal"]]
                   .rename(columns={"decimal": "over_decimal"}))
    under_dec = (med.loc[med["outcome"] == "Under", keys + ["decimal"]]
                    .rename(columns={"decimal": "under_decimal"}))

    odds_wide = (lines
                 .merge(over_dec, on=keys, how="left")
                 .merge(under_dec, on=keys, how="left"))

    # Step 4: single join onto games.
    g = g.merge(
        odds_wide,
        left_on=["home_team", "away_team", "game_date_only"],
        right_on=keys,
        how="left",
    )
    drop_cols = [c for c in keys + ["game_date_only"] if c in g.columns]
    return g.drop(columns=drop_cols)
