"""Plan the Phase 2 b1 odds-cache fill — PRIORITIZED.

For each underserved game-day, count how many CACHE games on that day would
flip from "<2 paired snaps" to ">=2 paired snaps with >=3h spread" if we
add a single 14:00-or-22:00 UTC snapshot. Sort by impact desc, take only
enough to push 2024 and 2025 each to >=80% paired coverage.

Output:
  data/odds_cache/fill_plan_b1_prioritized.json — list of {gd, ts, reason, impact_2024, impact_2025}
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


def main():
    # 1) Load existing snapshots
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

    # 2) Per cache game, classify state and bind to its game_date.
    cache_games = {2024: [], 2025: []}
    paired_count = {2024: 0, 2025: 0}
    underserved_by_date = defaultdict(list)  # gd -> [(year, home, away, existing_ts_or_none)]
    for y in (2024, 2025):
        cache_df = pd.read_parquet(
            FC / f"features_{y}_full_1_v12.parquet",
            columns=["game_id", "game_date", "home_team", "away_team"],
        )
        for _, r in cache_df.iterrows():
            gd = str(r["game_date"])[:10]
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
            cache_games[y].append({
                "gd": gd, "home": home_abbr, "away": away_abbr, "paired": paired,
                "existing_ts": [x[0] for x in h2h],
                "first_pitch": snaps[0][2] if snaps else None,
            })
            if paired:
                paired_count[y] += 1
            else:
                # Compute target ts: 14:00 UTC or 22:00 UTC, whichever isn't already in cache
                target_22 = f"{gd}T22:00:00Z"
                target_14 = f"{gd}T14:00:00Z"
                # Prefer the timestamp that creates the largest spread
                # If existing snap is at 22:00 UTC, fetch 14:00; else 22:00
                if any(t.startswith(f"{gd}T22") or t.startswith(f"{(datetime.fromisoformat(gd).date()+timedelta(days=1)).isoformat()}T22") for t in [x[0] for x in h2h]):
                    target = target_14
                    reason = "widen_spread"
                else:
                    target = target_22
                    reason = "canonical_22"
                if target in existing_ts:
                    # already cached but didn't help; try the other slot
                    target = target_14 if target == target_22 else target_22
                    reason = "alt_slot"
                underserved_by_date[gd].append({
                    "year": y, "home": home_abbr, "away": away_abbr,
                    "target_ts": target, "reason": reason,
                })

    n_2024 = len(cache_games[2024]); n_2025 = len(cache_games[2025])
    print(f"Initial coverage:")
    print(f"  2024: {paired_count[2024]}/{n_2024} = {paired_count[2024]/n_2024*100:.1f}%")
    print(f"  2025: {paired_count[2025]}/{n_2025} = {paired_count[2025]/n_2025*100:.1f}%")

    target_pct = 0.80
    games_needed_2024 = max(0, int(n_2024 * target_pct) - paired_count[2024]) + 1
    games_needed_2025 = max(0, int(n_2025 * target_pct) - paired_count[2025]) + 1
    print(f"\nTo clear 80%:")
    print(f"  2024: need {games_needed_2024} more paired")
    print(f"  2025: need {games_needed_2025} more paired")

    # 3) Per game-day, the impact of a SINGLE complementary call =
    #    number of underserved games on that day that would flip.
    # We assume: if we add ONE timestamp at 14:00 or 22:00 UTC, every
    # underserved game on that day with its commence_time within ~24h
    # flips to paired. (A snapshot at 14:00 captures all games on that day,
    # adding one more snap to all underserved games.)
    candidates = []
    for gd, items in underserved_by_date.items():
        if gd[:4] not in ("2024", "2025"):
            continue
        # All items on this gd will share the same target_ts (we picked the best slot)
        # but to be safe, group by target_ts
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

    # Sort by total impact desc
    candidates.sort(key=lambda c: -c["impact_total"])

    print(f"\nTotal candidate calls: {len(candidates)}")
    print(f"Impact distribution: max={candidates[0]['impact_total']}, "
          f"median={candidates[len(candidates)//2]['impact_total']}, "
          f"min={candidates[-1]['impact_total']}")

    # 4) Greedy: take top calls until both years cleared.
    selected = []
    cum_2024 = 0; cum_2025 = 0
    for c in candidates:
        if cum_2024 >= games_needed_2024 and cum_2025 >= games_needed_2025:
            break
        selected.append(c)
        cum_2024 += c["impact_2024"]
        cum_2025 += c["impact_2025"]

    print(f"\nSelected calls: {len(selected)}")
    print(f"Quota cost (10/call): {len(selected)*10} requests")
    print(f"Projected new pairs: 2024 +{cum_2024}  2025 +{cum_2025}")
    print(f"Projected coverage:")
    print(f"  2024: {(paired_count[2024]+cum_2024)/n_2024*100:.1f}%")
    print(f"  2025: {(paired_count[2025]+cum_2025)/n_2025*100:.1f}%")

    out = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\fill_plan_b1_prioritized.json")
    out.write_text(json.dumps({
        "initial_coverage": {
            "2024": {"paired": paired_count[2024], "total": n_2024},
            "2025": {"paired": paired_count[2025], "total": n_2025},
        },
        "target_pct": target_pct,
        "calls": selected,
    }, indent=2))
    print(f"\nPlan written: {out}")


if __name__ == "__main__":
    main()
