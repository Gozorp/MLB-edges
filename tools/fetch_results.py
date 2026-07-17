#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-fetch actual MLB game results for a slate date so the local postgame
Claude job can Read them (instead of curling). Writes docs/data/_results_<date>.json."""
import sys, os, json, urllib.request, datetime
try:
    from slate_date import slate_today
except ImportError:
    from tools.slate_date import slate_today
d = sys.argv[1] if len(sys.argv) > 1 else (slate_today() - datetime.timedelta(days=1)).isoformat()
url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s"
       "&hydrate=team,linescore,decisions" % d)
out = []
ok = False
try:
    j = json.load(urllib.request.urlopen(url, timeout=30))
    for dt in j.get("dates", []):
        for g in dt.get("games", []):
            ls = g.get("linescore", {}) or {}
            lst = ls.get("teams", {}) or {}
            tm = g.get("teams", {}) or {}
            out.append({
                "away": (((tm.get("away") or {}).get("team") or {}).get("abbreviation")),
                "home": (((tm.get("home") or {}).get("team") or {}).get("abbreviation")),
                "away_runs": (lst.get("away") or {}).get("runs"),
                "home_runs": (lst.get("home") or {}).get("runs"),
                "status": (g.get("status") or {}).get("detailedState"),
                "gamePk": g.get("gamePk"),
            })
    ok = True
except Exception as e:
    print("fetch_results: error %r" % e)
path = "docs/data/_results_%s.json" % d
if not ok:
    # Never clobber a previously-good results file with an empty payload on a
    # transient fetch failure -- the postgame job would grade against nothing.
    print("fetch_results: fetch FAILED -- leaving any existing %s untouched" % path)
else:
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": d, "games": out}, indent=2))
    os.replace(path + ".tmp", path)  # atomic
    print("fetch_results: wrote %s with %d games" % (path, len(out)))
