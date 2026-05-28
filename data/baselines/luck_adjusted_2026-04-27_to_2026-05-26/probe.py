"""Luck-adjusted self-correction probe.

Tests whether model losses where the picked team won the game-level
xwOBA battle (Bad Beat / variance) regress to better subsequent
5-game win rates than losses where the picked team got out-xwOBA'd
(Bad Read / flawed logic).

LOCKED THRESHOLDS (see memory project_luck_adjusted_probe_thresholds):
  X = +0.025  (xwOBA noise gate)
  Y = +8pp    (KEEP-mute criterion)
  KILL = -3pp (do NOT mute, possibly amplify)
  WINDOW = 5 games subsequent
  RE-PROBE = 2026-06-26 at +10pp

Run modes:
  python probe.py --dry-run
    Print raw 5-game window for one Bad Beat + one Bad Read.
    Manual sanity-check the schedule-API window lookup.

  python probe.py
    Full aggregation. Writes summary.json + picks_with_xwoba.csv
    to the baseline folder.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

# ---- locked spec ----
X_XWOBA_GATE = 0.025
Y_KEEP_DELTA_PP = 8.0
KILL_DELTA_PP = -3.0
WINDOW_N = 5

USER_AGENT = "Mozilla/5.0 mlb_edge-luck-probe"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
RATE_LIMIT_SECONDS = 0.5

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PICKS_GLOB = os.path.join(REPO_ROOT, "docs", "data", "picks_*_diag.csv")
XWOBA_LOG = os.path.join(REPO_ROOT, "data", "postgame", "game_xwoba_log.csv")
OUT_DIR = os.path.dirname(__file__)

# MLB Stats API teamId by 3-letter abbrev (matches diag CSV convention).
TEAM_ID = {
    "ARI": 109, "AZ": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CHW": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117,
    "KC":  118, "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158, "MIN": 142,
    "NYM": 121, "NYY": 147, "OAK": 133, "PHI": 143, "PIT": 134, "SD": 135,
    "SEA": 136, "SF": 137, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141,
    "WSH": 120,
}
# Reverse for printing — pick the canonical diag-CSV abbrev for each id.
ID_TO_ABBR = {v: k for k, v in TEAM_ID.items() if k != "ARI"}  # prefer AZ over ARI


def parse_matchup(s: str) -> tuple[str, str, int]:
    """Diag CSV 'matchup' looks like 'HOU @ TEX' or 'NYY @ KC (G2 of 3)'.

    Returns (away_abbr, home_abbr, game_number_if_dh_else_1).
    """
    s = s.strip()
    # Strip "(G2 of 3)" trailing suffix — that's series indicator, not DH.
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    if " @ " not in s:
        return "", "", 1
    aw, hm = s.split(" @ ", 1)
    return aw.strip(), hm.strip(), 1


def load_picks_archive() -> list[dict]:
    """Load all picks_*_diag.csv rows from the bake folder.

    Filters to rows where the model actually made a directional pick
    (skips TBD / PENDING_SP_DATA / NO_PICK rows).
    """
    rows = []
    for path in sorted(glob.glob(PICKS_GLOB)):
        date_str = os.path.basename(path).replace("picks_", "").replace("_diag.csv", "")
        with open(path) as f:
            r = csv.DictReader(f)
            for row in r:
                pick = (row.get("pick") or "").strip().upper()
                if pick in ("", "TBD", "PENDING_SP_DATA", "NONE", "NAN"):
                    continue
                aw, hm, _ = parse_matchup(row.get("matchup", ""))
                if not aw or not hm:
                    continue
                rows.append({
                    "date": date_str,
                    "away": aw,
                    "home": hm,
                    "pick": pick,
                    "matchup": row.get("matchup", ""),
                    "tier": (row.get("tier") or "").strip().upper(),
                    "pick_prob": row.get("pick_prob", ""),
                })
    return rows


def load_xwoba_log() -> dict:
    """Index xwoba log by (date, away, home) -> row."""
    out = {}
    with open(XWOBA_LOG) as f:
        for row in csv.DictReader(f):
            key = (row["game_date"], row["away_team"], row["home_team"])
            out[key] = row
    return out


def classify_pick(pick_row: dict, xwoba_row: dict) -> dict:
    """Determine which bucket this picked-game falls into.

    Returns dict with: bucket ('bad_beat' / 'bad_read' / 'null_zone' /
    'win' / 'unscoreable'), pick_team_xwoba, opp_xwoba, gap_pickside,
    won_scoreboard.
    """
    try:
        hx = float(xwoba_row["home_xwoba"]) if xwoba_row["home_xwoba"] else None
        ax = float(xwoba_row["away_xwoba"]) if xwoba_row["away_xwoba"] else None
        hs = int(xwoba_row["home_score"])
        as_ = int(xwoba_row["away_score"])
    except (ValueError, KeyError, TypeError):
        return {"bucket": "unscoreable"}

    if hx is None or ax is None:
        return {"bucket": "unscoreable"}

    pick = pick_row["pick"]
    if pick == pick_row["home"]:
        pick_x, opp_x = hx, ax
        won_scoreboard = hs > as_
    elif pick == pick_row["away"]:
        pick_x, opp_x = ax, hx
        won_scoreboard = as_ > hs
    else:
        return {"bucket": "unscoreable"}

    gap = pick_x - opp_x  # positive => pick side had xwOBA edge

    out = {
        "pick_team_xwoba": round(pick_x, 4),
        "opp_xwoba": round(opp_x, 4),
        "gap_pickside": round(gap, 4),
        "won_scoreboard": won_scoreboard,
        "home_score": hs,
        "away_score": as_,
    }

    if won_scoreboard:
        out["bucket"] = "win"
    elif gap >= X_XWOBA_GATE:
        out["bucket"] = "bad_beat"
    elif gap <= -X_XWOBA_GATE:
        out["bucket"] = "bad_read"
    else:
        out["bucket"] = "null_zone"
    return out


# ---- schedule lookup with PER-TEAM caching ----
# We pull each team's FULL archive-window schedule once, then slice per-pick.
# Avoids the per-(team,window) cache-miss explosion that would balloon API calls.
_TEAM_SCHED = {}  # team_abbr -> sorted list of game dicts
_SCHED_WINDOW = None  # (start_date, end_date) — the wide window used


def _pull_team_full_window(team_abbr: str, start: date, end: date) -> list[dict]:
    """Pull one team's complete schedule for [start, end]. Final games only."""
    team_id = TEAM_ID.get(team_abbr)
    if not team_id:
        return []
    params = {
        "sportId": 1,
        "teamId": team_id,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "hydrate": "linescore",
    }
    r = requests.get(SCHEDULE_URL, params=params, timeout=30,
                     headers={"User-Agent": USER_AGENT})
    time.sleep(RATE_LIMIT_SECONDS)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("detailedState", "")
            if status != "Final":
                continue
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_id = home["team"]["id"]
            is_home = (home_id == team_id)
            if is_home:
                team_score = home.get("score", 0)
                opp_score = away.get("score", 0)
                opp_id = away["team"]["id"]
            else:
                team_score = away.get("score", 0)
                opp_score = home.get("score", 0)
                opp_id = home["team"]["id"]
            opp_abbr = ID_TO_ABBR.get(opp_id, f"id{opp_id}")
            out.append({
                "date": d["date"],
                "opp_abbr": opp_abbr,
                "team_score": team_score,
                "opp_score": opp_score,
                "won": team_score > opp_score,
                "is_home": is_home,
            })
    out.sort(key=lambda x: x["date"])
    return out


