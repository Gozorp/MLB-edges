#!/usr/bin/env python3
"""
probe.py — Lineup-matchup-gap signal validation (Rule 2, pre-F6 gate).

Joins picks_<date>_diag.csv files with MLB Stats API outcomes, computes
the pick-side-oriented lineup_matchup_gap (= home_lc - away_lc, flipped
when pick is the away team), and reports AUC + Pearson r vs the binary
"pick won" target.

Decision matrix (locked 2026-05-27 before any code ran, Rule 2):
  AUC >= 0.55       -> green light F6 conviction signal
  AUC <= 0.52       -> kill F6, fall back to raw XGBoost feature only
  0.52 < AUC < 0.55 -> grey zone; revisit with more data before deciding

Usage:
    python probe.py --start 2026-04-27 --end 2026-05-26 \\
                    --out data/baselines/lineup_matchup_gap_X_to_Y/
"""
from __future__ import annotations
import argparse, csv, glob, json, math, os, re, statistics, sys, urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

TEAM_ABBR = {
    "Arizona Diamondbacks":"ARI","Atlanta Braves":"ATL","Baltimore Orioles":"BAL",
    "Boston Red Sox":"BOS","Chicago White Sox":"CHW","Chicago Cubs":"CHC",
    "Cincinnati Reds":"CIN","Cleveland Guardians":"CLE","Colorado Rockies":"COL",
    "Detroit Tigers":"DET","Houston Astros":"HOU","Kansas City Royals":"KC",
    "Los Angeles Angels":"LAA","Los Angeles Dodgers":"LAD","Miami Marlins":"MIA",
    "Milwaukee Brewers":"MIL","Minnesota Twins":"MIN","New York Yankees":"NYY",
    "New York Mets":"NYM","Oakland Athletics":"OAK","Athletics":"OAK",
    "Philadelphia Phillies":"PHI","Pittsburgh Pirates":"PIT","San Diego Padres":"SD",
    "San Francisco Giants":"SF","Seattle Mariners":"SEA","St. Louis Cardinals":"STL",
    "Tampa Bay Rays":"TB","Texas Rangers":"TEX","Toronto Blue Jays":"TOR",
    "Washington Nationals":"WSH",
}
MU_RE = re.compile(r"^(\w{2,4})\s*@\s*(\w{2,4})(?:\s*\(G(\d+)(?:\s+of\s+\d+)?\))?")
REPO = Path(__file__).resolve().parents[3]


