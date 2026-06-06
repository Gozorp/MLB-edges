#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sp_hr_recency.py -- read-only SP HR-prone RECENCY sidecar (DISPLAY ONLY).

Expands the dashboard HR-props heuristic (docs/index.html _hrProbability) with a
short-term recency layer for each slate starter: HR allowed + ER over the last 3
starts BEFORE the slate date. NEVER touches the XGBoost win/totals model. See
memory project_sp_hr_recency_spec for the locked formulas.

Writes docs/data/sp_hr_recent_<date>.json. FULLY SANDBOXED: every SP is computed
under its own try/except; a missing sidecar just means the frontend keeps its
existing season-HR/9 behavior. Predictions are unaffected.

Window = last 3 starts strictly BEFORE the slate date (afternoon games are
already in the gameLog -- including them would leak the game being predicted).

Usage:  python tools/sp_hr_recency.py [YYYY-MM-DD]
"""
import sys
import os
import csv
import json
import re
import time
import glob
import datetime
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-sphr/1.0"}

LG_HR_PER_START = 0.75
ERA_SPIKE = 6.5
ER_MULT_CAP = 1.6
ER_MULT_SLOPE = 0.12
MULT_LO, MULT_HI = 0.5, 3.5
WINDOW = 3


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


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def _ip_to_outs(ip):
    try:
        f = float(ip)
    except (TypeError, ValueError):
        return 0
    whole = int(f)
    frac = round((f - whole) * 10)
    if frac > 2:
        frac = 0
    return whole * 3 + frac


def _slate_sps(date):
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = "picks_%s_diag.csv" % date
    if not os.path.exists(path):
        return []
    csv.field_size_limit(10 ** 7)
    out, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for k in ("home_sp_name", "away_sp_name"):
                nm = (row.get(k) or "").strip()
                if nm and nm.upper() != "TBD" and nm not in seen:
                    seen.add(nm)
                    out.append(nm)
    return out


def _resolve_ids(names, season):
    j = _get("%s/sports/1/players?season=%d" % (API, season))
    idmap = defaultdict(list)
    for p in j.get("people", []):
        idmap[_norm(p.get("fullName"))].append(p.get("id"))
    out = {}
    for nm in names:
        c = idmap.get(_norm(nm)) or []
        if c:
            out[nm] = c[0]
    return out


def _gamelog_season(pid, season):
    url = ("%s/people/%s/stats?stats=gameLog,season&group=pitching&season=%d&sportId=1"
           % (API, pid, season))
    j = _get(url)
    gl, sea = [], None
    for sg in j.get("stats", []):
        dn = (sg.get("type") or {}).get("displayName")
        sp = sg.get("splits") or []
        if dn == "gameLog":
            gl = sp
        elif dn == "season" and sp:
            sea = sp[0].get("stat") or {}
    return gl, sea


def _season_hr9(sea):
    if not sea:
        return None
    v = sea.get("homeRunsPer9")
    try:
        if v is not None:
            return float(v)
    except (TypeError, ValueError):
        pass
    try:
        hr = float(sea.get("homeRuns") or 0)
        ip = _ip_to_outs(sea.get("inningsPitched")) / 3.0
        return 9 * hr / ip if ip > 0 else None
    except Exception:
        return None


def sp_recency(pid, season, date):
    gl, sea = _gamelog_season(pid, season)
    starts = []
    for sp in gl:
        st = sp.get("stat") or {}
        try:
            gs = int(st.get("gamesStarted") or 0)
        except (TypeError, ValueError):
            gs = 0
        if gs >= 1 and (sp.get("date") or "") < date:
            starts.append(st)
    starts = starts[-WINDOW:]
    if not starts:
        return {"insuff": True, "flag": None}
    n = len(starts)
    hr = sum(int(s.get("homeRuns") or 0) for s in starts)
    er = sum(int(s.get("earnedRuns") or 0) for s in starts)
    outs = sum(_ip_to_outs(s.get("inningsPitched")) for s in starts)
    ip = outs / 3.0
    hr_ps = hr / n
    era = (9 * er / ip) if ip > 0 else None
    flag = "EXTREME" if hr_ps >= 2.0 else ("HEAVY" if hr_ps >= 1.0 else None)
    er_spike = era is not None and era >= ERA_SPIKE
    hr_mult = hr_ps / LG_HR_PER_START
    er_mult = min(ER_MULT_CAP, 1.0 + ER_MULT_SLOPE * (era - ERA_SPIKE)) if er_spike else 1.0
    recency_factor = hr_mult * er_mult
    shr9 = _season_hr9(sea)
    season_factor = clamp((shr9 / 1.2) if shr9 else 1.0, 0.5, 2.0)
    mult = clamp(0.75 * recency_factor + 0.25 * season_factor, MULT_LO, MULT_HI)
    return {"id": pid, "starts": n, "hr": hr, "er": er, "ip": round(ip, 1),
            "hr_per_start": round(hr_ps, 2),
            "era": round(era, 2) if era is not None else None,
            "flag": flag, "er_spike": er_spike,
            "season_hr9": round(shr9, 2) if shr9 else None,
            "mult": round(mult, 2)}


def build(date):
    season = int(date[:4])
    names = _slate_sps(date)
    name2id = _resolve_ids(names, season)
    out = {}
    for nm in names:
        pid = name2id.get(nm)
        if not pid:
            continue
        try:
            out[nm] = sp_recency(pid, season, date)
        except Exception as e:
            out[nm] = {"flag": None, "error": type(e).__name__}
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
               "method": "last3starts<slate; mult=clamp(0.75*(hrPS/0.75*erMult)+0.25*seasonHR9factor,0.5,3.5); HEAVY>=1 EXTREME>=2 spike ERA>=6.5",
               "sps": {}}
    try:
        sidecar["sps"] = build(date)
        flagged = [(nm, r.get("flag"), r.get("hr_per_start"), r.get("era"), r.get("mult"))
                   for nm, r in sidecar["sps"].items() if r.get("flag")]
        print("SPs: %d | flagged HR-prone: %d" % (len(sidecar["sps"]), len(flagged)))
        for nm, fl, hps, era, mult in sorted(flagged, key=lambda x: -(x[4] or 0)):
            print("  %-22s %-8s %.2f HR/start  ERA %s  mult %.2f" % (nm, fl, hps, era, mult))
    except Exception as e:
        print("SP-HR-FAIL %s: %s" % (type(e).__name__, e))
    outp = os.path.join("docs", "data", "sp_hr_recent_%s.json" % date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1)
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
