#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sp_hr_recency.py -- read-only SP HR-risk classifier sidecar (DISPLAY ONLY).

REWRITTEN 2026-06-14 to fix false-positive "EXTREME HR" tags on aces (e.g. Zack
Wheeler, 2.22 ERA / 1.11 HR9, was tagged EXTREME purely on a couple of multi-HR
starts). The old logic flagged on last-3-start HR/start ALONE, with no ERA or
opponent context. The new logic is an ERA-gated, opponent-aware, trend-confirmed
5-tier cascade. It NEVER touches the XGBoost win/totals model -- pure display.

CASCADE (short-circuits top-down; see memory project_sp_hr_recency_spec v2):
  Level 0  GOD TIER  season ERA <= 2.50  -> ABSOLUTE OVERRIDE. No HR tag at all.
                     Skip the opponent-HR check AND the trend check. A generational
                     /untouchable arm dictates the game; matchup HR is noise.
  Level 4  ACE/LOW   2.50 < ERA <= 3.40  -> allow only a "SMALL" lean, BLOCK
                     HEAVY/EXTREME no matter the matchup.
  Level 3  REGRESS   3.40 < ERA <= 4.50  -> factor opponent HR; if FREQUENT, flag
                     REGRESSION and run the last-3-start trend (escalate if worsening).
  Levels 1/2 HIGH    ERA > 4.50          -> fully vulnerable.
                       FREQUENT opp HR -> Level 1 SEVERE  (flag EXTREME)
                       MODERATE opp HR -> Level 2 MODERATE (flag HEAVY)
                       SMALL    opp HR -> low "ELEVATED (ERA)" note (flag SMALL)

OPPONENT HR (small-sample-safe): pitcher's HR + IP vs TODAY's opponent over a
3-year time-decayed window (weights 1.0 / 0.6 / 0.3, current->2yr-ago), pulled
from the gameLog by opponent id. Then Bayesian-shrunk toward the pitcher's season
HR/9 with PADDING_IP=20 anchor innings, so one 2-HR game can't blow up the rate.
  smoothed_hr9 = 9 * (wHR + (seasonHR9/9)*PAD) / (wIP + PAD)
  FREQUENT if smoothed > 1.50, SMALL if < 1.00, else MODERATE.
If the pitcher has never faced the opponent -> fall back to season HR/9 (labeled).

TREND (last-3-start, not calendar 7d -- a 7d window is usually 1 start): "DOWN"
if last-3 ERA > season ERA + 1.50, OR last-3 K-BB% drops >= 5pp vs season. (Velocity
/spin decay is the sharpest signal but Savant arsenal feeds are dead -> July note.)

Writes docs/data/sp_hr_recent_<date>.json. FULLY SANDBOXED: every SP under its own
try/except; a missing sidecar just means the frontend keeps season-HR/9 behavior.

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
UA = {"User-Agent": "mlb_edge-sphr/2.0"}

# ---- ERA tier cutoffs (locked w/ user 2026-06-14) ----
GOD_ERA = 2.50          # <= -> absolute override, no HR tag
ACE_ERA = 3.40          # <= -> allow only SMALL lean, block HEAVY/EXTREME
AVG_ERA = 4.50          # <= -> Average (factor matchup + trend); > -> High/vulnerable
# ---- opponent-HR (Bayesian shrinkage over 3yr time-decayed vs-team window) ----
PADDING_IP = 20.0       # anchor innings toward season HR/9
DECAY = {0: 1.0, 1: 0.6, 2: 0.3}   # seasons back -> weight
FREQ_HI = 1.50          # smoothed HR/9 > -> FREQUENT
FREQ_LO = 1.00          # smoothed HR/9 < -> SMALL ; between -> MODERATE
LG_HR9 = 1.20           # league-ish HR/9 anchor for the prob multiplier
# ---- last-3-start trend ----
WINDOW = 3
TREND_ERA_DELTA = 1.50  # last3 ERA > season + this -> trending down
TREND_KBB_DROP = 5.0    # last3 K-BB% <= season - this (pp) -> trending down
# ---- HR-prob multiplier caps by tier ----
MULT_LO, MULT_HI = 0.5, 3.5

CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def cn(x):
    return CANON.get(str(x).strip(), str(x).strip())


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


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _team_abbr_to_id(season):
    """canonical abbr -> team id (statsapi)."""
    j = _get("%s/teams?sportId=1&season=%d" % (API, season))
    out = {}
    for t in j.get("teams", []):
        ab = cn(t.get("abbreviation") or "")
        if ab and t.get("id"):
            out[ab] = t["id"]
    return out


def _slate_sps(date):
    """[(sp_name, opp_team_abbr)] -- opp = the team batting against this SP.
    away SP faces the home team; home SP faces the away team."""
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
    """(gameLog splits, season stat dict) for one season."""
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


def _gamelog_only(pid, season):
    url = ("%s/people/%s/stats?stats=gameLog&group=pitching&season=%d&sportId=1"
           % (API, pid, season))
    j = _get(url)
    for sg in j.get("stats", []):
        if (sg.get("type") or {}).get("displayName") == "gameLog":
            return sg.get("splits") or []
    return []


def _season_hr9(sea):
    if not sea:
        return None
    v = _fnum(sea.get("homeRunsPer9"))
    if v is not None:
        return v
    hr = _fnum(sea.get("homeRuns")) or 0.0
    ip = _ip_to_outs(sea.get("inningsPitched")) / 3.0
    return 9 * hr / ip if ip > 0 else None


def _season_kbb(sea):
    if not sea:
        return None
    so = _fnum(sea.get("strikeOuts"))
    bb = _fnum(sea.get("baseOnBalls"))
    bf = _fnum(sea.get("battersFaced"))
    if so is None or bb is None or not bf:
        return None
    return 100.0 * (so - bb) / bf


def _matchup_vs_opp(pid, season, opp_id):
    """3yr time-decayed (wHR, wIP) vs opp_id, + which seasons had data."""
    if not opp_id:
        return 0.0, 0.0, []
    whr = wip = 0.0
    used = []
    for back, wt in DECAY.items():
        yr = season - back
        try:
            gl = _gamelog_and_season(pid, yr)[0] if back == 0 else _gamelog_only(pid, yr)
        except Exception:
            continue
        shr = souts = 0
        for s in gl:
            if (s.get("opponent") or {}).get("id") == opp_id:
                st = s.get("stat") or {}
                shr += int(st.get("homeRuns") or 0)
                souts += _ip_to_outs(st.get("inningsPitched"))
        if souts > 0:
            ip = souts / 3.0
            whr += wt * shr
            wip += wt * ip
            used.append({"season": yr, "hr": shr, "ip": round(ip, 1), "w": wt})
    return whr, wip, used


def _smoothed_matchup_hr9(whr, wip, season_hr9):
    """Bayesian shrinkage toward season HR/9 with PADDING_IP anchor innings."""
    base = season_hr9 if season_hr9 is not None else LG_HR9
    num = whr + (base / 9.0) * PADDING_IP
    den = wip + PADDING_IP
    return 9.0 * num / den if den > 0 else base


def _last3_trend(gl_cur, date, season_era, season_kbb):
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
    if len(starts) < 2:
        return {"trend": "HOLD", "basis": "insufficient recent starts", "starts": len(starts),
                "l3_hr": None, "l3_er": None, "l3_ip": None, "l3_era": None,
                "l3_hr_per_start": None, "l3_kbb": None}
    n = len(starts)
    hr = sum(int(s.get("homeRuns") or 0) for s in starts)
    er = sum(int(s.get("earnedRuns") or 0) for s in starts)
    outs = sum(_ip_to_outs(s.get("inningsPitched")) for s in starts)
    so = sum(int(s.get("strikeOuts") or 0) for s in starts)
    bb = sum(int(s.get("baseOnBalls") or 0) for s in starts)
    bf = sum(int(s.get("battersFaced") or 0) for s in starts)
    ip = outs / 3.0
    l3_era = (9 * er / ip) if ip > 0 else None
    l3_kbb = (100.0 * (so - bb) / bf) if bf else None
    down = False
    reasons = []
    if l3_era is not None and season_era is not None and l3_era > season_era + TREND_ERA_DELTA:
        down = True
        reasons.append("L3 ERA %.2f vs season %.2f (+%.2f)" % (l3_era, season_era, l3_era - season_era))
    if l3_kbb is not None and season_kbb is not None and (l3_kbb - season_kbb) <= -TREND_KBB_DROP:
        down = True
        reasons.append("L3 K-BB%% %.1f vs season %.1f (%.1fpp)" % (l3_kbb, season_kbb, l3_kbb - season_kbb))
    if not reasons:
        reasons.append("holding form (L3 ERA %.2f vs season %.2f)"
                       % (l3_era if l3_era is not None else -1,
                          season_era if season_era is not None else -1))
    return {"trend": "DOWN" if down else "HOLD", "basis": "; ".join(reasons), "starts": n,
            "l3_hr": hr, "l3_er": er, "l3_ip": round(ip, 1),
            "l3_era": round(l3_era, 2) if l3_era is not None else None,
            "l3_hr_per_start": round(hr / n, 2),
            "l3_kbb": round(l3_kbb, 1) if l3_kbb is not None else None}


