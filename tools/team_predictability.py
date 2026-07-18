#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
team_predictability.py -- per-team model accuracy ranking sidecar. DISPLAY ONLY.

For every team, over all graded historical picks (diag CSVs joined to
verified finals): how often was the model's pick CORRECT in games involving
that team?  Best -> worst ranking for the dashboard's tools drawer.

Two views per team:
  n / correct / acc          -- all graded games the team played in
  picked_n / picked_wins     -- games where the model picked THIS team

Writes docs/data/team_predictability.json (atomic).
Usage: python tools/team_predictability.py    (no args; scans all history)
Sandboxed: any failure prints a warning and writes nothing.
"""
import csv
import datetime
import glob
import json
import os
import re

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
S = lambda x: (x if isinstance(x, str) else "") if x is not None else ""
MIN_N = 8          # below this the ranking is noise; shown grayed at the bottom
csv.field_size_limit(10 ** 7)


def _truth():
    truth = {}
    for f in glob.glob("docs/data/postgame/*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for m, i in (d.get("by_matchup", {}) or {}).items():
            if not isinstance(i, dict) or "(G" in S(m):
                continue
            sc = S(i.get("final_score")).strip()
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(m))
            if mm and re.match(r"^\d+-\d+$", sc):
                a, b = map(int, sc.split("-"))
                truth[(date, mm.group(1), mm.group(2))] = (a, b)
    for f in glob.glob("docs/data/_results_*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for g in d.get("games", []):
            if g.get("status") != "Final":
                continue
            try:
                truth[(date, S(g["away"]).strip(), S(g["home"]).strip())] = \
                    (int(g["away_runs"]), int(g["home_runs"]))
            except Exception: pass
    return truth


def main():
    truth = _truth()
    teams = {}   # abbr -> dict(n, correct, picked_n, picked_wins)
    n_graded = 0
    dates = set()
    seen = set()   # global across files: root + docs/data hold copies of the same slates
    for f in sorted(glob.glob("picks_20??-??-??_diag.csv")
                    + glob.glob("docs/data/picks_20??-??-??_diag.csv")):
        md = re.search(r"picks_(\d{4}-\d\d-\d\d)_diag", f)
        if not md:
            continue
        date = md.group(1)
        try:
            rdr = list(csv.DictReader(open(f, encoding="utf-8", errors="replace")))
        except Exception:
            continue
        for r in rdr:
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(r.get("matchup")))
            if not mm:
                continue
            away, home = mm.group(1), mm.group(2)
            if away in ("AL", "NL") or home in ("AL", "NL"):
                continue   # All-Star exhibition — not a team ranking data point
            key = (date, away, home)
            if key in seen or key not in truth:
                continue
            seen.add(key)
            pick = S(r.get("pick")).strip()
            if not pick or pick == "TBD" or pick not in (away, home):
                continue
            ar, hr = truth[key]
            if ar == hr:
                continue
            winner = home if hr > ar else away
            correct = (pick == winner)
            n_graded += 1
            dates.add(date)
            for t in (away, home):
                d = teams.setdefault(t, {"n": 0, "correct": 0, "picked_n": 0, "picked_wins": 0})
                d["n"] += 1
                d["correct"] += int(correct)
                if t == pick:
                    d["picked_n"] += 1
                    d["picked_wins"] += int(correct)

    if n_graded < 30:
        print("[team-predict] only %d graded picks; refusing to write noise" % n_graded)
        return
    out_teams = []
    for t, d in teams.items():
        acc = d["correct"] / d["n"] if d["n"] else 0.0
        out_teams.append({"team": t, "n": d["n"], "correct": d["correct"],
                          "acc": round(acc, 3),
                          "picked_n": d["picked_n"], "picked_wins": d["picked_wins"],
                          "thin": d["n"] < MIN_N})
    # best -> worst; thin samples sink to the bottom regardless of acc
    out_teams.sort(key=lambda x: (x["thin"], -x["acc"], -x["n"]))
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "n_graded_picks": n_graded, "n_dates": len(dates), "min_n": MIN_N,
           "basis": ("Share of graded model picks that were CORRECT in games "
                     "involving each team, all verified history. "
                     "picked_* = record when the model picked that team."),
           "teams": out_teams}
    outp = os.path.join(ROOT, "docs", "data", "team_predictability.json")
    tmp = outp + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, outp)
    print("[team-predict] %d teams over %d graded picks (%d dates) -> %s"
          % (len(out_teams), n_graded, len(dates), outp))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[team-predict] WARN unexpected failure %r -- nothing written" % (e,))