def fetch_day(d):
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1"
           f"&date={d}&hydrate=linescore")
    req = urllib.request.Request(url, headers={"User-Agent": "probe/1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    out = {}
    for di in data.get("dates", []):
        for g in di.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final": continue
            ls = (g.get("linescore") or {}).get("teams", {})
            hr = (ls.get("home") or {}).get("runs")
            ar = (ls.get("away") or {}).get("runs")
            if hr is None or ar is None: continue
            home = TEAM_ABBR.get(g["teams"]["home"]["team"]["name"], "??")
            away = TEAM_ABBR.get(g["teams"]["away"]["team"]["name"], "??")
            gn = g.get("gameNumber", 1)
            out[(d, away, home, gn)] = home if hr > ar else away
    return out


def auc(scores, labels):
    """AUC via Mann-Whitney U. scores higher -> predict label=1."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg: return float("nan")
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n: wins += 1
            elif p == n: wins += 0.5
    return wins / (len(pos) * len(neg))


def pearson_r(xs, ys):
    if len(xs) < 2: return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    a = datetime.fromisoformat(args.start).date()
    b = datetime.fromisoformat(args.end).date()
    dates = [(a + timedelta(days=i)).isoformat()
             for i in range((b - a).days + 1)]

    print(f"[probe] window {args.start} -> {args.end} ({len(dates)} dates)")
    outcomes = {}
    for d in dates:
        try: outcomes.update(fetch_day(d))
        except Exception as e: print(f"[warn] {d}: {e}", file=sys.stderr)
    print(f"[probe] fetched {len(outcomes)} final-game outcomes")

    gaps, wons = [], []
    skipped_no_lc, skipped_no_outcome, skipped_no_pick = 0, 0, 0
    rows_for_csv = []
    for csv_path in sorted(glob.glob(str(REPO / "docs" / "data"
                                         / "picks_*_diag.csv"))):
        m = re.match(r"picks_(\d{4}-\d{2}-\d{2})_diag\.csv",
                     os.path.basename(csv_path))
        if not m: continue
        d = m.group(1)
        if d < args.start or d > args.end: continue
        with open(csv_path, newline="") as fh:
            for row in csv.DictReader(fh):
                mm = MU_RE.match(row.get("matchup", ""))
                if not mm: continue
                away, home, gn = mm.group(1), mm.group(2), int(mm.group(3) or 1)
                pick = (row.get("pick") or "").strip()
                if not pick or pick == "TBD":
                    skipped_no_pick += 1; continue
                try:
                    h_lc = float(row.get("home_lineup_concentration") or "")
                    a_lc = float(row.get("away_lineup_concentration") or "")
                except ValueError:
                    skipped_no_lc += 1; continue
                winner = outcomes.get((d, away, home, gn)) \
                         or outcomes.get((d, away, home, 1))
                if not winner: skipped_no_outcome += 1; continue
                # Pick-side oriented gap: positive = pick's lineup advantage.
                home_minus_away = h_lc - a_lc
                gap = home_minus_away if pick == home else -home_minus_away
                won = 1 if pick == winner else 0
                gaps.append(gap); wons.append(won)
                rows_for_csv.append({"date": d, "matchup": row["matchup"],
                                     "pick": pick, "home_lc": h_lc,
                                     "away_lc": a_lc, "gap_pickside": gap,
                                     "winner": winner, "won": won})

    n = len(gaps)
    print(f"[probe] joined {n} pick-outcome pairs (skipped: "
          f"no_pick={skipped_no_pick}, no_lc={skipped_no_lc}, "
          f"no_outcome={skipped_no_outcome})")

    if n < 30:
        print("[probe] WARN: n<30, results may not be meaningful")

    a_auc = auc(gaps, wons)
    a_r = pearson_r(gaps, [float(w) for w in wons])
    win_rate = sum(wons) / n if n else float("nan")
    gap_mean = statistics.fmean(gaps) if gaps else float("nan")
    gap_std = statistics.pstdev(gaps) if len(gaps) >= 2 else float("nan")
    sorted_g = sorted(gaps)
    p10 = sorted_g[int(0.10 * n)] if n else float("nan")
    p50 = sorted_g[n // 2] if n else float("nan")
    p90 = sorted_g[int(0.90 * n)] if n else float("nan")

    print()
    print(f"  n_pairs           = {n}")
    print(f"  win_rate          = {win_rate:.3f}")
    print(f"  AUC               = {a_auc:.4f}")
    print(f"  Pearson r         = {a_r:.4f}")
    print(f"  gap_mean          = {gap_mean:+.4f}")
    print(f"  gap_stdev         = {gap_std:.4f}")
    print(f"  gap percentiles   = p10={p10:+.3f}  p50={p50:+.3f}  p90={p90:+.3f}")
    if a_auc >= 0.55:
        verdict = "GREEN  — F6 justified, full proposal greenlit"
    elif a_auc <= 0.52:
        verdict = "RED    — F6 killed, fall back to raw XGB feature only"
    else:
        verdict = "GREY   — inconclusive, defer until more data"
    print(f"  VERDICT           = {verdict}")

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "picks_with_gap.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "matchup", "pick",
                                            "home_lc", "away_lc",
                                            "gap_pickside", "winner", "won"])
        w.writeheader()
        for r in rows_for_csv: w.writerow(r)
    summary = {
        "window": {"start": args.start, "end": args.end},
        "n_pairs": n, "win_rate": round(win_rate, 4),
        "auc": round(a_auc, 4), "pearson_r": round(a_r, 4),
        "gap": {"mean": round(gap_mean, 4), "stdev": round(gap_std, 4),
                "p10": round(p10, 3), "p50": round(p50, 3),
                "p90": round(p90, 3)},
        "verdict": verdict,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\n[probe] wrote {out_dir/'picks_with_gap.csv'}")
    print(f"[probe] wrote {out_dir/'summary.json'}")


if __name__ == "__main__":
    main()