def _freq(smoothed):
    if smoothed > FREQ_HI:
        return "FREQUENT"
    if smoothed < FREQ_LO:
        return "SMALL"
    return "MODERATE"


def classify(season_era, era_tier, freq, smoothed, trend_down, season_hr9, has_matchup):
    """Return (risk_level, risk_label, flag, mult). flag in
    {None, SMALL, MODERATE, HEAVY, EXTREME}. Cascade per the docstring."""
    base_mult = (smoothed / LG_HR9) if smoothed else 1.0
    if trend_down:
        base_mult *= 1.15

    # Level 0 -- God Tier absolute override
    if era_tier == "GOD":
        return 0, "God Tier", None, 1.0

    # Level 4 -- Ace/Low: allow only a SMALL lean, block HEAVY/EXTREME
    if era_tier == "ACE":
        flag = "SMALL" if freq == "FREQUENT" else None
        return 4, "Ace / Low", flag, clamp(min(base_mult, 1.25), 0.6, 1.25)

    # Level 3 -- Average ERA: regression watch when frequent opp HR
    if era_tier == "AVERAGE":
        if freq == "FREQUENT":
            flag = "HEAVY" if trend_down else "MODERATE"
            return 3, ("Regression Warning (worsening)" if trend_down else "Regression Warning"), \
                flag, clamp(base_mult, 0.6, 2.3)
        if freq == "MODERATE":
            return 4, "Watch", "SMALL", clamp(base_mult, 0.6, 1.6)
        return 4, "Low", None, clamp(base_mult, 0.5, 1.3)

    # ERA > 4.50 -- High / fully vulnerable
    if freq == "FREQUENT":
        return 1, "Severe Risk", "EXTREME", clamp(base_mult, 1.0, MULT_HI)
    if freq == "MODERATE":
        return 2, "Moderate Risk", "HEAVY", clamp(base_mult, 0.8, 2.6)
    return 2, "Elevated (ERA)", "SMALL", clamp(base_mult, 0.6, 1.8)


def sp_record(pid, season, date, opp_ab, opp_id):
    gl_cur, sea = _gamelog_and_season(pid, season)
    season_era = _fnum((sea or {}).get("era"))
    season_hr9 = _season_hr9(sea)
    season_kbb = _season_kbb(sea)

    if season_era is None:
        era_tier = "AVERAGE"   # unknown ERA -> treat as Average (conservative, no override)
    elif season_era <= GOD_ERA:
        era_tier = "GOD"
    elif season_era <= ACE_ERA:
        era_tier = "ACE"
    elif season_era <= AVG_ERA:
        era_tier = "AVERAGE"
    else:
        era_tier = "HIGH"

    # Opponent matchup HR (skip entirely for God Tier -- pure override)
    if era_tier == "GOD":
        whr = wip = 0.0
        used = []
        smoothed = season_hr9 if season_hr9 is not None else LG_HR9
        freq = "SMALL"
        basis = "skipped (God Tier override)"
    else:
        whr, wip, used = _matchup_vs_opp(pid, season, opp_id)
        smoothed = _smoothed_matchup_hr9(whr, wip, season_hr9)
        freq = _freq(smoothed)
        basis = ("vs %s 3yr (%s)" % (opp_ab, ",".join("%d:%dHR/%.0fIP" % (u["season"], u["hr"], u["ip"]) for u in used))) \
            if used else ("no vs-%s history -> season HR/9 fallback" % (opp_ab or "opp"))

    # Trend (skip for God Tier)
    if era_tier == "GOD":
        tr = {"trend": "HOLD", "basis": "skipped (God Tier override)", "starts": 0,
              "l3_hr": None, "l3_er": None, "l3_ip": None, "l3_era": None,
              "l3_hr_per_start": None, "l3_kbb": None}
    else:
        tr = _last3_trend(gl_cur, date, season_era, season_kbb)
    trend_down = (tr["trend"] == "DOWN") and (era_tier in ("AVERAGE", "HIGH"))

    lvl, label, flag, mult = classify(season_era, era_tier, freq, smoothed,
                                      trend_down, season_hr9, bool(used))

    return {
        "id": pid,
        "opp": opp_ab, "opp_id": opp_id,
        "season_era": round(season_era, 2) if season_era is not None else None,
        "season_hr9": round(season_hr9, 2) if season_hr9 is not None else None,
        "season_kbb": round(season_kbb, 1) if season_kbb is not None else None,
        "era_tier": era_tier,
        # opponent matchup
        "matchup_w_hr": round(whr, 2), "matchup_w_ip": round(wip, 1),
        "matchup_seasons": used,
        "smoothed_matchup_hr9": round(smoothed, 2),
        "matchup_freq": freq, "matchup_basis": basis,
        "has_matchup": bool(used),
        # trend (last 3 starts)
        "starts": tr["starts"], "l3_era": tr["l3_era"], "l3_hr": tr["l3_hr"],
        "l3_hr_per_start": tr["l3_hr_per_start"], "l3_kbb": tr["l3_kbb"],
        "trend": tr["trend"], "trend_down": trend_down, "trend_basis": tr["basis"],
        # final classification
        "risk_level": lvl, "risk_label": label, "flag": flag,
        "mult": round(mult, 2),
        # legacy back-compat (older frontend builds read these)
        "hr_per_start": tr["l3_hr_per_start"], "era": tr["l3_era"],
        "er_spike": bool(season_era is not None and season_era > AVG_ERA and trend_down),
    }


