"""Build handedness-stratified park factors from raw Statcast.

For each (venue / home_team, batting hand), compute:
  - runs_index   = (avg_runs_per_pa_at_venue_for_hand) / (league_avg_runs_per_pa_for_hand) * 100
  - hr_index     = analogous for HR rate
  - woba_index   = analogous for woba

We use 2022-2024 as the training window (3 seasons, league-wide stable).
Then for each game in 2024/2025 cache, attach:
  - park_runs_factor_lhb / park_runs_factor_rhb (venue-level constants)
  - park_hr_factor_lhb / park_hr_factor_rhb
  - actual_lhb_pa_pct, actual_rhb_pa_pct (from that game's statcast PAs)
  - park_runs_factor_lhb_weighted (= lhb_pct * park_runs_factor_lhb + rhb_pct * park_runs_factor_rhb)
  - park_hr_factor_lhb_weighted
  - park_pull_air_lhb_weighted (placeholder for the wind interaction term)

Output: data/feature_cache/handedness_park_factors_<year>.parquet (per-game)
        data/feature_cache/handedness_park_factors_lookup.parquet (per-venue/hand)
"""
from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import pandas as pd
import numpy as np

CACHE = Path(r"D:\mlb_edge\mlb_edge\data\statcast_cache\statcast_chunk")
FC = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

PA_END_EVENTS = {
    "single", "double", "triple", "home_run",
    "field_out", "strikeout", "walk", "hit_by_pitch",
    "sac_fly", "sac_bunt", "field_error", "fielders_choice",
    "fielders_choice_out", "double_play", "grounded_into_double_play",
    "force_out", "intent_walk", "strikeout_double_play",
    "sac_fly_double_play", "triple_play", "catcher_interf",
}
HR_EVENTS = {"home_run"}


def load_statcast_pa(years_train, years_score):
    """Load all chunks. Returns DataFrame keyed at PA-level (one row per
    plate appearance) with [game_pk, game_date, game_year, home_team,
    stand, events, woba_value, score_change_runs (estimated)]."""
    files = sorted(CACHE.glob("*.parquet"))
    frames = []
    for f in files:
        df = pd.read_parquet(f, columns=[
            "game_pk", "game_date", "game_year", "home_team",
            "stand", "events", "woba_value",
            "post_bat_score", "bat_score",
            "post_fld_score", "fld_score",
            "inning_topbot",
        ])
        # Filter to PA-end events only
        df = df[df["events"].isin(PA_END_EVENTS)]
        df = df[df["game_year"].isin(years_train + years_score)]
        if len(df):
            frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(df):,} PA rows across {df['game_year'].nunique()} seasons")
    return df


def compute_pa_runs(df):
    """Estimate runs scored on this PA = post_bat_score - bat_score
    (from offensive perspective)."""
    runs = (df["post_bat_score"].fillna(0) - df["bat_score"].fillna(0)).clip(lower=0)
    return runs.astype(float)


def compute_handedness_factors(pa_df, train_years):
    """Per (home_team, stand) compute index_runs, index_hr, index_woba on
    train_years only."""
    train = pa_df[pa_df["game_year"].isin(train_years)].copy()
    train["pa_runs"] = compute_pa_runs(train)
    train["is_hr"] = train["events"].isin(HR_EVENTS).astype(int)

    # League average per stand
    league = train.groupby("stand").agg(
        league_runs=("pa_runs", "mean"),
        league_hr=("is_hr", "mean"),
        league_woba=("woba_value", "mean"),
    )
    print("\nLeague averages per stand (train years):")
    print(league)

    # Per-park (home_team only — we use home_team as venue proxy)
    by_park = train.groupby(["home_team", "stand"]).agg(
        n_pa=("pa_runs", "size"),
        venue_runs=("pa_runs", "mean"),
        venue_hr=("is_hr", "mean"),
        venue_woba=("woba_value", "mean"),
    ).reset_index()
    by_park = by_park.merge(league, left_on="stand", right_index=True)
    by_park["index_runs_handed"] = (by_park["venue_runs"] / by_park["league_runs"]) * 100
    by_park["index_hr_handed"] = (by_park["venue_hr"] / by_park["league_hr"]) * 100
    by_park["index_woba_handed"] = (by_park["venue_woba"] / by_park["league_woba"]) * 100
    return by_park, league


