"""What's in the v12 cache for the early-season dates the planner flagged as
high-impact? The 18-impact-day=2024-03-23 doesn't sound like a real MLB date."""
from __future__ import annotations
import pandas as pd
from pathlib import Path

FC = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

for y in (2024, 2025):
    df = pd.read_parquet(FC / f"features_{y}_full_1_v12.parquet",
                         columns=["game_id", "game_date", "home_team", "away_team"])
    df["gd"] = df["game_date"].astype(str).str[:10]
    print(f"\n=== {y} early-season game-day distribution ===")
    early = df[df["gd"].str.startswith(f"{y}-03")]
    if len(early):
        for gd, n in early["gd"].value_counts().sort_index().items():
            sample = early[early["gd"] == gd][["away_team", "home_team"]].head(3).values.tolist()
            sample_str = ", ".join([f"{a}@{h}" for a, h in sample])
            print(f"  {gd}: {n:>3} games  ({sample_str}{', ...' if n > 3 else ''})")
