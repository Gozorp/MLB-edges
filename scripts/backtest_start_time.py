"""
backtest_start_time.py
----------------------
Test whether the model's hit rate varies by game start time. The user
observed that on 04-26, "early" games (start before 13:00 PDT) hit 58%
while "late" games (13:00+) hit only 33%. We need a much larger sample
to know if this is a real pattern or one-day noise.

Approach:
  1. Pull game start times for all 2023+2024+2025 game_pks via MLB
     Stats API's date-range schedule endpoint (3 calls total).
  2. Join with the historical feature cache.
  3. Score each game with the v12 main model.
  4. Bucket by start hour (local-team timezone).
  5. Compute hit rate per bucket.

If late games are genuinely worse, we'll see a clear hit-rate cliff
past some hour threshold.
"""
from __future__ import annotations
import sys, os, time
from pathlib import Path
import joblib
import pandas as pd
import requests
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from mlb_edge.model import predict as mlb_predict
from mlb_edge.stadiums import STADIUMS, normalize_team

START_TIMES_PATH = ROOT / "data" / "game_start_times.parquet"


def fetch_season_start_times(season: int) -> pd.DataFrame:
    """One MLB API call returns all games for the season with gameDate."""
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "startDate": f"{season}-03-15",
        "endDate":   f"{season}-11-15",
        "gameType":  "R",   # regular season only
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    rows = []
    for dd in r.json().get("dates", []):
        for g in dd.get("games", []):
            ateam = g["teams"]["away"]["team"]
            hteam = g["teams"]["home"]["team"]
            home_abbr = hteam.get("abbreviation") or hteam.get("teamCode", "?").upper()
            rows.append({
                "game_pk": int(g["gamePk"]),
                "start_utc": pd.Timestamp(g["gameDate"]),
                "home_abbr": home_abbr,
                "away_abbr": ateam.get("abbreviation") or ateam.get("teamCode", "?").upper(),
            })
    df = pd.DataFrame(rows)
    print(f"  season {season}: {len(df)} games")
    return df


def to_local_hour(ts_utc: pd.Timestamp, home_abbr: str) -> int:
    """Convert UTC start time to home-stadium local hour using stadium tz_offset."""
    norm = normalize_team(home_abbr)
    stadium = STADIUMS.get(norm)
    if not stadium:
        # fallback to ET
        offset = -4
    else:
        offset = stadium.get("tz_offset_dst", -5)
    return int((ts_utc + pd.Timedelta(hours=offset)).hour)


def main() -> int:
    print("Step 1: fetch start times…")
    if START_TIMES_PATH.exists():
        st = pd.read_parquet(START_TIMES_PATH)
        print(f"  using cached {START_TIMES_PATH.name} ({len(st)} games)")
    else:
        frames = []
        for season in (2023, 2024, 2025):
            frames.append(fetch_season_start_times(season))
            time.sleep(0.5)   # politeness
        st = pd.concat(frames, ignore_index=True)
        st.to_parquet(START_TIMES_PATH, index=False)
        print(f"  saved {START_TIMES_PATH.name}")

    # Compute local hour
    st["local_hour"] = st.apply(
        lambda r: to_local_hour(r["start_utc"], r["home_abbr"]), axis=1
    )

    print("\nStep 2: load historical feature cache…")
    cache_dir = ROOT / "data" / "feature_cache"
    frames = []
    for season in (2023, 2024, 2025):
        for ver in ("v12", "v11", "v10", "v9"):
            p = cache_dir / f"features_{season}_full_1_{ver}.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                frames.append(df)
                print(f"  loaded {p.name}: {len(df)} games")
                break
    train = pd.concat(frames, ignore_index=True)
    train = train.dropna(subset=["home_win"]).reset_index(drop=True)

    print(f"\nStep 3: join + score with v12 main model…")
    # Join on game_id (audit) which equals game_pk
    train = train.merge(st[["game_pk", "local_hour"]],
                        left_on="game_id", right_on="game_pk", how="left")
    n_with_time = train["local_hour"].notna().sum()
    print(f"  {n_with_time}/{len(train)} games matched to start time")

    main_models = joblib.load(ROOT / "models" / "latest.pkl")
    pred = mlb_predict(main_models["stage1"], main_models["stage2"], train)
    train["main_p"] = pred["model_prob"].values
    train["main_pick_home"] = train["main_p"] >= 0.5
    train["correct"] = train["main_pick_home"] == (train["home_win"] == 1)

    print()
    print("=" * 70)
    print("  HIT RATE BY GAME START HOUR (home-team local time)")
    print("=" * 70)
    print(f"  {'Hour':<8} {'Range':<14} {'N':>6} {'Hits':>6} {'Hit Rate':>10}")
    print("  " + "-" * 50)

    valid = train.dropna(subset=["local_hour"]).copy()
    valid["local_hour"] = valid["local_hour"].astype(int)
    for h in sorted(valid["local_hour"].unique()):
        sub = valid[valid["local_hour"] == h]
        if len(sub) < 30:
            continue
        hr = sub["correct"].mean()
        print(f"  {h:>02}:00     {h:>02}:00-{h+1:>02}:00     {len(sub):>6}  {sub['correct'].sum():>6}  {hr:>8.1%}")

    # Bucket by early/mid/late
    print()
    print("  ─" * 25)
    for label, low, high in [
        ("Day (10-13)",       10, 13),
        ("Afternoon (13-17)", 13, 17),
        ("Evening (17-20)",   17, 20),
        ("Night (20-24)",     20, 24),
    ]:
        sub = valid[(valid["local_hour"] >= low) & (valid["local_hour"] < high)]
        if len(sub):
            print(f"  {label:<22} {len(sub):>5} games   {sub['correct'].mean():>6.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
