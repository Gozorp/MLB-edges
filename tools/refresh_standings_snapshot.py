#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/refresh_standings_snapshot.py — keep the team-quality standings feed
fresh (2026-07-10 flaw fix: the Chrome-based B-R scraper died in April and the
pipeline was computing team form gaps off 78-day-old records / zero-fills).

Writes today's snapshot in the EXACT format bref.py already reads
(./data/bref/standings/{YYYYMMDD}_upto-*.csv, columns
"Tm","W","L","W-L%","GB","RS","RA","pythW-L%"), sourced from MLB statsapi —
wins/losses/RS/RA are objective facts, identical numbers to B-R, with none of
the anti-bot scraping fragility. pythW-L% uses the B-R exponent (1.83).

Run standalone or as the pre-predict chain step. Atomic writes; exit 0 OK.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join("data", "bref", "standings")

# statsapi team abbreviation -> B-R code style used by the existing snapshots
BREF_CODE = {
    "AZ": "ARI", "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CWS": "CHW", "CHW": "CHW", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC": "KCR", "KCR": "KCR",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN",
    "NYM": "NYM", "NYY": "NYY", "OAK": "ATH", "ATH": "ATH", "PHI": "PHI",
    "PIT": "PIT", "SD": "SDP", "SDP": "SDP", "SEA": "SEA", "SF": "SFG",
    "SFG": "SFG", "STL": "STL", "TB": "TBR", "TBR": "TBR", "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSN", "WSN": "WSN",
}
DIV_STEM = {
    "American League East": "AL-E", "American League Central": "AL-C",
    "American League West": "AL-W", "National League East": "NL-E",
    "National League Central": "NL-C", "National League West": "NL-W",
}


def fetch():
    season = datetime.date.today().year
    url = ("https://statsapi.mlb.com/api/v1/standings?leagueId=103,104"
           "&season=%d&standingsTypes=regularSeason&hydrate=division,team" % season)
    return json.load(urllib.request.urlopen(url, timeout=30))


def pyth(rs, ra, exp=1.83):
    if rs <= 0 and ra <= 0:
        return ""
    try:
        return "%.3f" % (rs ** exp / (rs ** exp + ra ** exp))
    except ZeroDivisionError:
        return ""


def fmt_pct(w, l):
    g = w + l
    return ("%.3f" % (w / g)) if g else ".000"


def row(t):
    team = t.get("team") or {}
    ab = team.get("abbreviation") or ""
    code = BREF_CODE.get(ab, ab)
    w = int(t.get("wins") or 0)
    l = int(t.get("losses") or 0)
    rs = int(t.get("runsScored") or 0)
    ra = int(t.get("runsAllowed") or 0)
    gb = (t.get("gamesBack") or "-").strip()
    gb = "--" if gb in ("-", "") else gb
    return [code, w, l, fmt_pct(w, l).lstrip("0"), gb, rs, ra, pyth(rs, ra).lstrip("0")]


def write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write('"Tm","W","L","W-L%","GB","RS","RA","pythW-L%"\n')
        for r in rows:
            f.write(",".join('"%s"' % v for v in r) + "\n")
    os.replace(tmp, path)


def main():
    os.makedirs(OUT, exist_ok=True)
    data = fetch()
    today = datetime.date.today().strftime("%Y%m%d")
    per_league = {"AL": [], "NL": []}
    n_files = 0

    for rec in data.get("records", []):
        div_name = ((rec.get("division") or {}).get("name")) or ""
        stem = DIV_STEM.get(div_name)
        if not stem:
            continue
        rows = [row(t) for t in rec.get("teamRecords", [])]
        rows.sort(key=lambda r: (-(r[1] / max(1, r[1] + r[2]))))
        write_csv(os.path.join(OUT, "%s_upto-%s.csv" % (today, stem)), rows)
        n_files += 1
        per_league[stem.split("-")[0]].extend(rows)

    for lg, rows in per_league.items():
        if not rows:
            continue
        rows.sort(key=lambda r: (-(r[1] / max(1, r[1] + r[2]))))
        top_w, top_l = rows[0][1], rows[0][2]
        for r in rows:
            gb = ((top_w - r[1]) + (r[2] - top_l)) / 2.0
            r[4] = "--" if gb <= 0 else ("%.1f" % gb).rstrip("0").rstrip(".")
        write_csv(os.path.join(OUT, "%s_upto-%s-overall.csv" % (today, lg)), rows)
        n_files += 1

    if n_files < 8:
        print("standings refresh INCOMPLETE: wrote %d/8 files" % n_files)
        return 1
    print("standings refresh OK: %d files for %s (source: statsapi, format: bref)"
          % (n_files, today))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("standings refresh FAILED: %r" % (e,))
        sys.exit(1)