def build_per_game_features(pa_df, factors_df, score_years):
    """For each game in score_years, compute LHB-PA%, attach hand-weighted park factors."""
    score = pa_df[pa_df["game_year"].isin(score_years)].copy()
    score = score[score["stand"].isin(["L", "R"])]
    # PA mix per game — count by stand
    mix = score.groupby(["game_pk", "home_team", "game_date"])["stand"].value_counts().unstack(fill_value=0)
    mix.columns = [f"n_pa_{c}" for c in mix.columns]
    if "n_pa_L" not in mix.columns:
        mix["n_pa_L"] = 0
    if "n_pa_R" not in mix.columns:
        mix["n_pa_R"] = 0
    mix = mix.reset_index()
    mix["total_pa"] = mix["n_pa_L"] + mix["n_pa_R"]
    mix["lhb_pa_pct"] = mix["n_pa_L"] / mix["total_pa"].where(mix["total_pa"] > 0, np.nan)
    mix["rhb_pa_pct"] = 1 - mix["lhb_pa_pct"]

    # Pivot factors_df to wide
    pf_wide = factors_df.pivot_table(
        index="home_team", columns="stand",
        values=["index_runs_handed", "index_hr_handed", "index_woba_handed", "n_pa"],
    )
    pf_wide.columns = [f"{a}_{b}" for a, b in pf_wide.columns]
    pf_wide = pf_wide.reset_index()
    out = mix.merge(pf_wide, on="home_team", how="left")
    # Hand-weighted (per-game) park factors:
    out["park_runs_factor_handed"] = (
        out["lhb_pa_pct"].fillna(0.4) * out["index_runs_handed_L"].fillna(100) / 100 +
        out["rhb_pa_pct"].fillna(0.6) * out["index_runs_handed_R"].fillna(100) / 100
    )
    out["park_hr_factor_handed"] = (
        out["lhb_pa_pct"].fillna(0.4) * out["index_hr_handed_L"].fillna(100) / 100 +
        out["rhb_pa_pct"].fillna(0.6) * out["index_hr_handed_R"].fillna(100) / 100
    )
    # Spread = how much the handed factor differs from the unweighted (L+R averaged) factor.
    out["park_runs_lhb_minus_rhb"] = (
        out["index_runs_handed_L"] - out["index_runs_handed_R"]
    ).fillna(0) / 100
    out["park_hr_lhb_minus_rhb"] = (
        out["index_hr_handed_L"] - out["index_hr_handed_R"]
    ).fillna(0) / 100
    return out


def main():
    pa_df = load_statcast_pa([2022, 2023, 2024], [2024, 2025])

    factors, league = compute_handedness_factors(pa_df, [2022, 2023, 2024])
    factors_path = FC / "handedness_park_factors_lookup.parquet"
    factors.to_parquet(factors_path, index=False)
    print(f"\nWrote per-park-handed factors lookup: {factors_path}")
    # Show a few notable parks
    show = factors[factors["home_team"].isin(["NYY", "COL", "BOS", "FEN", "OAK", "CHW", "BAL"])]
    print(show.sort_values(["home_team", "stand"]).to_string(index=False))
    print("\n[Top 10 LHB-favoring parks by index_hr_handed (LHB)]")
    lhb = factors[factors["stand"] == "L"].nlargest(10, "index_hr_handed")[["home_team", "index_hr_handed", "index_runs_handed", "n_pa"]]
    print(lhb.to_string(index=False))
    print("\n[Top 10 RHB-favoring parks by index_hr_handed (RHB)]")
    rhb = factors[factors["stand"] == "R"].nlargest(10, "index_hr_handed")[["home_team", "index_hr_handed", "index_runs_handed", "n_pa"]]
    print(rhb.to_string(index=False))

    per_game = build_per_game_features(pa_df, factors, [2024, 2025])
    out_path = FC / "handedness_park_factors_per_game.parquet"
    per_game.to_parquet(out_path, index=False)
    print(f"\nWrote per-game features: {out_path}")
    print(f"  n_games_2024: {per_game[per_game['game_date'].astype(str).str.startswith('2024')].shape[0]}")
    print(f"  n_games_2025: {per_game[per_game['game_date'].astype(str).str.startswith('2025')].shape[0]}")
    print(f"\nSample features (Yankee Stadium games):")
    nyy = per_game[per_game["home_team"] == "NYY"].head(5)
    print(nyy[["game_pk", "game_date", "lhb_pa_pct",
               "park_runs_factor_handed", "park_hr_factor_handed",
               "park_hr_lhb_minus_rhb"]].to_string(index=False))


if __name__ == "__main__":
    main()
