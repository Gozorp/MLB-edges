"""
grid_search_filter.py
---------------------
Grid-search the edge filter (MIN_EDGE_PCT, MAX_EDGE_PCT, MIN_FAIR_PROB)
against the 2023, 2024, 2025 walk-forward predictions of the CURRENT
lineup-aware model (cache v5). Output: WR, ROI, n_bets for each combo.

Currently deployed filter:
  MIN_EDGE_PCT = 0.05   MAX_EDGE_PCT = 0.07   MIN_FAIR_PROB = 0.45
(v8/v8.1 tuning note: these were set against the pre-cache-rebuild v6
data; rerunning on the v5-cache retrain may show the optimum has moved.)

Pipeline:
  1. For each season, build historical frame + odds (cache hit).
  2. Walk-forward predict with the current retrained Stage 1/Stage 2.
  3. Compute per-game side/decimal/fair/edge on the CONSIDER universe
     (valid odds only) — regardless of current filter.
  4. For each (min_edge, max_edge, min_fair) combo, simulate flat-stake
     betting and report WR/ROI/n, broken out by season.

Flat-stake is used here (not Kelly + conviction) so the grid results
isolate the filter's effect. Tier-conviction and Kelly sizing only
matter downstream once we've chosen the filter.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from mlb_edge import backtesting as btm
from mlb_edge import build_pipeline as bp
from mlb_edge.market_analysis import shin_vec


SEASONS = [2023, 2024, 2025]
PREDS_OUT = "grid_search_preds.parquet"
RESULTS_OUT = "grid_search_results.csv"


def build_preds(season: int) -> pd.DataFrame:
    """Walk-forward predictions with odds merged. Returns per-game frame
    with the fields grid-search needs."""
    print(f"\n[{season}] building historical frame + odds...")
    games = bp.build_historical_frame(season)
    odds  = bp.build_odds_frame(season)
    games_with_odds = bp.merge_games_and_odds(games, odds)
    games_with_odds = games_with_odds[games_with_odds["home_decimal"].notna()]
    games_with_odds = games_with_odds.dropna(subset=["home_win", "home_f5_win"])
    print(f"[{season}]   {len(games_with_odds)} games with odds + labels")

    print(f"[{season}] running walk-forward (5 folds)...")
    preds = btm.fit_and_predict_walk_forward(games_with_odds, n_splits=5)
    if preds.empty:
        return pd.DataFrame()

    # Shin devig to fair probabilities
    home_dec = preds["home_decimal"].to_numpy(dtype=float)
    away_dec = preds["away_decimal"].to_numpy(dtype=float)
    fair_home, _ = shin_vec(1.0 / home_dec, 1.0 / away_dec)

    out = pd.DataFrame({
        "game_id":   preds["game_id"],
        "game_date": preds["game_date"],
        "season":    season,
        "model_prob": preds["model_prob"].to_numpy(dtype=float),
        "home_dec":  home_dec,
        "away_dec":  away_dec,
        "fair_home": fair_home,
        "home_win":  preds["home_win"].astype(int).to_numpy(),
    })
    # Side-of-bet columns
    is_home = out["model_prob"] >= 0.5
    out["side"]        = np.where(is_home, "home", "away")
    out["side_prob"]   = np.where(is_home, out["model_prob"], 1 - out["model_prob"])
    out["side_dec"]    = np.where(is_home, out["home_dec"],  out["away_dec"])
    out["side_fair"]   = np.where(is_home, out["fair_home"], 1 - out["fair_home"])
    out["edge_pp"]     = out["side_prob"] - out["side_fair"]
    out["side_won"]    = np.where(is_home, out["home_win"], 1 - out["home_win"]).astype(int)
    return out


def simulate_flat(preds: pd.DataFrame, min_edge: float, max_edge: float,
                  min_fair: float) -> dict:
    m = ((preds["edge_pp"] >= min_edge)
         & (preds["edge_pp"] <= max_edge)
         & (preds["side_fair"] >= min_fair)
         & preds["side_fair"].notna())
    slice_ = preds[m]
    n = len(slice_)
    if n == 0:
        return {"n": 0, "wr": np.nan, "roi": np.nan}
    # Flat $1 per bet, payout = (decimal - 1) on win, -1 on loss
    won = slice_["side_won"].to_numpy(dtype=int)
    dec = slice_["side_dec"].to_numpy(dtype=float)
    pnl = np.where(won == 1, dec - 1.0, -1.0)
    return {
        "n":    int(n),
        "wr":   float(won.mean()),
        "roi":  float(pnl.sum() / n),
        "pnl":  float(pnl.sum()),
    }


def main():
    # -----------------------------------------------------------------
    # 1. Generate / cache per-season per-game prediction frames
    # -----------------------------------------------------------------
    if Path(PREDS_OUT).exists():
        print(f"Loading preds from cache {PREDS_OUT}...")
        preds = pd.read_parquet(PREDS_OUT)
    else:
        frames = []
        for s in SEASONS:
            df = build_preds(s)
            if not df.empty:
                frames.append(df)
        if not frames:
            print("ERROR: no predictions generated")
            return
        preds = pd.concat(frames, ignore_index=True)
        preds.to_parquet(PREDS_OUT)
        print(f"\nSaved {len(preds)} per-game predictions to {PREDS_OUT}")

    print(f"\nTotal games with preds: {len(preds)}")
    print(f"  by season: {preds.groupby('season').size().to_dict()}")

    # -----------------------------------------------------------------
    # 2. Grid search — sweep (min_edge, max_edge, min_fair)
    # -----------------------------------------------------------------
    print("\n" + "=" * 78)
    print("GRID SEARCH (flat-stake simulator)")
    print("=" * 78)

    min_edges = [0.02, 0.03, 0.04, 0.05, 0.06]
    max_edges = [0.07, 0.10, 0.15, 0.25]
    min_fairs = [0.35, 0.40, 0.45, 0.50]

    rows = []
    for mi in min_edges:
        for mx in max_edges:
            if mx <= mi: continue
            for mf in min_fairs:
                r = simulate_flat(preds, mi, mx, mf)
                r.update({"min_edge": mi, "max_edge": mx, "min_fair": mf})
                # Per-season breakdown
                for s in SEASONS:
                    sr = simulate_flat(preds[preds["season"] == s], mi, mx, mf)
                    r[f"n_{s}"]   = sr["n"]
                    r[f"wr_{s}"]  = sr.get("wr", np.nan)
                    r[f"roi_{s}"] = sr.get("roi", np.nan)
                rows.append(r)

    res = pd.DataFrame(rows)
    res = res.sort_values("roi", ascending=False)

    # -----------------------------------------------------------------
    # 3. Report
    # -----------------------------------------------------------------
    print(f"\nTop 15 by pooled ROI (n>=40 to reject flukes):")
    good = res[res["n"] >= 40].head(15)
    cols = ["min_edge", "max_edge", "min_fair", "n", "wr", "roi",
            "n_2023", "roi_2023", "n_2024", "roi_2024", "n_2025", "roi_2025"]
    print(good[cols].to_string(
        index=False,
        formatters={"wr":"{:.3f}".format, "roi":"{:+.4f}".format,
                    "roi_2023":"{:+.4f}".format, "roi_2024":"{:+.4f}".format,
                    "roi_2025":"{:+.4f}".format}))

    # Current filter baseline for comparison
    print("\n" + "=" * 78)
    print("CURRENT FILTER (MIN_EDGE=0.05, MAX_EDGE=0.07, MIN_FAIR=0.45):")
    print("=" * 78)
    cur = res[(res["min_edge"] == 0.05) & (res["max_edge"] == 0.07)
              & (res["min_fair"] == 0.45)]
    if not cur.empty:
        print(cur[cols].to_string(
            index=False,
            formatters={"wr":"{:.3f}".format, "roi":"{:+.4f}".format,
                        "roi_2023":"{:+.4f}".format, "roi_2024":"{:+.4f}".format,
                        "roi_2025":"{:+.4f}".format}))

    res.to_csv(RESULTS_OUT, index=False)
    print(f"\nFull grid saved to {RESULTS_OUT}")


if __name__ == "__main__":
    main()
