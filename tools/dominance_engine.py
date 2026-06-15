#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dominance_engine.py -- read-only SP "ceiling" / Dominance flag sidecar (DISPLAY ONLY).

Companion to sp_hr_recency.py: that one is the FLOOR (HR-risk safeguards), this is
the CEILING (which starters take a game over). NEVER touches the XGBoost win/totals
model -- pure display, same freeze-safe class as the other sidecars.

PHASE 1 (this file, statsapi-ONLY -- no new external feed before the unattended trip):
Per user 2026-06-14, approximate the bat-missing signal with rolling-3-start K%
(correlates ~0.85 with CSW%), gated against the opponent lineup's strikeout-proneness
(season team K% -- a statsapi stand-in for collective chase/whiff rate).

  Rolling 3-start K%  = sum(strikeOuts)/sum(battersFaced) over last 3 starts < slate.
  Season K-BB%        = (SO-BB)/BF*100 (season split).
  xFIP                = statsapi sabermetrics (predictive ERA estimator, context only).
  Opp team K%         = team season strikeOuts/PA; opp_k_high if >= league_mean + 1.0pp.

FLAGS (2 of the user's 3 are buildable statsapi-only; #3 needs Savant -> July):
  ULTRA-DOMINANT  rolling K% >= 30 AND opponent whiff-prone (opp_k_high)
                  -> 10+ K / 7+ IP lean; hammer K-prop over; high-value DFS.
  HIGH-FLOOR ACE  rolling K% >= 28 AND season K-BB% >= 20
                  -> quality-start floor, low variance.
  HIGH-K LEAN     rolling K% >= 28 (neither of the above)
                  -> bat-missing arm; ceiling present, matchup not as soft.
  (MATCHUP NIGHTMARE = kill-pitch whiff% x opponent bottom-5 vs that pitch type ->
   needs Savant pitch-level data; pre-registered for the July build.)

Writes docs/data/dominance_<date>.json. FULLY SANDBOXED: every SP under its own
try/except; a missing/failed sidecar just means no ceiling chips render.

Usage:  python tools/dominance_engine.py [YYYY-MM-DD]
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
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-dom/1.0"}

WINDOW = 3
ROLL_K_ULTRA = 30.0     # rolling-3-start K% -> ultra-dominant gate (CSW>32 proxy)
ROLL_K_HIGH = 28.0      # rolling-3-start K% -> high-dominance arm (user: K%>28 ~ CSW)
KBB_FLOOR = 20.0        # season K-BB% -> high-floor ace gate
OPP_K_MARGIN = 1.0      # opp team K% >= league_mean + this -> whiff-prone opponent

CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def cn(x):
    return CANON.get(str(x).strip(), str(x).strip())


def _get(url, timeout=25, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(sleep)
    raise last


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _team_abbr_to_id(season):
    j = _get("%s/teams?sportId=1&season=%d" % (API, season))
    out = {}
    for t in j.get("teams", []):
        ab = cn(t.get("abbreviation") or "")
        if ab and t.get("id"):
            out[ab] = t["id"]
    return out


def _team_k_pct(season):
    """team id -> season batting K% (strikeOuts/PA), plus league mean."""
    j = _get("%s/teams/stats?stats=season&group=hitting&season=%d&sportId=1" % (API, season))
    out = {}
    for sp in j.get("stats", [{}])[0].get("splits", []):
        st = sp.get("stat") or {}
        tid = (sp.get("team") or {}).get("id")
        so = _fnum(st.get("strikeOuts")) or 0.0
        pa = _fnum(st.get("plateAppearances")) or 0.0
        if tid and pa:
            out[tid] = 100.0 * so / pa
    mean = statistics.mean(out.values()) if out else 22.0
    return out, mean


def _slate_sps(date):
    """[(sp_name, opp_team_abbr)] -- opp = the lineup batting against this SP."""
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = "picks_%s_diag.csv" % date
    if not os.path.exists(path):
        return []
    csv.field_size_limit(10 ** 7)
    out, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", (row.get("matchup") or ""))
            away_ab = cn(mm.group(1)) if mm else None
            home_ab = cn(mm.group(2)) if mm else None
            for k, opp in (("away_sp_name", home_ab), ("home_sp_name", away_ab)):
                nm = (row.get(k) or "").strip()
                if nm and nm.upper() != "TBD" and nm not in seen:
                    seen.add(nm)
                    out.append((nm, opp))
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


def _gamelog_and_season(pid, season):
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


def _xfip(pid, season):
    try:
        j = _get("%s/people/%s/stats?stats=sabermetrics&group=pitching&season=%d&sportId=1"
                 % (API, pid, season))
        sp = j.get("stats", [{}])[0].get("splits", [])
        if sp:
            return _fnum((sp[0].get("stat") or {}).get("xfip"))
    except Exception:
        pass
    return None


def _rolling_k(gl_cur, date):
    starts = []
    for s in gl_cur:
        st = s.get("stat") or {}
        try:
            gs = int(st.get("gamesStarted") or 0)
        except (TypeError, ValueError):
            gs = 0
        if gs >= 1 and (s.get("date") or "") < date:
            starts.append(st)
    starts = starts[-WINDOW:]
    if not starts:
        return None, 0
    so = sum(int(s.get("strikeOuts") or 0) for s in starts)
    bf = sum(int(s.get("battersFaced") or 0) for s in starts)
    return (100.0 * so / bf if bf else None), len(starts)


def _season_kbb(sea):
    if not sea:
        return None
    so = _fnum(sea.get("strikeOuts"))
    bb = _fnum(sea.get("baseOnBalls"))
    bf = _fnum(sea.get("battersFaced"))
    if so is None or bb is None or not bf:
        return None
    return 100.0 * (so - bb) / bf


def classify(roll_k, season_kbb, opp_k_high):
    """(flag, label, edge) or (None, None, None)."""
    if roll_k is None or roll_k < ROLL_K_HIGH:
        return None, None, None
    if roll_k >= ROLL_K_ULTRA and opp_k_high:
        return "ULTRA", "Ultra-Dominant", "10+ K / 7+ IP lean; hammer K-prop over; high-value DFS captain"
    if season_kbb is not None and season_kbb >= KBB_FLOOR:
        return "ACE", "High-Floor Ace", "quality-start floor (6+ IP, <=3 ER); low-variance play"
    return "LEAN", "High-K Lean", "bat-missing arm; ceiling present, opponent not as whiff-prone"


def sp_record(pid, season, date, opp_ab, opp_id, team_k, league_k):
    gl_cur, sea = _gamelog_and_season(pid, season)
    roll_k, n = _rolling_k(gl_cur, date)
    season_k = None
    if sea:
        so = _fnum(sea.get("strikeOuts"))
        bf = _fnum(sea.get("battersFaced"))
        season_k = (100.0 * so / bf) if (so is not None and bf) else None
    season_kbb = _season_kbb(sea)
    k9 = _fnum((sea or {}).get("strikeoutsPer9Inn"))
    xfip = _xfip(pid, season)
    opp_k = team_k.get(opp_id)
    opp_k_high = (opp_k is not None) and (opp_k >= league_k + OPP_K_MARGIN)
    flag, label, edge = classify(roll_k, season_kbb, opp_k_high)
    return {
        "id": pid, "opp": opp_ab,
        "starts": n,
        "rolling3_k_pct": round(roll_k, 1) if roll_k is not None else None,
        "season_k_pct": round(season_k, 1) if season_k is not None else None,
        "season_kbb": round(season_kbb, 1) if season_kbb is not None else None,
        "k9": round(k9, 2) if k9 is not None else None,
        "xfip": round(xfip, 2) if xfip is not None else None,
        "opp_k_pct": round(opp_k, 1) if opp_k is not None else None,
        "opp_k_high": opp_k_high,
        "league_k_mean": round(league_k, 1),
        "dom_flag": flag, "dom_label": label, "edge": edge,
        "csw_proxy": "rolling-3-start K% (~0.85 corr w/ CSW%); statsapi-only Phase 1",
    }


def build(date):
    season = int(date[:4])
    pairs = _slate_sps(date)
    names = [nm for nm, _ in pairs]
    name2id = _resolve_ids(names, season)
    try:
        abbr2id = _team_abbr_to_id(season)
    except Exception:
        abbr2id = {}
    try:
        team_k, league_k = _team_k_pct(season)
    except Exception:
        team_k, league_k = {}, 22.0
    out = {}
    for nm, opp_ab in pairs:
        pid = name2id.get(nm)
        if not pid:
            continue
        try:
            out[nm] = sp_record(pid, season, date, opp_ab, abbr2id.get(opp_ab), team_k, league_k)
        except Exception as e:
            out[nm] = {"dom_flag": None, "error": type(e).__name__}
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
               "schema": "v1-statsapi-proxy",
               "method": ("CEILING flags (DISPLAY-ONLY): rolling-3-start K%% (CSW proxy) gated vs "
                          "opponent team K%%. ULTRA>=30%%+whiff-prone opp; ACE>=28%%+K-BB%%>=20; "
                          "LEAN>=28%%. MatchupNightmare(kill-pitch/Savant)=July. statsapi-only, no new feed."),
               "sps": {}}
    try:
        sidecar["sps"] = build(date)
        flagged = [(nm, r) for nm, r in sidecar["sps"].items() if r.get("dom_flag")]
        print("SPs: %d | dominance-flagged: %d" % (len(sidecar["sps"]), len(flagged)))
        order = {"ULTRA": 0, "ACE": 1, "LEAN": 2}
        for nm, r in sorted(flagged, key=lambda x: order.get(x[1].get("dom_flag"), 9)):
            print("  %-22s %-16s rollK %.1f%%  K-BB %s  xFIP %s  oppK %s%s"
                  % (nm, r.get("dom_label"), r.get("rolling3_k_pct") or 0,
                     r.get("season_kbb"), r.get("xfip"), r.get("opp_k_pct"),
                     " [whiff-prone opp]" if r.get("opp_k_high") else ""))
    except Exception as e:
        print("DOM-FAIL %s: %s" % (type(e).__name__, e))
    outp = os.path.join("docs", "data", "dominance_%s.json" % date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1)
    os.replace(outp + ".tmp", outp)
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