def build(date):
    season = int(date[:4])
    pairs = _slate_sps(date)
    names = [nm for nm, _ in pairs]
    name2id = _resolve_ids(names, season)
    abbr2id = {}
    try:
        abbr2id = _team_abbr_to_id(season)
    except Exception:
        abbr2id = {}
    out = {}
    for nm, opp_ab in pairs:
        pid = name2id.get(nm)
        if not pid:
            continue
        try:
            out[nm] = sp_record(pid, season, date, opp_ab, abbr2id.get(opp_ab))
        except Exception as e:
            out[nm] = {"flag": None, "risk_level": None, "error": type(e).__name__}
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
               "schema": "v2-era-gated-cascade",
               "method": ("ERA-gated 5-tier cascade: GOD<=2.50 override / ACE<=3.40 cap-SMALL / "
                          "AVG<=4.50 matchup+trend / HIGH>4.50. Opp HR = 3yr-decayed(1/.6/.3) "
                          "vs-team gameLog, Bayesian-shrunk to season HR/9 (PAD 20IP); "
                          "FREQ>1.5 SMALL<1.0. Trend=last3 ERA>season+1.5 or K-BB%% drop>=5pp. DISPLAY-ONLY."),
               "sps": {}}
    try:
        sidecar["sps"] = build(date)
        flagged = [(nm, r) for nm, r in sidecar["sps"].items() if r.get("flag")]
        gods = [nm for nm, r in sidecar["sps"].items() if r.get("era_tier") == "GOD"]
        print("SPs: %d | flagged: %d | God-Tier overrides: %d %s"
              % (len(sidecar["sps"]), len(flagged), len(gods), gods))
        order = {"EXTREME": 0, "HEAVY": 1, "MODERATE": 2, "SMALL": 3}
        for nm, r in sorted(flagged, key=lambda x: order.get(x[1].get("flag"), 9)):
            print("  %-22s L%s %-22s flag=%-8s ERA %s tier=%-7s opp=%s smoothed %.2f (%s) trend=%s mult %.2f"
                  % (nm, r.get("risk_level"), r.get("risk_label"), r.get("flag"),
                     r.get("season_era"), r.get("era_tier"), r.get("opp"),
                     r.get("smoothed_matchup_hr9") or 0, r.get("matchup_freq"),
                     r.get("trend"), r.get("mult") or 0))
    except Exception as e:
        print("SP-HR-FAIL %s: %s" % (type(e).__name__, e))
    outp = os.path.join("docs", "data", "sp_hr_recent_%s.json" % date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1)
    os.replace(outp + ".tmp", outp)  # atomic: no torn sidecar on crash/AV-lock
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
