"""Debug why cache hits exist for game-days the planner thought were underserved.

Take 2024-03-23 (impact=18 in plan). Inspect the cache file for the
`2024-03-23T22:00:00Z` request, see what timestamp the response carries,
and what games are in it. Then re-check matching against cache games.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge import data_ingestion as di

CACHE = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\historical")


def cache_path_for(ts):
    """Reproduce OddsClient._cache_path('historical', ts) hashing."""
    return CACHE / (hashlib.md5(ts.encode()).hexdigest()[:14] + ".json")


def main():
    test_ts = ["2024-03-23T22:00:00Z", "2024-03-22T22:00:00Z",
               "2024-03-31T22:00:00Z", "2025-03-21T22:00:00Z"]
    for ts in test_ts:
        path = cache_path_for(ts)
        if not path.exists():
            print(f"{ts}  → NO CACHE FILE")
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{ts}  → ERROR: {e}")
            continue
        resp_ts = obj.get("timestamp", "")
        prev_ts = obj.get("previous_timestamp", "")
        next_ts = obj.get("next_timestamp", "")
        n_games = len(obj.get("data", []))
        sample = []
        for g in obj.get("data", [])[:3]:
            home = g.get("home_team")
            away = g.get("away_team")
            ct = g.get("commence_time")
            sample.append(f"{away} @ {home} ({ct})")
        print(f"{ts}")
        print(f"  cache_path: {path.name}")
        print(f"  resp_ts:    {resp_ts}")
        print(f"  prev_ts:    {prev_ts}")
        print(f"  next_ts:    {next_ts}")
        print(f"  n_games:    {n_games}")
        for s in sample:
            print(f"    {s}")
        print()


if __name__ == "__main__":
    main()
