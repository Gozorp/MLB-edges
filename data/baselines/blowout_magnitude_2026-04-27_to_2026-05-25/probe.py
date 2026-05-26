#!/usr/bin/env python3
"""
probe.py — reproducible blowout-magnitude baseline.

Joins picks_<date>_diag.csv files (under docs/data/) with MLB Stats
API final scores, then segments the resulting losing-picks population
by margin (|run_diff| >= 5 = "blowout") and conviction tier. Output
is a frozen snapshot suitable for backtesting future drift in the
blowout-vs-signal hypothesis.

Why this exists: in May 2026 we considered porting the legacy
recursive_weight_update.apply_blowout_penalties magnitude logic into
the new symmetric gradient loop. The hypothesis was that losing by
5+ runs encodes information about signal failure that losing by 1-2
runs does not. This probe tested that hypothesis on 28 days of real
slates and found no statistically meaningful signal — blowout losses
occur at almost exactly the MLB-baseline rate among our losses,
suggesting blowouts are bullpen variance rather than model failure.
The legacy code was deleted on the strength of this finding. This
snapshot exists so the question can be re-asked in the future against
the same methodology.

Usage:
    python probe.py --start 2026-04-27 --end 2026-05-25 \\
                    --out data/baselines/blowout_magnitude_X_to_Y/

Outputs (in --out):
    picks_resolved.csv   raw pick x outcome join (one row per resolved pick)
    summary.json         derived stats (tier x margin, percentiles, baseline)

Only depends on the Python stdlib and picks_<date>_diag.csv files
already in the repo. Re-runs are safe and idempotent.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

TEAM_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago White Sox": "CWS", "Chicago Cubs": "CHC",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Yankees": "NYY",
    "New York Mets": "NYM", "Oakland Athletics": "OAK",
    "Athletics": "ATH", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

MATCHUP_RE = re.compile(
    r"^(\w{2,4})\s*@\s*(\w{2,4})(?:\s*\(G(\d+)(?:\s+of\s+\d+)?\))?")

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DIAG_GLOB = str(REPO / "docs" / "data" / "picks_*_diag.csv")


def fetch_day(date_iso: str) -> list[dict]:
    """Hit MLB Stats API for one day's final scores."""
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1"
           f"&date={date_iso}&hydrate=linescore")
    req = urllib.request.Request(url, headers={"User-Agent": "probe/1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        payload = json.loads(r.read().decode())
    out = []
    for d in payload.get("dates", []):
        for g in d.get("games", []):
            ls = g.get("linescore", {})
            tt = ls.get("teams", {})
            hr = (tt.get("home") or {}).get("runs")
            ar = (tt.get("away") or {}).get("runs")
            if hr is None or ar is None:
                continue
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            home_full = g["teams"]["home"]["team"]["name"]
            away_full = g["teams"]["away"]["team"]["name"]
            home = TEAM_ABBR.get(home_full, home_full)
            away = TEAM_ABBR.get(away_full, away_full)
            hr, ar = int(hr), int(ar)
            out.append({
                "date": date_iso, "game_num": g.get("gameNumber", 1),
                "home": home, "away": away,
                "home_R": hr, "away_R": ar,
                "winner": home if hr > ar else away,
                "run_diff": abs(hr - ar),
            })
    return out


def daterange(start: str, end: str) -> list[str]:
    a = datetime.fromisoformat(start).date()
    b = datetime.fromisoformat(end).date()
    return [(a + timedelta(days=i)).isoformat()
            for i in range((b - a).days + 1)]


def join_picks_to_outcomes(start: str, end: str,
                           diag_glob: str) -> list[dict]:
    dates = daterange(start, end)
    outcomes_by_date: dict[str, list[dict]] = {}
    for d in dates:
        try:
            outcomes_by_date[d] = fetch_day(d)
        except Exception as e:
            print(f"[warn] {d}: {e}", file=sys.stderr)

    oidx = {}
    for d, games in outcomes_by_date.items():
        for g in games:
            oidx[(d, g["away"], g["home"], g["game_num"])] = g

    rows = []
    for diag in sorted(glob.glob(diag_glob)):
        base = os.path.basename(diag)
        m = re.match(r"picks_(\d{4}-\d{2}-\d{2})_diag\.csv", base)
        if not m:
            continue
        d = m.group(1)
        if d not in outcomes_by_date:
            continue
        with open(diag, newline="") as fh:
            for row in csv.DictReader(fh):
                mm = MATCHUP_RE.match(row.get("matchup", ""))
                if not mm:
                    continue
                away, home, gn = mm.group(1), mm.group(2), int(mm.group(3) or 1)
                o = oidx.get((d, away, home, gn)) or \
                    oidx.get((d, away, home, 1))
                if not o:
                    continue
                pick = (row.get("pick") or "").strip()
                if pick not in (away, home):
                    continue
                try:
                    p_model = float(row.get("p_model") or 0)
                except ValueError:
                    p_model = 0.0
                try:
                    pick_prob = float(row.get("pick_prob") or 0)
                except ValueError:
                    pick_prob = 0.0
                rows.append({
                    "date": d,
                    "matchup": row["matchup"],
                    "pick": pick,
                    "tier": (row.get("tier") or "").strip(),
                    "signals": (row.get("signals") or "").strip(),
                    "p_model": round(p_model, 4),
                    "pick_prob": round(pick_prob, 4),
                    "won": (pick == o["winner"]),
                    "run_diff": o["run_diff"],
                    "winner": o["winner"],
                    "home_R": o["home_R"],
                    "away_R": o["away_R"],
                })
    return rows, outcomes_by_date


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(p * len(xs))))
    return xs[k]


