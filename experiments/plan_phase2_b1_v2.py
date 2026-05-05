"""Phase 2 b1 fill plan v2 — exclude spring training games.

Apply opening-day cutoffs:
  - 2024 regular season: 2024-03-28
  - 2025 regular season: 2025-03-27
(Korea Series 2024-03-20/21 + Tokyo Series 2025-03-18/19 are real games but
the v12 cache includes lots of spring training rows on 03-22 .. 03-26 that
never had US sportsbook odds posted; these inflate the denominator.)

Then run the same impact-prioritized greedy fill toward 80% paired.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

CACHE = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\historical")
FC = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

CUTOFF = {2024: "2024-03-28", 2025: "2025-03-27"}

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

KOREA_2024 = {"2024-03-20", "2024-03-21"}  # LAD/SD reg-season games to keep
TOKYO_2025 = {"2025-03-18", "2025-03-19"}  # CHC/LAD


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def candidate_team_names(abbr):
    full = ABBR_TO_TEAM.get(abbr)
    if not full:
        return []
    out = [full]
    if abbr == "OAK":
        out.append("Athletics")
    elif abbr == "ATH":
        out.append("Oakland Athletics")
    return out


def is_regular_season(gd, year):
    if gd >= CUTOFF[year]:
        return True
    if year == 2024 and gd in KOREA_2024:
        return True
    if year == 2025 and gd in TOKYO_2025:
        return True
    return False


def main():
    files = sorted(CACHE.glob("*.json"))
    # Track BOTH response timestamps (for matching) AND cache file existence
    # via md5 of the requested timestamp (to avoid duplicate fetches).
    import hashlib
    existing_cache_keys = set(f.stem for f in files)  # md5[:14]
    game_snaps = defaultdict(list)
    for f in files:
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = obj.get("timestamp", "")
        if not ts:
            continue
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

    def is_cached(ts):
        return hashlib.md5(ts.encode()).hexdigest()[:14] in existing_cache_keys

    paired_count = {2024: 0, 2025: 0}
    n_total = {2024: 0, 2025: 0}
    underserved_by_date = defaultdict(list)
    for y in (2024, 2025):
        cache_df = pd.read_parquet(
            FC / f"features_{y}_full_1_v12.parquet",
            columns=["game_id", "game_date", "home_team", "away_team"],
        )
        for _, r in cache_df.iterrows():
            gd = str(r["game_date"])[:10]
            if not is_regular_season(gd, y):
                continue
            n_total[y] += 1
            home_abbr, away_abbr = r["home_team"], r["away_team"]
            cand_h = candidate_team_names(home_abbr)
            cand_a = candidate_team_names(away_abbr)
            if not cand_h or not cand_a:
                continue
            snaps = []
            for h in cand_h:
                for a in cand_a:
                    snaps += game_snaps.get((gd, h, a), [])
            snaps = list({s[0]: s for s in snaps}.values())
            h2h = [x for x in snaps if x[1]]
            paired = False
            if len(h2h) >= 2:
                tss = sorted(x[0] for x in h2h)
                try:
                    spread_h = (parse(tss[-1]) - parse(tss[0])).total_seconds() / 3600
                    if spread_h >= 3.0:
                        paired = True
                except Exception:
                    pass
            if paired:
                paired_count[y] += 1
                continue
            # Pick a target ts: prefer 14:00 UTC (different time-of-day) if 22:00 UTC already cached
            target_22 = f"{gd}T22:00:00Z"
            target_14 = f"{gd}T14:00:00Z"
            if is_cached(target_22) and not is_cached(target_14):
                target = target_14
                reason = "complement_14"
            elif not is_cached(target_22):
                target = target_22
                reason = "fetch_22"
            elif not is_cached(target_14):
                target = target_14
                reason = "fetch_14"
            else:
                # Both cached; truly unpairable from this game-day's snaps alone.
                # Try a +1day 22:00 UTC (covers late games rolling over).
                next_day = (datetime.fromisoformat(gd).date() + timedelta(days=1)).isoformat()
                target = f"{next_day}T14:00:00Z"
                reason = "fetch_next14"
                if is_cached(target):
                    continue  # nothing more to do
            underserved_by_date[gd].append({
                "year": y, "home": home_abbr, "away": away_abbr,
                "target_ts": target, "reason": reason,
            })

    print(f"Initial coverage (regular season only):")
    for y in (2024, 2025):
        print(f"  {y}: {paired_count[y]}/{n_total[y]} = {paired_count[y]/n_total[y]*100:.1f}%")

    target_pct = 0.80
    needed = {y: max(0, int(n_total[y] * target_pct) - paired_count[y]) + 1
              for y in (2024, 2025)}
    print(f"To clear 80%: 2024 +{needed[2024]}, 2025 +{needed[2025]}")

    candidates = []
    for gd, items in underserved_by_date.items():
        from collections import Counter
        by_target = defaultdict(list)
        for it in items:
            by_target[it["target_ts"]].append(it)
        for ts, group in by_target.items():
            i_2024 = sum(1 for g in group if g["year"] == 2024)
            i_2025 = sum(1 for g in group if g["year"] == 2025)
            candidates.append({
                "gd": gd, "ts": ts, "reason": group[0]["reason"],
                "impact_2024": i_2024, "impact_2025": i_2025,
                "impact_total": i_2024 + i_2025,
            })

    candidates.sort(key=lambda c: -c["impact_total"])
    print(f"\nCandidate calls (only NOT-cached): {len(candidates)}")
    if candidates:
        print(f"Top 10:")
        for c in candidates[:10]:
            print(f"  gd={c['gd']} ts={c['ts']} reason={c['reason']} "
                  f"impact_2024={c['impact_2024']} impact_2025={c['impact_2025']}")

    # Greedy
    selected = []
    cum = {2024: 0, 2025: 0}
    for c in candidates:
        if cum[2024] >= needed[2024] and cum[2025] >= needed[2025]:
            break
        selected.append(c)
        cum[2024] += c["impact_2024"]
        cum[2025] += c["impact_2025"]

    print(f"\nSelected: {len(selected)} calls = {len(selected)*10} quota")
    print(f"Projected: 2024 +{cum[2024]} -> "
          f"{(paired_count[2024]+cum[2024])/n_total[2024]*100:.1f}%; "
          f"2025 +{cum[2025]} -> "
          f"{(paired_count[2025]+cum[2025])/n_total[2025]*100:.1f}%")

    out = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\fill_plan_b1_v2.json")
    out.write_text(json.dumps({
        "initial_coverage": {
            str(y): {"paired": paired_count[y], "total": n_total[y]} for y in (2024, 2025)
        },
        "target_pct": target_pct,
        "calls": selected,
    }, indent=2))
    print(f"Plan: {out}")


if __name__ == "__main__":
    main()
