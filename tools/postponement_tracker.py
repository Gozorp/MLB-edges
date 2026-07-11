#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/postponement_tracker.py — automatic postponement → makeup-date linking
(2026-07-11, user feature request).

MLB postponements already flow into the pipeline implicitly: the slate is
schedule-driven, so a makeup game appears in its new date's bake on its own.
What this sidecar adds is the CONVERSION VISIBILITY, automatically:
  * on the ORIGINAL date: this game was postponed (reason) → plays <new date>
  * on the MAKEUP date:   this game is a makeup of <original date>

Writes docs/data/postponed_<date>.json for the slate date AND the previous
day (postponements are often announced after the prior day's last bake):

  { "date": "...", "postponed": [ {matchup, reason, reschedule_date,
      reschedule_time_utc, game_pk} ], "makeups": [ {matchup, makeup_of,
      double_header, game_number, game_pk} ] }

statsapi-only, display/telemetry sidecar — never touches the frozen model.
The dashboard renders chips from it (PPD → Jul 12 / MAKEUP · from Jul 10).
Chained after the bake; in publish candidates. `--selftest` = offline fixture.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# statsapi abbreviation -> slate matchup code (mirror of the pipeline's usage)
SLATE_CODE = {"ATH": "OAK", "CWS": "CHW", "AZ": "ARI", "WSN": "WSH"}
PPD_STATES = ("Postponed", "Suspended", "Cancelled")


def code(team):
    ab = (team or {}).get("abbreviation") or (team or {}).get("name") or "?"
    return SLATE_CODE.get(ab, ab)


def fetch_day(date):
    u = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s"
         "&hydrate=team" % date)
    js = json.load(urllib.request.urlopen(u, timeout=25))
    games = []
    for day in js.get("dates", []):
        games.extend(day.get("games", []))
    return games


def build_report(date, games):
    postponed, makeups = [], []
    for g in games:
        st = g.get("status") or {}
        state = st.get("detailedState") or ""
        away = code((g["teams"]["away"] or {}).get("team"))
        home = code((g["teams"]["home"] or {}).get("team"))
        matchup = "%s @ %s" % (away, home)
        if any(k in state for k in PPD_STATES):
            postponed.append({
                "matchup": matchup,
                "game_pk": g.get("gamePk"),
                "state": state,
                "reason": st.get("reason"),
                "reschedule_date": g.get("rescheduleGameDate"),
                "reschedule_time_utc": g.get("rescheduleDate"),
            })
        rf_date = g.get("rescheduledFromDate")
        if rf_date and rf_date != date:
            makeups.append({
                "matchup": matchup,
                "game_pk": g.get("gamePk"),
                "makeup_of": rf_date,
                "double_header": g.get("doubleHeader"),
                "game_number": g.get("gameNumber"),
            })
    return {"date": date,
            "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds"),
            "postponed": postponed, "makeups": makeups}


def write_report(rep):
    out = "docs/data/postponed_%s.json" % rep["date"]
    tmp = out + ".tmp"
    json.dump(rep, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, out)
    print("postponed sidecar %s: %d postponed, %d makeups -> %s"
          % (rep["date"], len(rep["postponed"]), len(rep["makeups"]), out))


def selftest():
    fixture = [{
        "gamePk": 1, "rescheduleGameDate": "2026-07-11",
        "rescheduleDate": "2026-07-11T16:05:00Z",
        "rescheduledFromDate": None,
        "status": {"detailedState": "Postponed", "reason": "Inclement Weather"},
        "teams": {"away": {"team": {"abbreviation": "MIL"}},
                  "home": {"team": {"abbreviation": "PIT"}}},
    }, {
        "gamePk": 2, "rescheduledFromDate": "2026-07-10",
        "doubleHeader": "S", "gameNumber": 1,
        "status": {"detailedState": "Scheduled"},
        "teams": {"away": {"team": {"abbreviation": "ATH"}},
                  "home": {"team": {"abbreviation": "CWS"}}},
    }]
    rep = build_report("2026-07-11", fixture)
    assert rep["postponed"][0]["matchup"] == "MIL @ PIT"
    assert rep["postponed"][0]["reschedule_date"] == "2026-07-11"
    assert rep["makeups"][0]["matchup"] == "OAK @ CHW"      # code mapping
    assert rep["makeups"][0]["makeup_of"] == "2026-07-10"
    print("selftest OK: postponement + makeup extraction + code mapping")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return 0
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    d0 = datetime.date.fromisoformat(date)
    for d in (d0, d0 - datetime.timedelta(days=1)):
        ds = d.isoformat()
        try:
            write_report(build_report(ds, fetch_day(ds)))
        except Exception as e:
            print("postponed sidecar FAILED for %s: %r" % (ds, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
