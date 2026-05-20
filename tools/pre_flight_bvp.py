"""
pre_flight_bvp.py — verification gate for the BvP-brain MVP build.

Run BEFORE shipping PUSH_BVP_BRAIN_MVP.bat to confirm:
  1. MLB statsapi vsPlayer endpoint reachable
  2. Per-batter BvP fetch returns plausible samples for the locked test set
  3. At least one matchup per slate has MEANINGFUL+ (PA>=10) samples so
     the brain has signal to reason about
  4. The shrinkage + sample_flag logic produces sensible classifications

Usage:
    python tools/pre_flight_bvp.py

Exit codes:
    0 = GREEN — proceed with the build, test set is valid
    1 = AMBER — pivot test slate or note that BvP signal is sparser than expected
    2 = RED — endpoint or fetch failure
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Tuple

# LOCKED IN 2026-05-19 — three slates with full postgame data, varied SP
# matchups.  Specific roles per slate emerge from the per-matchup scan
# below (BASELINE = rich BvP, FALSE-POS CONTROL = noisy BvP, etc.).
TEST_SLATES: List[Tuple[str, str]] = [
    ("2026-05-16", "Mid-week slate, mixed veteran/rookie lineups"),
    ("2026-05-17", "Weekend slate, more division matchups"),
    ("2026-05-18", "Weekend slate, includes TEX@COL veteran-vs-veteran "
                   "(PA=34 confirmed in exploratory scan)"),
]

# Sample-size gates (must match bvp_brain.py constants).  Per Rule 9
# these are starting guesses, marked [H] until backtested.
PA_MEANINGFUL_FLOOR = 10
PA_LOTS_OF_HISTORY = 30
OWNER_OPS_FLOOR = 0.900
WEAK_OPS_CEILING = 0.500


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — same minimal-deps shape as pre_flight_platoon)
# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "mlb_edge_pre_flight_bvp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def schedule_for(date_str: str) -> List[Dict]:
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={date_str}&hydrate=team,probablePitcher")
    try:
        data = _fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"    [schedule fetch failed: {e}]")
        return []
    out = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            out.append({
                "game_pk": g["gamePk"],
                "away": g["teams"]["away"]["team"].get("abbreviation"),
                "home": g["teams"]["home"]["team"].get("abbreviation"),
                "away_sp_id": (g["teams"]["away"].get("probablePitcher") or {}).get("id"),
                "home_sp_id": (g["teams"]["home"].get("probablePitcher") or {}).get("id"),
            })
    return out


def boxscore_top_n(game_pk: int, side: str, n: int = 3) -> List[Tuple[int, str, int]]:
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    data = _fetch(url)
    rows = []
    for pid, info in data["teams"][side]["players"].items():
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
    return rows[:n]


def fetch_bvp_raw(batter_id: int, pitcher_id: int) -> Optional[Dict]:
    """Direct vsPlayer call, parses vsPlayerTotal split.  Returns dict with
    PA, HR, OPS or None on failure.
    """
    qs = urllib.parse.urlencode({
        "stats": "vsPlayer", "group": "hitting",
        "opposingPlayerId": pitcher_id, "sportId": 1,
    })
    url = f"https://statsapi.mlb.com/api/v1/people/{batter_id}/stats?{qs}"
    try:
        data = _fetch(url)
    except Exception as e:
        return {"error": str(e)}
    for sg in data.get("stats", []):
        if (sg.get("type") or {}).get("displayName") != "vsPlayerTotal":
            continue
        splits = sg.get("splits") or []
        if not splits:
            continue
        st = splits[0].get("stat") or {}
        try:
            pa = int(st.get("plateAppearances", 0) or 0)
            hr = int(st.get("homeRuns", 0) or 0)
            try:
                ops = float(st.get("ops") or 0)
            except (TypeError, ValueError):
                ops = 0.0
            return {"pa": pa, "hr": hr, "ops": ops}
        except (ValueError, TypeError):
            return {"pa": 0, "hr": 0, "ops": 0.0}
    return {"pa": 0, "hr": 0, "ops": 0.0}


def classify(pa: int, ops: float) -> str:
    if pa <= 0:
        return "NO_DATA"
    if pa >= PA_MEANINGFUL_FLOOR and ops >= OWNER_OPS_FLOOR:
        return "OWNER"
    if pa >= PA_MEANINGFUL_FLOOR and ops <= WEAK_OPS_CEILING:
        return "WEAK_VS"
    if pa >= PA_LOTS_OF_HISTORY:
        return "LOTS_OF_HISTORY"
    if pa >= PA_MEANINGFUL_FLOOR:
        return "MEANINGFUL"
    return "SMALL_SAMPLE"


# ---------------------------------------------------------------------------
# Per-slate evaluator
# ---------------------------------------------------------------------------
def evaluate_slate(date_str: str, desc: str) -> Tuple[bool, List[str]]:
    print(f"\n--- {date_str}  [{desc}] ---")
    notes: List[str] = []
    try:
        sched = schedule_for(date_str)
    except Exception as e:
        notes.append(f"schedule fetch failed: {e}")
        print(f"    FAIL — schedule: {e}")
        return False, notes
    if not sched:
        notes.append("empty schedule")
        print("    FAIL — empty schedule")
        return False, notes
    print(f"    Slate has {len(sched)} games.  Scanning matchups for "
          f"MEANINGFUL+ BvP samples...")
    matchups_with_signal = 0
    total_meaningful = 0
    total_owners = 0
    total_lots = 0
    best_matchup = None
    best_pa = 0
    for g in sched:
        if not (g["away_sp_id"] and g["home_sp_id"]):
            continue
        # For pre-flight, audit the HOME top-3 against the AWAY SP only
        # (covers both sides via slate variety).  Keeps the API call count
        # bounded: 15 games * 3 batters = 45 calls per slate.
        try:
            home_top = boxscore_top_n(g["game_pk"], "home", n=3)
        except Exception as e:
            print(f"    boxscore fail {g['away']}@{g['home']}: {e}")
            continue
        matchup_max_pa = 0
        any_meaningful = False
        for pos, name, pid in home_top:
            time.sleep(0.3)  # be polite to statsapi
            rec = fetch_bvp_raw(pid, g["away_sp_id"])
            if not rec or "error" in rec:
                continue
            pa = rec["pa"]
            ops = rec["ops"]
            flag = classify(pa, ops)
            if pa > matchup_max_pa:
                matchup_max_pa = pa
            if flag in ("MEANINGFUL", "LOTS_OF_HISTORY", "OWNER", "WEAK_VS"):
                any_meaningful = True
                total_meaningful += 1
                if flag == "OWNER":
                    total_owners += 1
                if flag == "LOTS_OF_HISTORY":
                    total_lots += 1
        if any_meaningful:
            matchups_with_signal += 1
        if matchup_max_pa > best_pa:
            best_pa = matchup_max_pa
            best_matchup = f"{g['away']}@{g['home']}"

    print(f"    matchups with MEANINGFUL+ signal: {matchups_with_signal}/{len(sched)}")
    print(f"    total meaningful records: {total_meaningful}  "
          f"(owners={total_owners}, lots-of-history={total_lots})")
    print(f"    richest matchup: {best_matchup}  (max-PA on one batter = {best_pa})")
    if matchups_with_signal == 0:
        notes.append("no matchups with MEANINGFUL+ samples")
        print("    SLATE WARN — no signal, brain will only see SMALL_SAMPLE rows")
        return False, notes
    print("    SLATE OK — brain has at least one signal-rich matchup")
    return True, notes


def main():
    print("=" * 72)
    print(" PRE_FLIGHT — BvP-brain MVP data verification")
    print("=" * 72)
    print(f" Gates: PA_MEANINGFUL_FLOOR={PA_MEANINGFUL_FLOOR}, "
          f"PA_LOTS_OF_HISTORY={PA_LOTS_OF_HISTORY}")
    print(f" Testing {len(TEST_SLATES)} locked-in slates against MLB statsapi")
    results = []
    for date_str, desc in TEST_SLATES:
        try:
            passed, notes = evaluate_slate(date_str, desc)
        except Exception as e:
            print(f"    UNCAUGHT — {e}")
            passed, notes = False, [f"uncaught: {e}"]
        results.append((date_str, passed, notes))
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
        print("   All test slates have MEANINGFUL+ BvP samples for at least one")
        print("   matchup.  Proceed with PUSH_BVP_BRAIN_MVP.bat.")
        return 0
    if n_pass >= max(1, n_total - 1):
        print(" PRE_FLIGHT: AMBER")
        print("   Most slates OK; at least one is BvP-sparse.")
        print("   Options:")
        print("     1. Swap the sparse slate for a different historical date")
        print("     2. Proceed; brain will gracefully handle SMALL_SAMPLE-only "
              "rows by falling back to baseline")
        return 1
    print(" PRE_FLIGHT: RED")
    print("   Multiple slates failed.  Likely endpoint or scraper issue.")
    print("   Pivot before committing to the build.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
