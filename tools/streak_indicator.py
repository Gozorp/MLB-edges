#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streak_indicator.py -- read-only 7d/14d hot-cold streak sidecar (teams + players).

DISPLAY ONLY. Never touches the model, weights, or predict.py -- a dashboard
texture layer like the HR-props / theoretical-chances chips. See memory
project_streak_indicator_spec for the locked formulas.

Writes docs/data/streaks_<date>.json. FULLY SANDBOXED: team and player sections
each run under their own try/except, so any failure degrades to a partial/empty
sidecar and NEVER raises into the nightly chain. A missing sidecar just means no
chips render -- predictions are unaffected.

"Hot" (user, 2026-06-05): high win consistency; dominant overall performance;
wins steady/sustained, NOT reliant on infrequent high-margin blowouts. The third
criterion is implemented by capping every game's margin at +/-5 runs across three
independent robust-margin estimators.

Usage:  python tools/streak_indicator.py [YYYY-MM-DD]
"""
import sys
import os
import csv
import json
import math
import time
import glob
import re
import statistics
import datetime
import urllib.request
import urllib.parse
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-streaks/1.0"}

CAP = 5.0
PYTHAG_EXP = 1.83
W_WIN, W_DOM = 0.45, 0.55
LG_OBP, LG_SLG, LG_OPS = 0.320, 0.395, 0.715
TEAM_MIN = {"7d": 4, "14d": 8}
PLAYER_MIN_PA = {"7d": 15, "14d": 30}


def _get(url, timeout=25, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(sleep)
    raise last


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def bucket(h):
    if h >= 0.40:
        return "HOT"
    if h >= 0.15:
        return "WARM"
    if h > -0.15:
        return "NEUTRAL"
    if h > -0.40:
        return "COOL"
    return "COLD"


def fetch_window_games(start, end):
    url = ("%s/schedule?sportId=1&startDate=%s&endDate=%s&hydrate=team,linescore"
           % (API, start, end))
    j = _get(url)
    games = []
    for d in j.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            ls = g.get("linescore", {}).get("teams", {})
            hr = (ls.get("home") or {}).get("runs")
            ar = (ls.get("away") or {}).get("runs")
            if hr is None or ar is None:
                continue
            t = g.get("teams", {})
            ha = ((t.get("home") or {}).get("team") or {}).get("abbreviation")
            aa = ((t.get("away") or {}).get("team") or {}).get("abbreviation")
            if not ha or not aa:
                continue
            gd = (g.get("gameDate", "") or "")[:10]
            games.append((gd, aa, int(ar), ha, int(hr)))
    return games


def team_entries(games):
    m = defaultdict(list)
    for gd, aa, ar, ha, hr in games:
        m[aa].append((gd, ar - hr, ar > hr, ar, hr))
        m[ha].append((gd, hr - ar, hr > ar, hr, ar))
    return m


def team_heat(entries):
    n = len(entries)
    wins = sum(1 for e in entries if e[2])
    win_pct = wins / n
    margins = [e[1] for e in entries]
    capped_mean = sum(clamp(x, -CAP, CAP) for x in margins) / n
    med = statistics.median(margins)
    crs = cra = 0.0
    for _, mg, won, rf, ra in entries:
        base = min(rf, ra)
        if mg > 0:
            crs += base + clamp(mg, 0, CAP)
            cra += base
        elif mg < 0:
            crs += base
            cra += base + clamp(-mg, 0, CAP)
        else:
            crs += base
            cra += base
    denom = crs ** PYTHAG_EXP + cra ** PYTHAG_EXP
    pythag = (crs ** PYTHAG_EXP / denom) if denom > 0 else 0.5
    c_prime = 2 * win_pct - 1
    dominance = ((capped_mean / CAP) + (clamp(med, -CAP, CAP) / CAP) + (2 * pythag - 1)) / 3.0
    h = W_WIN * c_prime + W_DOM * dominance
    return {"H": round(h, 3), "bucket": bucket(h), "games": n, "w": wins,
            "l": n - wins, "win_pct": round(win_pct, 3),
            "capped_margin": round(capped_mean, 2), "median_margin": med,
            "pythag": round(pythag, 3)}


def build_teams(end_date):
    end = datetime.date.fromisoformat(end_date)
    start14 = end - datetime.timedelta(days=13)
    start7 = end - datetime.timedelta(days=6)
    games = fetch_window_games(start14.isoformat(), end.isoformat())
    emap = team_entries(games)
    out = {}
    s7 = start7.isoformat()
    for tm, entries in emap.items():
        e7 = [e for e in entries if e[0] >= s7]
        rec = {}
        rec["14d"] = (team_heat(entries) if len(entries) >= TEAM_MIN["14d"]
                      else {"bucket": "INSUFF", "games": len(entries)})
        rec["7d"] = (team_heat(e7) if len(e7) >= TEAM_MIN["7d"]
                     else {"bucket": "INSUFF", "games": len(e7)})
        out[tm] = rec
    windows = {"7d": {"start": start7.isoformat(), "end": end.isoformat()},
               "14d": {"start": start14.isoformat(), "end": end.isoformat()}}
    return out, windows


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def _slate_batters(date):
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = "picks_%s_diag.csv" % date
    if not os.path.exists(path):
        return []
    csv.field_size_limit(10 ** 7)
    out, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", row.get("matchup", ""))
            if not mm:
                continue
            for side, tm in (("away_top_5_batters_json", mm.group(1)),
                             ("home_top_5_batters_json", mm.group(2))):
                try:
                    arr = json.loads(row.get(side) or "[]")
                except Exception:
                    arr = []
                for b in arr:
                    nm = (b.get("name") or "").strip()
                    if nm and (nm, tm) not in seen:
                        seen.add((nm, tm))
                        out.append((nm, tm))
    return out


def _resolve_ids(batters):
    j = _get("%s/sports/1/players?season=%d" % (API, datetime.date.today().year))
    idmap = defaultdict(list)
    for p in j.get("people", []):
        idmap[_norm(p.get("fullName"))].append(p.get("id"))
    out = {}
    for nm, tm in batters:
        cands = idmap.get(_norm(nm)) or []
        if cands:
            out[nm] = cands[0]
    return out


def _byd_season(ids, start, end, want_season):
    types = "byDateRange,season" if want_season else "byDateRange"
    out = {}
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        url = ("%s/people?personIds=%s&hydrate=stats(group=[hitting],type=[%s],"
               "startDate=%s,endDate=%s,sportId=1)"
               % (API, ",".join(str(x) for x in chunk), types, start, end))
        try:
            j = _get(url)
        except Exception:
            continue
        for p in j.get("people", []):
            rec = {}
            for sg in p.get("stats", []):
                dn = (sg.get("type") or {}).get("displayName")
                sp = sg.get("splits") or []
                if sp and dn in ("byDateRange", "season"):
                    rec["win" if dn == "byDateRange" else "season"] = sp[0].get("stat") or {}
            if rec:
                out[p.get("id")] = rec
    return out


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def player_heat(stat, season_ops):
    pa = int(_fnum(stat.get("plateAppearances")) or 0)
    obp, slg, ops, babip = (_fnum(stat.get("obp")), _fnum(stat.get("slg")),
                            _fnum(stat.get("ops")), _fnum(stat.get("babip")))
    if obp is None or slg is None:
        return None, pa
    # dominance vs league (OBP-leaning = steady on-base, the individual "not a fluke")
    form_lg = 0.60 * ((obp - LG_OBP) / 0.070) + 0.40 * ((slg - LG_SLG) / 0.110)
    # streak vs the player's own season baseline (the core hot/cold signal)
    own_z = 0.0
    if season_ops and ops is not None:
        own_z = clamp((ops - season_ops) / 0.200, -2, 2)
    h = math.tanh(0.45 * (0.35 * form_lg + 0.65 * own_z))
    if babip is not None and babip > 0.380 and h > 0:
        h *= 0.8
    return h, pa


def _team_abbr_to_id():
    j = _get("%s/teams?sportId=1&season=%d" % (API, datetime.date.today().year))
    out = {}
    for t in j.get("teams", []):
        ab = (t.get("abbreviation") or "").strip()
        if ab and t.get("id"):
            out[ab] = t["id"]
    return out


_ROSTER_ALIAS = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
                 "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def _slate_teams(date):
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = "picks_%s_diag.csv" % date
    if not os.path.exists(path):
        return []
    teams, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", row.get("matchup", ""))
            if not mm:
                continue
            for tm in (mm.group(1), mm.group(2)):
                if tm not in seen:
                    seen.add(tm); teams.append(tm)
    return teams


def _roster_batters(date):
    """Pre-lineup fallback: when the diag has no posted lineup, cover every
    active position player on the slate's teams so the dashboard's projected
    lineup is fully tagged. Display-only; never feeds the model."""
    teams = _slate_teams(date)
    if not teams:
        return []
    try:
        abbr2id = _team_abbr_to_id()
    except Exception:
        return []
    out, seen = [], set()
    for tm in teams:
        tid = abbr2id.get(tm) or abbr2id.get(_ROSTER_ALIAS.get(tm, tm))
        if not tid:
            continue
        try:
            j = _get("%s/teams/%d/roster?rosterType=active" % (API, tid))
        except Exception:
            continue
        for r in j.get("roster", []):
            if ((r.get("position") or {}).get("abbreviation") or "") == "P":
                continue
            nm = ((r.get("person") or {}).get("fullName") or "").strip()
            if nm and (nm, tm) not in seen:
                seen.add((nm, tm)); out.append((nm, tm))
    return out


def build_players(end_date, windows):
    if not windows:
        return {}
    batters = list(dict.fromkeys(_slate_batters(end_date) + _roster_batters(end_date)))
    if not batters:
        return {}
    name2id = _resolve_ids(batters)
    name2team = {nm: tm for nm, tm in batters}
    ids = sorted(set(name2id.values()))
    if not ids:
        return {}
    w7, w14 = windows["7d"], windows["14d"]
    win14 = _byd_season(ids, w14["start"], w14["end"], True)
    win7 = _byd_season(ids, w7["start"], w7["end"], False)
    out = {}
    for nm, pid in name2id.items():
        s14 = win14.get(pid, {})
        season_ops = _fnum((s14.get("season") or {}).get("ops")) if s14.get("season") else None
        rec = {"team": name2team.get(nm)}
        for label, data in (("7d", win7.get(pid, {})), ("14d", s14)):
            st = data.get("win")
            if not st:
                rec[label] = {"bucket": "INSUFF", "pa": 0}
                continue
            h, pa = player_heat(st, season_ops)
            if h is None or pa < PLAYER_MIN_PA[label]:
                rec[label] = {"bucket": "INSUFF", "pa": pa}
            else:
                rec[label] = {"H": round(h, 3), "bucket": bucket(h), "pa": pa,
                              "obp": st.get("obp"), "slg": st.get("slg"),
                              "ops": st.get("ops"), "hr": st.get("homeRuns")}
        out[nm] = rec
    return out


def resolve_slate_date(arg):
    if arg:
        return arg
    fs = sorted(glob.glob("docs/data/picks_*_diag.csv") + glob.glob("picks_*_diag.csv"),
                key=os.path.getmtime)
    if fs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fs[-1]))
        if m:
            return m.group(1)
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def main():
    date = resolve_slate_date(sys.argv[1] if len(sys.argv) > 1 else None)
    sidecar = {"date": date,
               "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "method": "win0.45+dom0.55; dom=mean(cappedMargin/5,medMargin/5,2*cappedPythag-1); cap5; exp1.83",
               "teams": {}, "players": {}, "windows": {}}
    try:
        teams, windows = build_teams(date)
        sidecar["teams"] = teams
        sidecar["windows"] = windows
        hot = [t for t, r in teams.items() if r.get("14d", {}).get("bucket") == "HOT"]
        cold = [t for t, r in teams.items() if r.get("14d", {}).get("bucket") == "COLD"]
        print("teams: %d  | 14d HOT: %s  | 14d COLD: %s" % (len(teams), hot, cold))
    except Exception as e:
        print("TEAM-FAIL %s: %s" % (type(e).__name__, e))
    try:
        sidecar["players"] = build_players(date, sidecar.get("windows"))
        print("players: %d" % len(sidecar["players"]))
    except Exception as e:
        print("PLAYER-FAIL %s: %s" % (type(e).__name__, e))
    outp = os.path.join("docs", "data", "streaks_%s.json" % date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1)
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