def prefetch_all_team_schedules(teams: list[str], archive_start: date,
                                archive_end: date):
    """Pull schedules for all teams once. Wide window = archive + 30 days lookahead."""
    global _SCHED_WINDOW
    start = archive_start
    end = archive_end + timedelta(days=30)
    _SCHED_WINDOW = (start, end)
    print(f"  prefetching schedules: {start} -> {end} for {len(teams)} teams")
    for t in teams:
        if t in _TEAM_SCHED:
            continue
        _TEAM_SCHED[t] = _pull_team_full_window(t, start, end)


def next_n_games(team_abbr: str, after_date_str: str, n: int = WINDOW_N) -> list[dict]:
    """Slice the team's cached schedule for next n games strictly after after_date."""
    sched = _TEAM_SCHED.get(team_abbr)
    if sched is None:
        # Lazy fallback if dry-run skipped the prefetch
        start, end = _SCHED_WINDOW or (
            datetime.strptime(after_date_str, "%Y-%m-%d").date() + timedelta(days=1),
            datetime.strptime(after_date_str, "%Y-%m-%d").date() + timedelta(days=30),
        )
        sched = _pull_team_full_window(team_abbr, start, end)
        _TEAM_SCHED[team_abbr] = sched
    return [g for g in sched if g["date"] > after_date_str][:n]


