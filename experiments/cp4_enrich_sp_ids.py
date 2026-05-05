"""CP4 prep — enrich 2024 + 2025 feature caches with home_sp_id /
away_sp_id by hitting the MLB Stats API schedule endpoint with
hydrate=probablePitcher, then writing a (game_id → SP IDs) lookup.

Output: data/pitch_quality/sp_ids_2024.parquet, sp_ids_2025.parquet
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def fetch_month(year: int, month: int) -> list[dict]:
    """One schedule call for a calendar month."""
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1,
                             "startDate": start.isoformat(),
                             "endDate": end.isoformat(),
                             "hydrate": "probablePitcher"},
                     timeout=30)
    r.raise_for_status()
    rows = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_pp = home.get("probablePitcher") or {}
            away_pp = away.get("probablePitcher") or {}
            rows.append({
                "game_id": int(g["gamePk"]),
                "game_date": g.get("gameDate", "")[:10],
                "home_sp_id": home_pp.get("id"),
                "away_sp_id": away_pp.get("id"),
                "home_sp_name": home_pp.get("fullName"),
                "away_sp_name": away_pp.get("fullName"),
            })
    return rows


def main():
    out_dir = Path("data/pitch_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    for year in (2024, 2025):
        all_rows = []
        # Months 3-10 cover spring training + regular season + playoffs
        for month in range(3, 11):
            print(f"  fetching {year}-{month:02d}...")
            try:
                rows = fetch_month(year, month)
            except Exception as e:
                print(f"    FAIL: {e}")
                continue
            all_rows.extend(rows)
            time.sleep(0.5)   # politeness
        df = pd.DataFrame(all_rows).drop_duplicates("game_id")
        out = out_dir / f"sp_ids_{year}.parquet"
        df.to_parquet(out, index=False)
        n_with_both = ((df["home_sp_id"].notna()) & (df["away_sp_id"].notna())).sum()
        print(f"  {year}: wrote {len(df)} games to {out}, "
              f"{n_with_both} with both SP IDs known")


if __name__ == "__main__":
    main()
