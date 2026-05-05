"""Build line-movement features for the 2024+2025 v12 scrubbed cache.

For each cache game, find all h2h snapshots that contain it, pick the
"opening" snap (earliest with h2h) and "closing" snap (latest with h2h),
compute implied probabilities (devigged via Shin), and emit per-side
line_movement_pp = close_implied - open_implied.

Output: data/feature_cache/line_movement_<year>_v1.parquet
Columns:
  game_id, game_date, home_team, away_team,
  home_open_implied, home_close_implied,
  away_open_implied, away_close_implied,
  line_movement_home_pp, line_movement_away_pp,
  open_to_close_hours, n_h2h_snaps, n_books_open, n_books_close,
  open_to_first_pitch_hours, close_to_first_pitch_hours
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\historical")
FC = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")
OUT_DIR = FC

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


def american_to_implied(price):
    """American odds -> implied probability (vig included)."""
    p = float(price)
    if p < 0:
        return -p / (-p + 100)
    return 100 / (p + 100)


def shin_devig(p_home, p_away):
    """Shin (1992) devigging for two-outcome market.
    Returns (fair_home, fair_away). z is the insider-trade fraction.
    """
    if p_home is None or p_away is None:
        return None, None
    s = p_home + p_away
    if s <= 1.0 or s > 1.5:
        # No vig or implausible; just normalize
        return p_home / s, p_away / s
    # Shin formula
    z = ((s - 1) * (p_home**2 + p_away**2 - s)) / (s * (p_home**2 + p_away**2) - 2 * p_home * p_away)
    z = max(0, min(z, 0.5))
    a = 1 - z
    fh = (np.sqrt(z**2 + 4 * a * p_home**2 / s) - z) / (2 * a)
    fa = (np.sqrt(z**2 + 4 * a * p_away**2 / s) - z) / (2 * a)
    norm = fh + fa
    if norm > 0:
        fh, fa = fh / norm, fa / norm
    return fh, fa


def consensus_implied_at_snap(g, home_name):
    """For one game in one snapshot, return (home_fair_implied, away_fair_implied,
    n_books). Uses median across books, then Shin devig."""
    home_implies = []
    away_implies = []
    for bk in g.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            home_p = away_p = None
            for o in mk.get("outcomes", []):
                if o.get("name") == home_name:
                    home_p = american_to_implied(o.get("price"))
                else:
                    away_p = american_to_implied(o.get("price"))
            if home_p is not None and away_p is not None:
                home_implies.append(home_p)
                away_implies.append(away_p)
    if not home_implies:
        return None, None, 0
    h_med = float(np.median(home_implies))
    a_med = float(np.median(away_implies))
    fh, fa = shin_devig(h_med, a_med)
    return fh, fa, len(home_implies)


def main():
    # 1) Load all cached snapshots, indexed by (utc_date, home_full, away_full).
    files = sorted(CACHE.glob("*.json"))
    snap_index = defaultdict(list)  # key -> [(snap_ts, game_obj)]
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
            utc_dt = datetime.fromisoformat(ct[:10]).date()
            key1 = (str(utc_dt), home, away)
            key2 = (str(utc_dt - timedelta(days=1)), home, away)
            snap_index[key1].append((ts, g))
            snap_index[key2].append((ts, g))
    print(f"Loaded {len(files)} cache files, indexed by {len(snap_index)} (date, teams) keys")

    for year in (2024, 2025):
        cache_df = pd.read_parquet(
            FC / f"features_{year}_full_1_v12.parquet",
            columns=["game_id", "game_date", "home_team", "away_team"],
        )
        rows = []
        for _, r in cache_df.iterrows():
            gd = str(r["game_date"])[:10]
            home_abbr, away_abbr = r["home_team"], r["away_team"]
            cand_h = candidate_team_names(home_abbr)
            cand_a = candidate_team_names(away_abbr)
            if not cand_h or not cand_a:
                rows.append({
                    "game_id": int(r["game_id"]), "game_date": gd,
                    "home_team": home_abbr, "away_team": away_abbr,
                })
                continue
            # Collect all snaps for this game
            snaps = []  # [(snap_ts, game_obj, home_name)]
            for h in cand_h:
                for a in cand_a:
                    for ts, g in snap_index.get((gd, h, a), []):
                        snaps.append((ts, g, h))
            # Dedupe by snap_ts (keep first)
            dedup = {}
            for ts, g, h in snaps:
                if ts not in dedup:
                    dedup[ts] = (g, h)
            snaps = sorted([(ts, g, h) for ts, (g, h) in dedup.items()])
            if len(snaps) < 2:
                # No paired snapshot; emit NaN row
                rows.append({
                    "game_id": int(r["game_id"]), "game_date": gd,
                    "home_team": home_abbr, "away_team": away_abbr,
                    "n_h2h_snaps": len([s for s in snaps if any(
                        mk.get("key") == "h2h"
                        for bk in s[1].get("bookmakers", [])
                        for mk in bk.get("markets", []))]),
                })
                continue
            # Pick the pair maximizing time-spread among h2h-bearing snaps
            h2h_snaps = []
            for ts, g, h in snaps:
                fh, fa, nb = consensus_implied_at_snap(g, h)
                if fh is not None:
                    h2h_snaps.append((ts, fh, fa, nb, g.get("commence_time", "")))
            if len(h2h_snaps) < 2:
                rows.append({
                    "game_id": int(r["game_id"]), "game_date": gd,
                    "home_team": home_abbr, "away_team": away_abbr,
                    "n_h2h_snaps": len(h2h_snaps),
                })
                continue
            # Find max-spread pair
            h2h_snaps.sort(key=lambda x: x[0])
            t0_str, fh0, fa0, nb0, ct = h2h_snaps[0]
            t1_str, fh1, fa1, nb1, _ = h2h_snaps[-1]
            try:
                t0, t1 = parse(t0_str), parse(t1_str)
                ct_dt = parse(ct) if ct else None
                spread_h = (t1 - t0).total_seconds() / 3600
                open_to_fp = (ct_dt - t0).total_seconds() / 3600 if ct_dt else None
                close_to_fp = (ct_dt - t1).total_seconds() / 3600 if ct_dt else None
            except Exception:
                spread_h = open_to_fp = close_to_fp = None
            rows.append({
                "game_id": int(r["game_id"]), "game_date": gd,
                "home_team": home_abbr, "away_team": away_abbr,
                "home_open_implied": fh0, "home_close_implied": fh1,
                "away_open_implied": fa0, "away_close_implied": fa1,
                "line_movement_home_pp": (fh1 - fh0) * 100,
                "line_movement_away_pp": (fa1 - fa0) * 100,
                "open_to_close_hours": spread_h,
                "n_h2h_snaps": len(h2h_snaps),
                "n_books_open": nb0, "n_books_close": nb1,
                "open_to_first_pitch_hours": open_to_fp,
                "close_to_first_pitch_hours": close_to_fp,
            })
        out_df = pd.DataFrame(rows)
        n_paired = out_df["line_movement_home_pp"].notna().sum()
        n_total = len(out_df)
        print(f"  {year}: {n_paired}/{n_total} = {n_paired/n_total*100:.1f}% have paired open/close")
        # Distribution of line_movement_home_pp
        x = out_df["line_movement_home_pp"].dropna()
        print(f"    line_movement_home_pp: median={x.median():+.2f}pp  p25={x.quantile(.25):+.2f}  p75={x.quantile(.75):+.2f}  std={x.std():.2f}")
        out_path = OUT_DIR / f"line_movement_{year}_v1.parquet"
        out_df.to_parquet(out_path, index=False)
        print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