def dry_run(picks: list[dict], xwoba: dict):
    """Print raw 5-game window for first Bad Beat + first Bad Read."""
    classified = []
    for p in picks:
        key = (p["date"], p["away"], p["home"])
        x = xwoba.get(key)
        if not x:
            continue
        c = classify_pick(p, x)
        if c["bucket"] in ("bad_beat", "bad_read"):
            classified.append({**p, **c})

    bb = next((c for c in classified if c["bucket"] == "bad_beat"), None)
    br = next((c for c in classified if c["bucket"] == "bad_read"), None)

    if not bb:
        print("DRY-RUN: no Bad Beat found in joined sample"); return
    if not br:
        print("DRY-RUN: no Bad Read found in joined sample"); return

    for label, c in [("BAD BEAT", bb), ("BAD READ", br)]:
        print(f"\n{'='*70}")
        print(f"{label} sample")
        print(f"{'='*70}")
        print(f"  date:           {c['date']}")
        print(f"  matchup:        {c['matchup']}")
        print(f"  model pick:     {c['pick']} ({c['tier']}, pick_prob={c['pick_prob']})")
        print(f"  game xwOBA:     pick={c['pick_team_xwoba']}  opp={c['opp_xwoba']}  gap={c['gap_pickside']:+.4f}")
        print(f"  scoreboard:     {c['away_score']}-{c['home_score']} (pick {'WON' if c['won_scoreboard'] else 'LOST'})")
        print(f"  next {WINDOW_N} played games for {c['pick']}:")
        nxt = next_n_games(c["pick"], c["date"])
        if not nxt:
            print(f"    (schedule lookup returned empty — check abbrev mapping for {c['pick']})")
            continue
        wins = 0
        for i, g in enumerate(nxt, 1):
            loc = "vs" if g["is_home"] else "@"
            mark = "W" if g["won"] else "L"
            if g["won"]:
                wins += 1
            print(f"    {i}. {g['date']}  {loc} {g['opp_abbr']:<4}  "
                  f"{c['pick']} {g['team_score']}-{g['opp_score']}  [{mark}]")
        print(f"  -> window win rate: {wins}/{len(nxt)} = {wins/len(nxt)*100:.1f}%")