def summarize(rows: list[dict], outcomes_by_date: dict,
              start: str, end: str) -> dict:
    n = len(rows)
    losses = [r for r in rows if not r["won"]]
    blowout_losses = [r for r in losses if r["run_diff"] >= 5]

    all_games = [g for games in outcomes_by_date.values() for g in games]
    baseline_blowout_rate = (
        sum(1 for g in all_games if g["run_diff"] >= 5) /
        max(1, len(all_games))
    )

    tier_table = {}
    tiers = sorted({r["tier"] for r in rows if r["tier"]})
    for t in tiers:
        won_close = sum(1 for r in rows
                        if r["tier"] == t and r["won"] and r["run_diff"] < 5)
        won_blow = sum(1 for r in rows
                       if r["tier"] == t and r["won"] and r["run_diff"] >= 5)
        lost_close = sum(1 for r in rows if r["tier"] == t
                         and not r["won"] and r["run_diff"] < 5)
        lost_blow = sum(1 for r in rows if r["tier"] == t
                        and not r["won"] and r["run_diff"] >= 5)
        tot = won_close + won_blow + lost_close + lost_blow
        if tot == 0:
            continue
        tier_table[t] = {
            "n": tot,
            "won_close": won_close, "won_blow": won_blow,
            "lost_close": lost_close, "lost_blow": lost_blow,
            "blow_loss_rate": round(lost_blow / tot, 4),
        }

    def cohort_stats(filt) -> dict:
        xs = [r["pick_prob"] for r in rows if filt(r)]
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "mean": round(sum(xs) / len(xs), 4),
            "p25": round(percentile(xs, 0.25), 4),
            "p50": round(percentile(xs, 0.50), 4),
            "p75": round(percentile(xs, 0.75), 4),
        }

    return {
        "window": {"start": start, "end": end,
                   "n_dates_with_outcomes": len(outcomes_by_date)},
        "totals": {
            "resolved_picks": n,
            "losses": len(losses),
            "blowout_losses": len(blowout_losses),
            "baseline_mlb_games": len(all_games),
        },
        "blowout_rates": {
            "our_losses": round(
                len(blowout_losses) / max(1, len(losses)), 4),
            "mlb_baseline": round(baseline_blowout_rate, 4),
            "delta_pp": round(
                (len(blowout_losses) / max(1, len(losses)) -
                 baseline_blowout_rate) * 100, 2),
        },
        "tier_table": tier_table,
        "loss_cohort_pick_prob": {
            "lost_close":
                cohort_stats(lambda r: not r["won"] and r["run_diff"] < 5),
            "lost_blowout":
                cohort_stats(lambda r: not r["won"] and r["run_diff"] >= 5),
        },
        "high_conviction_losses": {
            "platinum_diamond_total":
                sum(1 for r in rows
                    if r["tier"] in ("PLATINUM", "DIAMOND")),
            "platinum_diamond_losses":
                sum(1 for r in rows
                    if not r["won"] and r["tier"] in ("PLATINUM", "DIAMOND")),
            "platinum_diamond_blowout_losses":
                sum(1 for r in rows
                    if not r["won"] and r["run_diff"] >= 5
                    and r["tier"] in ("PLATINUM", "DIAMOND")),
        },
    }


def write_csv(rows: list[dict], path: Path) -> None:
    cols = ["date", "matchup", "pick", "tier", "signals", "p_model",
            "pick_prob", "won", "run_diff", "winner", "home_R", "away_R"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in cols})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="ISO date (inclusive)")
    ap.add_argument("--end", required=True, help="ISO date (inclusive)")
    ap.add_argument("--out", required=True,
                    help="Output directory (created if missing)")
    ap.add_argument("--diag-glob", default=DEFAULT_DIAG_GLOB,
                    help="Glob for picks_*_diag.csv files")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[probe] window {args.start} -> {args.end}")
    rows, oxd = join_picks_to_outcomes(args.start, args.end, args.diag_glob)
    print(f"[probe] {len(rows)} resolved picks across "
          f"{len(oxd)} dates with outcomes")

    write_csv(rows, out_dir / "picks_resolved.csv")
    summary = summarize(rows, oxd, args.start, args.end)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")

    print(f"[probe] wrote {out_dir / 'picks_resolved.csv'}")
    print(f"[probe] wrote {out_dir / 'summary.json'}")
    print(f"[probe] our-loss blowout rate "
          f"{summary['blowout_rates']['our_losses']:.1%} vs MLB baseline "
          f"{summary['blowout_rates']['mlb_baseline']:.1%} "
          f"(delta {summary['blowout_rates']['delta_pp']:+.2f}pp)")


if __name__ == "__main__":
    main()
