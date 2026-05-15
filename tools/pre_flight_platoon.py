"""
pre_flight_platoon.py — verification gate for the platoon-brain MVP build.

Run BEFORE shipping PUSH_TOP_5_BATTER_CONTEXT.bat to confirm:
  1. MLB statsapi platoon split endpoints are reachable
  2. Career splits exist for the actual top-3 batters in our test slates
  3. Sample sizes are large enough for the splits to be predictive (not noise)
  4. The test set isn't compromised by undersampled players

Usage:
    python tools/pre_flight_platoon.py

Exit codes:
    0 = GREEN — proceed with the build, test set is valid
    1 = AMBER — pivot test slate or add prompt instruction to discount samples
    2 = RED — endpoint or fetch failure, need to use a different data source
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import List, Tuple

# LOCKED IN 2026-05-14 — these three slates passed PRE_FLIGHT and were
# chosen for the platoon-brain MVP dry-run.  Roles:
#   1. NYY @ MIL — BASELINE: top-3 has 5000+ PA samples (Judge, Goldschmidt,
#      etc.), tests whether Claude reads + cites the JSON correctly
#   2. ATL @ LAD — FALSE-POSITIVE CONTROL: LAD top-3 has mild LHP weakness
#      but ATL threw RHP, so splits FAVOR LAD.  If Claude flips this call,
#      that's destabilization (splits aren't relevant here)
#   3. CHC @ TEX — STRONGER FALSE-POSITIVE CONTROL: CHC top-3 has favorable
#      splits vs Leiter (RHP), loss was Leiter outperforming expectation.
#      Splits should NOT save this pick — tests whether Claude correctly
#      recognizes splits aren't a deciding factor here
TEST_SLATES: List[Tuple[str, str, str, str, str]] = [
    ("2026-05-09", "NYY", "MIL", "away",
     "BASELINE — does Claude read+cite the JSON? NYY top-3 5000+ PA samples"),
    ("2026-05-10", "ATL", "LAD", "home",
     "FALSE-POS CONTROL — LAD vs RHP, splits favor LAD; Claude should not flip"),
    ("2026-05-09", "CHC", "TEX", "away",
     "FALSE-POS CONTROL — CHC favorable splits vs Leiter; loss was variance"),
]

# Exploration set used during the initial 2026-05-14 validation.  Kept as
# a commented reference so future sweeps can re-enable any of these
# without re-discovering them.  Re-enable by uncommenting + assigning
# to TEST_SLATES above.
# EXPLORATION_SLATES = [
#     ("2026-05-08", "SEA", "CHW", "away", "structural — no clear mismatch"),
#     ("2026-05-12", "DET", "NYM", "away", "DET top-3 vs Peralta — McGonigle 47 PA"),
#     ("2026-05-12", "MIA", "MIN", "away", "MIA top-3 vs Ober — Hicks 95 PA"),
#     ("2026-05-13", "ARI", "TEX", "away", "ARI top-3 — Marte reverse split"),
# ]

MIN_USEFUL_PA = 100
STABLE_PA = 150


def _fetch(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "mlb_edge_pre_flight/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def find_game_pk(date_str: str, away: str, home: str):
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={date_str}&hydrate=team")
    try:
        data = _fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"    [statsapi schedule fetch failed: {e}]")
        return None
    aliases = {
        "CWS": "CHW", "CHW": "CWS",
        "AZ": "ARI", "ARI": "AZ",
        "ATH": "OAK", "OAK": "ATH",
        "WSH": "WAS", "WAS": "WSH",
    }
    def normalize(x):
        return {x, aliases.get(x, x)}
    for day in data.get("dates", []):
        for g in day.get("games", []):
            a = g["teams"]["away"]["team"].get("abbreviation", "")
            h = g["teams"]["home"]["team"].get("abbreviation", "")
            if a in normalize(away) and h in normalize(home):
                return g["gamePk"]
    return None


def get_actual_top_n(game_pk, side, n=3):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    data = _fetch(url)
    team = data["teams"][side]
    rows = []
    for pid, info in team["players"].items():
        order = info.get("battingOrder")
        if not order:
            continue
        try:
            pos = int(order) // 100
        except (ValueError, TypeError):
            continue
        if 1 <= pos <= n:
            rows.append((pos, info["person"]["fullName"], info["person"]["id"]))
    rows.sort()
    return rows


def get_career_splits(player_id):
    # Correct endpoint is `careerStatSplits` (not `career`).
    # `stats=career` returns the aggregate row without sitCodes filtering;
    # `careerStatSplits` is the one that respects vl/vr filters.
    url = (f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
           f"?stats=careerStatSplits&group=hitting&sitCodes=vl,vr")
    data = _fetch(url)
    out = {"vs_LHP": {}, "vs_RHP": {}}
    for s in data.get("stats", []):
        for split in s.get("splits", []):
            code = (split.get("split") or {}).get("code", "")
            stat = split.get("stat", {})
            try:
                pa = int(stat.get("plateAppearances", 0) or 0)
            except (TypeError, ValueError):
                pa = 0
            try:
                ops = float(stat.get("ops") or 0)
            except (TypeError, ValueError):
                ops = 0.0
            try:
                avg = float(stat.get("avg") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            entry = {"PA": pa, "OPS": ops, "AVG": avg}
            if code == "vl":
                out["vs_LHP"] = entry
            elif code == "vr":
                out["vs_RHP"] = entry
    return out


def evaluate_slate(date_str, away, home, side, desc):
    print(f"\n--- {date_str}  {away} @ {home}  [audit {side} top-3] ---")
    print(f"    {desc}")
    notes = []
    game_pk = find_game_pk(date_str, away, home)
    if not game_pk:
        notes.append("game_pk lookup failed")
        print("    FAIL — couldn't resolve game_pk")
        return False, notes
    print(f"    game_pk = {game_pk}")
    try:
        top3 = get_actual_top_n(game_pk, side, n=3)
    except Exception as e:
        notes.append(f"boxscore fetch failed: {e}")
        print(f"    FAIL — boxscore: {e}")
        return False, notes
    if len(top3) < 3:
        notes.append(f"only {len(top3)} batters in top order")
        print(f"    FAIL — only {len(top3)} batters in top-3 slots")
        return False, notes
    all_clean = True
    for pos, name, pid in top3:
        try:
            splits = get_career_splits(pid)
        except Exception as e:
            print(f"    #{pos} {name}: splits fetch failed ({e})")
            notes.append(f"{name}: splits fetch failed")
            all_clean = False
            continue
        vs_l = splits["vs_LHP"]
        vs_r = splits["vs_RHP"]
        pa_l, pa_r = vs_l.get("PA", 0), vs_r.get("PA", 0)
        ops_l, ops_r = vs_l.get("OPS", 0.0), vs_r.get("OPS", 0.0)
        flags = []
        if pa_l < MIN_USEFUL_PA: flags.append(f"LOW_LHP_PA={pa_l}")
        if pa_r < MIN_USEFUL_PA: flags.append(f"LOW_RHP_PA={pa_r}")
        if MIN_USEFUL_PA <= pa_l < STABLE_PA: flags.append(f"SUB_STABLE_LHP={pa_l}")
        if MIN_USEFUL_PA <= pa_r < STABLE_PA: flags.append(f"SUB_STABLE_RHP={pa_r}")
        spread = abs(ops_l - ops_r)
        spread_note = ("BIG_SPLIT" if spread >= 0.150
                       else "MOD_SPLIT" if spread >= 0.050
                       else "FLAT_SPLIT")
        status = "OK" if not any("LOW_" in f for f in flags) else "WARN"
        flag_str = " ".join(flags) if flags else "OK"
        print(f"    #{pos} {name:<24} {status}  "
              f"vs LHP OPS={ops_l:.3f} ({pa_l:>4} PA) | "
              f"vs RHP OPS={ops_r:.3f} ({pa_r:>4} PA) | "
              f"d={spread:.3f} {spread_note}  [{flag_str}]")
        if any("LOW_" in f for f in flags):
            all_clean = False
            notes.append(f"{name}: sub-{MIN_USEFUL_PA} PA")
    if all_clean:
        print("    SLATE OK — top-3 splits meet sample-size gates")
    else:
        print(f"    SLATE WARN — at least one batter below {MIN_USEFUL_PA} PA")
    return all_clean, notes


def main():
    print("=" * 72)
    print(" PRE_FLIGHT — platoon-brain MVP data verification")
    print("=" * 72)
    print(f" Gates: MIN_USEFUL_PA={MIN_USEFUL_PA}, STABLE_PA={STABLE_PA}")
    print(f" Testing {len(TEST_SLATES)} historical slates against MLB statsapi")
    results = []
    for date_str, away, home, side, desc in TEST_SLATES:
        try:
            passed, notes = evaluate_slate(date_str, away, home, side, desc)
        except Exception as e:
            print(f"    UNCAUGHT — {e}")
            passed, notes = False, [f"uncaught: {e}"]
        results.append((f"{date_str} {away}@{home}", passed, notes))
    print()
    print("=" * 72)
    print(" SUMMARY")
    print("=" * 72)
    for tag, passed, notes in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {tag}")
        for n in notes:
            print(f"          - {n}")
    n_pass = sum(1 for _, p, _ in results if p)
    n_total = len(results)
    print()
    if n_pass == n_total:
        print(" PRE_FLIGHT: GREEN")
        print("   All test slates have stable career splits for the actual")
        print("   batted top-3.  Proceed with PUSH_TOP_5_BATTER_CONTEXT.bat.")
        return 0
    elif n_pass >= max(1, n_total - 1):
        print(" PRE_FLIGHT: AMBER")
        print("   Most slates OK; at least one has sample-size issues.")
        print("   Options:")
        print("     1. Drop the failing slate from the test set")
        print("     2. Pre-commit prompt instruction to discount low-PA splits")
        print("     3. Use the FALLBACK slate as primary structural test")
        return 1
    else:
        print(" PRE_FLIGHT: RED")
        print("   Multiple slates failed.  Likely endpoint or scraper issue.")
        print("   Pivot before committing to the 4-day build.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
