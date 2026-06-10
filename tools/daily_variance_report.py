#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/daily_variance_report.py
==============================
DAILY MICRO-CALIBRATION (Tier 1) -- the "Daily Variance Report".
Surfaces ONLY significant deviations from baseline for the upcoming slate:
  1. Roster & injury delta   (statsapi /transactions per MLB team: IL / recall / option / trade / DFA)
  2. SP verification         (confirmed? thin-sample? K%/xERA anomaly vs the diag baseline)
  3. Bullpen fatigue 48-72h  (relief pitch counts + consecutive days from recent boxscores)
  4. Lineup/platoon variance (SP handedness vs each side; lineup posted yet)

READ-ONLY: writes docs/data/daily_variance_<date>.md + .json. Does NOT touch grading/staking.
Pure stdlib + urllib (no extra deps). Defensive: any section that fails degrades to a note.
Run:  python tools/daily_variance_report.py [YYYY-MM-DD]   (default = today UTC)
"""
import os, sys, json, csv, datetime, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

API = "https://statsapi.mlb.com/api/v1"
def _get(path, **params):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
          "User-Agent": "mlb_edge-variance/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

# DENYLIST — codes that never change tonight's MLB active roster (suppressed from the
# headline but still counted + auditable). ASG = minor/rehab assignment shuffle; NUM = uniform #.
NOISE_CODES = {"ASG", "NUM"}
# Recognized roster-impacting codes -> surfaced + categorized in the headline.
KNOWN_ROSTER_CODES = {"SC", "TR", "REL", "SFA", "CU", "OPT", "SE", "DES", "OUT", "CLW", "RET", "SGN", "DFA", "RTN"}
TX_CATEGORY = {"SC": "status", "TR": "TRADE", "REL": "released", "SFA": "signed", "CU": "recalled",
               "OPT": "optioned", "SE": "selected", "DES": "DFA", "OUT": "outrighted", "CLW": "claimed",
               "RET": "retired", "SGN": "signed", "DFA": "DFA", "RTN": "returned"}
ROSTER_KW = ("injured list", "activated", "recalled", "optioned", "selected the contract",
             "designated", "traded", "claimed", "reinstated", "placed")
def _classify(code, desc):
    dl = desc.lower()
    if "injured list" in dl: return "IL"
    if "activated" in dl or "reinstated" in dl: return "activated"
    return TX_CATEGORY.get(code, code or "misc")

def slate(day):
    d = _get("/schedule", sportId=1, date=day, hydrate="probablePitcher,team")
    games = []
    for dd in d.get("dates", []):
        for g in dd.get("games", []):
            if (g.get("officialDate") or day) != day: continue
            t = g.get("teams", {})
            def side(s):
                tm = t.get(s, {}); team = tm.get("team", {}); pp = tm.get("probablePitcher") or {}
                return {"id": team.get("id"), "abbr": team.get("abbreviation"),
                        "name": team.get("teamName"), "sp_id": pp.get("id"), "sp": pp.get("fullName")}
            games.append({"pk": g.get("gamePk"), "away": side("away"), "home": side("home")})
    return games

def transactions_for(teams, day):
    """Keep-all (minus known noise). Structural leak guard on team-id, not description text.
    Unrecognized codes -> review bucket + self-documenting log (never silently dropped)."""
    start = (datetime.date.fromisoformat(day) - datetime.timedelta(days=2)).isoformat()
    mlb_ids = {tid for tid, _ in teams}
    kept, misc, leaks, unknown = [], [], [], {}
    suppressed = 0
    for tid, abbr in teams:
        try:
            d = _get("/transactions", teamId=tid, startDate=start, endDate=day)
        except Exception as e:
            misc.append({"team": abbr, "note": "transactions fetch failed (%r)" % e}); continue
        seen = set()
        for tx in d.get("transactions", []):
            code = (tx.get("typeCode") or "").strip()
            desc = (tx.get("description") or "").strip()
            txkey = tx.get("id") or (tx.get("effectiveDate"), desc)   # trades come back twice per team -> de-dupe
            if txkey in seen: continue
            seen.add(txkey)
            tteam = tx.get("team") or {}; tteam_id = tteam.get("id")
            # STRUCTURAL leak guard: a true MiLB leak = owning team-id outside the MLB slate set
            # (keyed on the integer id, NOT description text -> a legit "optioned to Triple-A X"
            #  MLB move is never mis-flagged just for naming a farm club).
            if tteam_id is not None and tteam_id not in mlb_ids:
                leaks.append({"team": abbr, "leak_team_id": tteam_id, "leak_team": tteam.get("name"),
                              "code": code, "desc": desc[:160]}); continue
            if code in NOISE_CODES:           # denylist: minor/cosmetic, suppress from headline
                suppressed += 1; continue
            row = {"team": abbr, "date": tx.get("effectiveDate") or tx.get("date"), "code": code,
                   "type": tx.get("typeDesc") or code, "cat": _classify(code, desc), "desc": desc[:200]}
            if code in KNOWN_ROSTER_CODES or any(k in desc.lower() for k in ROSTER_KW):
                kept.append(row)              # recognized roster move -> headline
            else:
                misc.append(row)              # KEEP-ALL: unrecognized code -> review bucket
                unknown[code] = unknown.get(code, 0) + 1
    if unknown:                               # telemetry: self-document novel codes
        try:
            os.makedirs("logs", exist_ok=True)
            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            with open("logs/unknown_tx_codes.log", "a", encoding="utf-8") as fh:
                for c, n in sorted(unknown.items()):
                    fh.write("%s\t%s\tcode=%r\tn=%d\n" % (day, ts, c, n))
        except Exception:
            pass
    return {"kept": kept, "misc": misc, "leaks": leaks, "suppressed": suppressed, "unknown_codes": unknown}

def _league_k_thresholds(default_hi=28.0, default_lo=15.0, max_age_days=21):
    """SP K% deviation gates RELATIVE to the current league baseline (Weekly Baseline Update).
    Graceful fallback to absolute defaults if the baseline file is missing or >max_age_days stale
    (so a 3-week-old file during the Japan freeze can't drive bad flags)."""
    try:
        b = json.load(open("docs/data/weekly_baseline.json", encoding="utf-8"))
        age = (datetime.date.today() - datetime.date.fromisoformat(b.get("generated", "")[:10])).days
        lk = b.get("windows", {}).get("14d", {}).get("pitching", {}).get("k_pct")
        if lk and 0 <= age <= max_age_days:
            return round(lk + 6.0, 1), round(lk - 7.0, 1), lk
    except Exception:
        pass
    return default_hi, default_lo, None


def sp_flags(games, diag):
    out = []
    hi, lo, lk = _league_k_thresholds()           # league-relative gates (fallback to 28/15)
    lgs = (" vs lg %.1f" % lk) if lk else ""
    for g in games:
        m = "%s @ %s" % (g["away"]["abbr"], g["home"]["abbr"])
        row = diag.get(m, {})
        for who, s in (("away", g["away"]), ("home", g["home"])):
            name = s.get("sp") or ""
            if not name:
                out.append({"game": m, "side": who, "sp": "TBD", "flag": "NOT CONFIRMED — probable SP not posted"}); continue
            tier = (row.get("tier") or "").upper()
            why = (row.get("why_skipped") or "")
            # PENDING is a row-level tier; flag only the side whose pitcher is actually named in the reason
            if "PENDING" in tier and name and name.split()[-1] in why:
                out.append({"game": m, "side": who, "sp": name, "flag": "thin sample / PENDING_SP_DATA — %s" % why[:90]})
            kp = row.get(who + "_sp_k_pct")
            try:
                kpf = float(kp)                       # diag K% is on a 0-100 scale (23.29 = 23.3%), NOT a fraction
                if kpf >= hi: out.append({"game": m, "side": who, "sp": name, "flag": "very high K%% (%.1f%%%s)" % (kpf, lgs)})
                elif 0 < kpf <= lo: out.append({"game": m, "side": who, "sp": name, "flag": "very low K%% (%.1f%%%s)" % (kpf, lgs)})
            except Exception: pass
    return out

def bullpen_fatigue(teams, day):
    """Fatigue = repeated/recent usage, NOT a single long outing. A lone bulk appearance from
    days ago means the reliever is RESTED; only flag a 1-app heavy outing if it was last night."""
    start = (datetime.date.fromisoformat(day) - datetime.timedelta(days=3)).isoformat()
    yday = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
    flags = []
    for tid, abbr in teams:
        usage = {}   # reliever -> {p:pitches, apps:set(pk), last:date}
        pk_date = {}
        try:
            sch = _get("/schedule", sportId=1, teamId=tid, startDate=start, endDate=yday)
            for dd in sch.get("dates", []):
                for g in dd.get("games", []):
                    if g.get("status", {}).get("abstractGameState") == "Final":
                        pk_date[g.get("gamePk")] = dd.get("date")
        except Exception:
            continue
        for pk in sorted(pk_date)[-3:]:
            gdate = pk_date.get(pk)
            try:
                box = _get("/game/%s/boxscore" % pk)
            except Exception:
                continue
            for ha in ("home", "away"):
                tm = box.get("teams", {}).get(ha, {})
                if tm.get("team", {}).get("id") != tid: continue
                players = tm.get("players", {}); order = tm.get("pitchers", [])
                for i, pid in enumerate(order):
                    if i == 0: continue  # starter of that game
                    pl = players.get("ID%s" % pid, {})
                    nm = pl.get("person", {}).get("fullName", "?")
                    npq = (((pl.get("stats") or {}).get("pitching") or {}).get("numberOfPitches"))
                    try: npq = int(npq)
                    except Exception: npq = 0
                    u = usage.setdefault(nm, {"p": 0, "apps": set(), "last": ""})
                    u["p"] += npq; u["apps"].add(pk)
                    if gdate and gdate > u["last"]: u["last"] = gdate
        for nm, u in usage.items():
            apps = len(u["apps"]); p = u["p"]
            flag = None
            if apps >= 3:
                flag = "overused — %d apps / %d pitches over last 3 games" % (apps, p)
            elif apps == 2 and p >= 35:
                flag = "%d apps / %d pitches over last 3 games — monitor" % (apps, p)
            elif apps == 1 and p >= 40 and u["last"] == yday:
                flag = "threw %d pitches last night — likely unavailable" % p
            if flag:
                flags.append({"team": abbr, "reliever": nm, "appearances_3d": apps,
                              "pitches_3d": p, "last": u["last"], "flag": flag})
    return flags

def platoon_notes(games):
    notes = []
    for g in games:
        for who, s, opp in (("home", g["home"], g["away"]), ("away", g["away"], g["home"])):
            sp = opp.get("sp_id")
            if not sp: continue
            try:
                hand = _get("/people/%s" % sp).get("people", [{}])[0].get("pitchHand", {}).get("code")
            except Exception:
                hand = None
            if hand:
                notes.append({"game": "%s @ %s" % (g["away"]["abbr"], g["home"]["abbr"]),
                              "team": s["abbr"], "note": "faces %sHP (%s)" % (hand, opp.get("sp") or "?")})
    return notes

def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    games = slate(day)
    teams = []
    for g in games:
        for s in (g["away"], g["home"]):
            if s["id"] and (s["id"], s["abbr"]) not in teams: teams.append((s["id"], s["abbr"]))
    # diag baseline (SP K%/tier) if today's diag exists
    diag = {}
    dp = "docs/data/picks_%s_diag.csv" % day
    if os.path.exists(dp):
        for r in csv.DictReader(open(dp, encoding="utf-8")): diag[(r.get("matchup") or "").strip()] = r

    report = {"date": day, "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "n_games": len(games), "roster_delta": [], "roster_review": [], "roster_leaks": [],
              "roster_suppressed": 0, "unknown_tx_codes": {}, "sp_flags": [], "bullpen_fatigue": [], "platoon": []}
    try:
        rd = transactions_for(teams, day)
        report["roster_delta"] = rd["kept"]; report["roster_review"] = rd["misc"]
        report["roster_leaks"] = rd["leaks"]; report["roster_suppressed"] = rd["suppressed"]
        report["unknown_tx_codes"] = rd["unknown_codes"]
    except Exception as e:
        report["roster_delta"] = [{"note": "roster section failed: %r" % e}]
    for sec, fn in (("sp_flags", lambda: sp_flags(games, diag)),
                    ("bullpen_fatigue", lambda: bullpen_fatigue(teams, day)),
                    ("platoon", lambda: platoon_notes(games))):
        try: report[sec] = fn()
        except Exception as e: report[sec] = [{"note": "%s section failed: %r" % (sec, e)}]

    os.makedirs("docs/data", exist_ok=True)
    _jp = "docs/data/daily_variance_%s.json" % day
    with open(_jp + ".tmp", "w", encoding="utf-8") as _fh:
        json.dump(report, _fh, indent=1)
    os.replace(_jp + ".tmp", _jp)  # atomic + explicit utf-8 (was platform-default)
    L = ["# Daily Variance Report — %s" % day,
         "_Generated %s · %d games · significant deviations only_" % (report["generated"], len(games)), ""]
    def sect(title, items, fmt):
        L.append("## %s" % title)
        if not items: L.append("_None flagged._"); L.append(""); return
        for it in items: L.append("- " + fmt(it))
        L.append("")
    sect("1 · Roster / Injury Delta", report["roster_delta"],
         lambda x: "**%s** [%s] — %s" % (x.get("team",""), x.get("cat", x.get("type","")), x.get("desc", x.get("note",""))))
    if report.get("roster_review"):
        sect("1b · Unrecognized codes (review — kept, never dropped)", report["roster_review"],
             lambda x: "**%s** code=%s — %s" % (x.get("team",""), x.get("code",""), x.get("desc", x.get("note",""))))
    if report.get("roster_leaks"):
        sect("WARN · MiLB leak guard (team-id outside slate MLB set)", report["roster_leaks"],
             lambda x: "%s: leak_team=%s (id %s) code=%s %s" % (x.get("team",""), x.get("leak_team",""), x.get("leak_team_id",""), x.get("code",""), x.get("desc","")))
    L.append("_Suppressed %d minor/cosmetic move(s) (ASG/NUM); %d unrecognized code-type(s) -> logs/unknown_tx_codes.log._" % (
        report.get("roster_suppressed", 0), len(report.get("unknown_tx_codes", {}) or {})))
    L.append("")
    sect("2 · Starting Pitcher Flags", report["sp_flags"],
         lambda x: "**%s** (%s SP %s): %s" % (x.get("game",""), x.get("side",""), x.get("sp",""), x.get("flag","")))
    sect("3 · Bullpen Fatigue (last 3 games)", report["bullpen_fatigue"],
         lambda x: "**%s** %s — %s" % (x.get("team",""), x.get("reliever",""), x.get("flag", x.get("note",""))))
    sect("4 · Lineup / Platoon", report["platoon"],
         lambda x: "%s — %s %s" % (x.get("game",""), x.get("team",""), x.get("note","")))
    _mp = "docs/data/daily_variance_%s.md" % day
    with open(_mp + ".tmp", "w", encoding="utf-8") as _fh:
        _fh.write("\n".join(L) + "\n")
    os.replace(_mp + ".tmp", _mp)  # atomic
    print("Daily Variance Report -> docs/data/daily_variance_%s.md (.json)" % day)
    print("  roster:%d  sp_flags:%d  bullpen_fatigue:%d  platoon:%d" %
          (len(report["roster_delta"]), len(report["sp_flags"]), len(report["bullpen_fatigue"]), len(report["platoon"])))

if __name__ == "__main__":
    main()
