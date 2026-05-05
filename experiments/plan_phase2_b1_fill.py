"""Plan the Phase 2 b1 odds-cache fill.

For each underserved 2024 / 2025 game-day, identify ONE complementary
snapshot timestamp to fetch. Output the call list as JSON.

Strategy:
  - Per game-day with at least one single-snap game: fetch 14:00 UTC
    (most existing snapshots are at 22:00 UTC; 14:00 widens the spread).
  - Per game-day with at least one missing-from-cache game: fetch 22:00 UTC
    if not already in cache (the canonical pattern).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

CACHE = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\historical")
FC = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

TEAM_ABBR = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Athletics": "ATH", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}
ABBR_TO_TEAM = {v: k for k, v in TEAM_ABBR.items()}


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    files = sorted(CACHE.glob("*.json"))
    existing_ts = set()
    game_snaps = defaultdict(list)
    for f in files:
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = obj.get("timestamp", "")
        if not ts:
            continue
        existing_ts.add(ts)
        for g in obj.get("data", []):
            ct = g.get("commence_time", "")
            if not ct:
                continue
            home, away = g.get("home_team", "?"), g.get("away_team", "?")
            has_h2h = any(
                mk.get("key") == "h2h"
                for bk in g.get("bookmakers", [])
                for mk in bk.get("markets", [])
            )
            utc_dt = datetime.fromisoformat(ct[:10]).date()
            game_snaps[(str(utc_dt), home, away)].append((ts, has_h2h, ct))
            game_snaps[(str(utc_dt - timedelta(days=1)), home, away)].append(
                (ts, has_h2h, ct)
            )

    underserved_dates = set()
    for y in (2024, 2025):
        cache_df = pd.read_parquet(
            FC / f"features_{y}_full_1_v12.parquet",
            columns=["game_id", "game_date", "home_team", "away_team"],
        )
        for _, r in cache_df.iterrows():
            gd = str(r["game_date"])[:10]
            home_abbr, away_abbr = r["home_team"], r["away_team"]
            home_full, away_full = ABBR_TO_TEAM.get(home_abbr), ABBR_TO_TEAM.get(away_abbr)
            if not home_full or not away_full:
                continue
            cand_h = [home_full]
            if home_abbr == "OAK":
                cand_h.append("Athletics")
            elif home_abbr == "ATH":
                cand_h.append("Oakland Athletics")
            cand_a = [away_full]
            if away_abbr == "OAK":
                cand_a.append("Athletics")
            elif away_abbr == "ATH":
                cand_a.append("Oakland Athletics")
            snaps = []
            for h in cand_h:
                for a in cand_a:
                    snaps += game_snaps.get((gd, h, a), [])
            snaps = list({s[0]: s for s in snaps}.values())
            h2h = [x for x in snaps if x[1]]
            if len(h2h) >= 2:
                tss = sorted(x[0] for x in h2h)
                try:
                    spread_h = (parse(tss[-1]) - parse(tss[0])).total_seconds() / 3600
                    if spread_h >= 3.0:
                        continue  # already paired
                except Exception:
                    pass
            underserved_dates.add(gd)

    plan = []
    for gd in sorted(underserved_dates):
        if gd[:4] not in ("2024", "2025"):
            continue
        target_22 = f"{gd}T22:00:00Z"
        target_14 = f"{gd}T14:00:00Z"
        if target_22 in existing_ts:
            plan.append({"gd": gd, "ts": target_14, "reason": "complement_to_22"})
        else:
            plan.append({"gd": gd, "ts": target_22, "reason": "canonical_22"})

    out = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\fill_plan_b1.json")
    out.write_text(json.dumps(plan, indent=2))
    print(f"underserved game-days (2024+2025): {len(underserved_dates)}")
    print(f"calls planned: {len(plan)}")
    print(f"quota cost (10/call): {len(plan)*10} requests")
    print(f"plan written: {out}")
    # Show breakdown by reason
    from collections import Counter
    breakdown = Counter(p["reason"] for p in plan)
    for r, n in breakdown.items():
        print(f"  {r}: {n}")


if __name__ == "__main__":
    main()