def aggregate(picks: list[dict], xwoba: dict):
    """Full aggregation. Writes baseline folder."""
    joined = []
    for p in picks:
        key = (p["date"], p["away"], p["home"])
        x = xwoba.get(key)
        if not x:
            continue
        c = classify_pick(p, x)
        joined.append({**p, **c})

    by_bucket = defaultdict(list)
    for r in joined:
        by_bucket[r["bucket"]].append(r)

    print(f"Joined sample: {len(joined)} pick-rows with xwOBA")
    for b, rows in sorted(by_bucket.items()):
        print(f"  {b}: {len(rows)}")

    bb_rows = by_bucket["bad_beat"]
    br_rows = by_bucket["bad_read"]

    # Prefetch every team's schedule once (30 API calls, ~15s) instead of
    # per-pick (50-100 calls, 50s+).
    archive_dates = sorted(set(p["date"] for p in picks))
    archive_start = datetime.strptime(archive_dates[0], "%Y-%m-%d").date()
    archive_end = datetime.strptime(archive_dates[-1], "%Y-%m-%d").date()
    teams_needed = sorted(set(r["pick"] for r in (bb_rows + br_rows)))
    prefetch_all_team_schedules(teams_needed, archive_start, archive_end)

    # Compute next-5 win rate per loss in each bucket.
    def cohort_winrate(rows: list[dict], label: str) -> tuple[float, int, int]:
        total_w, total_g = 0, 0
        for r in rows:
            nxt = next_n_games(r["pick"], r["date"])
            for g in nxt:
                total_g += 1
                if g["won"]:
                    total_w += 1
        wr = total_w / total_g if total_g else 0.0
        print(f"  {label}: {total_w}/{total_g} window-game outcomes => {wr*100:.2f}%")
        return wr, total_w, total_g

    print("\nComputing cohort window win-rates...")
    bb_wr, bb_w, bb_g = cohort_winrate(bb_rows, "bad_beat cohort")
    br_wr, br_w, br_g = cohort_winrate(br_rows, "bad_read cohort")

    delta_pp = (bb_wr - br_wr) * 100

    if delta_pp >= Y_KEEP_DELTA_PP:
        verdict = "KEEP"
        verdict_long = (f"Mute Bad Beat penalty to 0.5x in "
                        f"apply_calibration_from_all_picks. Delta {delta_pp:+.2f}pp "
                        f"clears the locked +{Y_KEEP_DELTA_PP}pp KEEP criterion.")
    elif delta_pp < KILL_DELTA_PP:
        verdict = "KILL"
        verdict_long = (f"Do NOT mute Bad Beat penalty. Delta {delta_pp:+.2f}pp "
                        f"violates the locked {KILL_DELTA_PP}pp KILL threshold — "
                        "Bad Beat cohort regresses *worse* than Bad Read, implying "
                        "model logic is misaligned with reality in a way xwOBA "
                        "isn't capturing. Shelve until 2026-06-26 re-probe.")
    else:
        verdict = "NULL"
        verdict_long = (f"No code change. Delta {delta_pp:+.2f}pp falls in the "
                        f"locked null zone [{KILL_DELTA_PP}, +{Y_KEEP_DELTA_PP}]. "
                        "Document directional reading and re-probe at 2026-06-26 "
                        "with the tighter +10pp criterion on the larger sample.")

    print(f"\nDelta (bad_beat - bad_read) = {delta_pp:+.2f}pp  =>  {verdict}")
    print(verdict_long)

    # Write picks_with_xwoba.csv
    out_csv = os.path.join(OUT_DIR, "picks_with_xwoba.csv")
    if joined:
        fieldnames = ["date", "matchup", "pick", "tier", "pick_prob",
                      "bucket", "pick_team_xwoba", "opp_xwoba",
                      "gap_pickside", "won_scoreboard",
                      "home_score", "away_score"]
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in joined:
                w.writerow(r)
        print(f"Wrote {out_csv}")

    # Write summary.json
    summary = {
        "probe_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archive_window": "2026-04-27 .. 2026-05-26",
        "locked_spec": {
            "X_xwoba_gate": X_XWOBA_GATE,
            "Y_keep_delta_pp": Y_KEEP_DELTA_PP,
            "kill_delta_pp": KILL_DELTA_PP,
            "window_n": WINDOW_N,
            "re_probe_date": "2026-06-26",
            "re_probe_keep_pp": 10.0,
            "memory_pointer": "project_luck_adjusted_probe_thresholds.md",
        },
        "joined_sample_size": len(joined),
        "bucket_counts": {b: len(rows) for b, rows in by_bucket.items()},
        "bad_beat_cohort": {
            "n_losses": len(bb_rows),
            "window_wins": bb_w,
            "window_games": bb_g,
            "win_rate": round(bb_wr, 4),
        },
        "bad_read_cohort": {
            "n_losses": len(br_rows),
            "window_wins": br_w,
            "window_games": br_g,
            "win_rate": round(br_wr, 4),
        },
        "delta_pp": round(delta_pp, 2),
        "verdict": verdict,
        "verdict_long": verdict_long,
    }
    out_json = os.path.join(OUT_DIR, "summary.json")
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {out_json}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print raw 5-game window for one Bad Beat + one Bad Read, no aggregation.")
    args = ap.parse_args()

    picks = load_picks_archive()
    xwoba = load_xwoba_log()
    print(f"loaded {len(picks)} picks from {len(set(p['date'] for p in picks))} dates")
    print(f"loaded {len(xwoba)} games from game_xwoba_log.csv")

    if args.dry_run:
        dry_run(picks, xwoba)
    else:
        aggregate(picks, xwoba)


if __name__ == "__main__":
    main()
,
            "window_games": bb_g,
            "win_rate": round(bb_wr, 4),
        },
        "bad_read_cohort": {
            "n_losses": len(br_rows),
            "window_wins": br_w,
            "window_games": br_g,
            "win_rate": round(br_wr, 4),
        },
        "delta_pp": round(delta_pp, 2),
        "verdict": verdict,
        "verdict_long": verdict_long,
    }
    out_json = os.path.join(OUT_DIR, "summary.json")
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {out_json}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print raw 5-game window for one Bad Beat + one Bad Read, no aggregation.")
    args = ap.parse_args()

    picks = load_picks_archive()
    xwoba = load_xwoba_log()
    print(f"loaded {len(picks)} picks from {len(set(p['date'] for p in picks))} dates")
    print(f"loaded {len(xwoba)} games from game_xwoba_log.csv")

    if args.dry_run:
        dry_run(picks, xwoba)
    else:
        aggregate(picks, xwoba)


if __name__ == "__main__":
    main()
