#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_sp_features.py -- READ-ONLY retrospective backtest (NO model change).

Tests whether the new SP micro-features (rolling-3-start K%, season K-BB%, HR/9,
last-3-start ERA trend, xFIP) carry INCREMENTAL out-of-sample predictive value
beyond the frozen model's win probability. Pre-registered bar: SP_FEATURE_BACKTEST_PREREG.md.

Does NOT touch the model, weights, stake layer, or production chain. Pulls historical
diags for the model baseline + features-as-of-date (leakage-safe: gameLog strictly
before each slate) and statsapi finals for outcomes. Writes a clean results log.

Usage: python tools/backtest_sp_features.py
"""
import os, sys, csv, json, glob, re, time, math, datetime, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-bt/1.0"}
CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
BURN_IN = 120
RNG = np.random.default_rng(7)


def cn(x): return CANON.get(str(x).strip(), str(x).strip())


def _get(url, timeout=25, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e; time.sleep(sleep)
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


def _ip_outs(ip):
    f = _fnum(ip)
    if f is None:
        return 0
    w = int(f); fr = round((f - w) * 10)
    if fr > 2:
        fr = 0
    return w * 3 + fr


# ---------- games + baseline from diags ----------
def load_games():
    rows = []
    for f in sorted(glob.glob("docs/data/picks_*_diag.csv")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f)
        if not m:
            continue
        date = m.group(1)
        csv.field_size_limit(10 ** 7)
        for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
            fp = _fnum(r.get("full_prob"))
            if fp is None:
                fp = _fnum(r.get("pick_prob"))
            if fp is None:
                continue
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", (r.get("matchup") or ""))
            if not mm:
                continue
            away, home = cn(mm.group(1)), cn(mm.group(2))
            hsp = (r.get("home_sp_name") or "").strip()
            asp = (r.get("away_sp_name") or "").strip()
            if not hsp or not asp or hsp.upper() == "TBD" or asp.upper() == "TBD":
                continue
            side = (r.get("pick_side") or "").strip().lower()
            home_prob = fp if side == "home" else (1.0 - fp) if side == "away" else fp
            rows.append({"date": date, "away": away, "home": home,
                         "home_prob": min(0.9999, max(0.0001, home_prob)),
                         "home_sp": hsp, "away_sp": asp})
    return rows


# ---------- outcomes from statsapi finals ----------
_abbr = {}
def _team_abbr_map(season):
    if season in _abbr:
        return _abbr[season]
    m = {}
    try:
        j = _get("%s/teams?sportId=1&season=%d" % (API, season))
        for t in j.get("teams", []):
            if t.get("id"):
                m[t["id"]] = cn(t.get("abbreviation") or "")
    except Exception:
        pass
    _abbr[season] = m
    return m


def finals_for(date):
    out = {}
    season = int(date[:4])
    am = _team_abbr_map(season)
    try:
        j = _get("%s/schedule?sportId=1&date=%s&hydrate=team" % (API, date))
    except Exception:
        return out
    for d in j.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            t = g["teams"]
            hid = (t["home"]["team"] or {}).get("id"); aid = (t["away"]["team"] or {}).get("id")
            ha = cn(t["home"]["team"].get("abbreviation") or am.get(hid) or "")
            aa = cn(t["away"]["team"].get("abbreviation") or am.get(aid) or "")
            hs = (t["home"].get("score")); as_ = (t["away"].get("score"))
            if hs is None or as_ is None:
                continue
            out[(aa, ha)] = 1 if hs > as_ else 0
    return out


# ---------- features as-of date ----------
_idcache = {}
def resolve_id(name, season):
    key = season
    if key not in _idcache:
        j = _get("%s/sports/1/players?season=%d" % (API, season))
        m = defaultdict(list)
        for p in j.get("people", []):
            m[_norm(p.get("fullName"))].append(p.get("id"))
        _idcache[key] = m
    c = _idcache[key].get(_norm(name)) or []
    return c[0] if c else None


_glcache = {}
def gamelog_season(pid, season):
    k = (pid, season)
    if k in _glcache:
        return _glcache[k]
    try:
        j = _get("%s/people/%s/stats?stats=gameLog,season&group=pitching&season=%d&sportId=1" % (API, pid, season))
    except Exception:
        _glcache[k] = ([], None); return _glcache[k]
    gl, sea = [], None
    for sg in j.get("stats", []):
        dn = (sg.get("type") or {}).get("displayName")
        sp = sg.get("splits") or []
        if dn == "gameLog":
            gl = sp
        elif dn == "season" and sp:
            sea = sp[0].get("stat") or {}
    _glcache[k] = (gl, sea)
    return _glcache[k]


_xfcache = {}
def xfip(pid, season):
    k = (pid, season)
    if k in _xfcache:
        return _xfcache[k]
    v = None
    try:
        j = _get("%s/people/%s/stats?stats=sabermetrics&group=pitching&season=%d&sportId=1" % (API, pid, season))
        sp = j.get("stats", [{}])[0].get("splits", [])
        if sp:
            v = _fnum((sp[0].get("stat") or {}).get("xfip"))
    except Exception:
        v = None
    _xfcache[k] = v
    return v


def sp_features(name, season, date):
    """rolling3_k%, kbb%, hr9, l3_era_trend, xfip  (None if unavailable)."""
    pid = resolve_id(name, season)
    if not pid:
        return {}
    gl, sea = gamelog_season(pid, season)
    starts = []
    for s in gl:
        st = s.get("stat") or {}
        try:
            gs = int(st.get("gamesStarted") or 0)
        except (TypeError, ValueError):
            gs = 0
        if gs >= 1 and (s.get("date") or "") < date:
            starts.append(st)
    last3 = starts[-3:]
    f = {}
    if last3:
        so = sum(int(s.get("strikeOuts") or 0) for s in last3)
        bf = sum(int(s.get("battersFaced") or 0) for s in last3)
        outs = sum(_ip_outs(s.get("inningsPitched")) for s in last3)
        er = sum(int(s.get("earnedRuns") or 0) for s in last3)
        if bf:
            f["roll_k"] = 100.0 * so / bf
        ip = outs / 3.0
        l3_era = (9 * er / ip) if ip > 0 else None
    else:
        l3_era = None
    if sea:
        so = _fnum(sea.get("strikeOuts")); bb = _fnum(sea.get("baseOnBalls")); bf = _fnum(sea.get("battersFaced"))
        if so is not None and bb is not None and bf:
            f["kbb"] = 100.0 * (so - bb) / bf
        f["hr9"] = _fnum(sea.get("homeRunsPer9"))
        sera = _fnum(sea.get("era"))
        if l3_era is not None and sera is not None:
            f["l3_trend"] = l3_era - sera
    xf = xfip(pid, season)
    if xf is not None:
        f["xfip"] = xf
    return f


def diff_feats(date, home_sp, away_sp):
    season = int(date[:4])
    h = sp_features(home_sp, season, date)
    a = sp_features(away_sp, season, date)
    def d(key, home_minus_away=True):
        hv, av = h.get(key), a.get(key)
        if hv is None or av is None:
            return 0.0, False
        return (hv - av) if home_minus_away else (av - hv), True
    feats, full = {}, True
    feats["d_roll3_k"], ok = d("roll_k", True); full &= ok
    feats["d_kbb"], ok = d("kbb", True); full &= ok
    feats["d_hr9"], ok = d("hr9", False); full &= ok       # away - home (more away HR -> home edge)
    feats["d_l3_trend"], ok = d("l3_trend", False); full &= ok  # away fading -> home edge
    feats["d_xfip"], ok = d("xfip", False); full &= ok     # away worse xfip -> home edge
    return feats, full


FCOLS = ["d_roll3_k", "d_kbb", "d_hr9", "d_l3_trend", "d_xfip"]
EXPECT_SIGN = {c: +1 for c in FCOLS}  # all defined positive toward home win


def logit(p):
    return math.log(p / (1 - p))


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p):
    return float(np.mean((p - y) ** 2))


def main():
    log = []
    def out(s):
        print(s); log.append(s)

    out("=== SP micro-feature retrospective backtest (READ-ONLY) ===")
    out("run_utc %s" % datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    games = load_games()
    out("diag games (both SP + prob): %d" % len(games))

    # attach outcomes
    fin = {}
    dates = sorted(set(g["date"] for g in games))
    for dt in dates:
        fin[dt] = finals_for(dt)
    data = []
    for g in games:
        y = fin.get(g["date"], {}).get((g["away"], g["home"]))
        if y is None:
            continue
        feats, full = diff_feats(g["date"], g["home_sp"], g["away_sp"])
        data.append({**g, "y": y, "feats": feats, "full": full})
    data.sort(key=lambda r: r["date"])
    n = len(data)
    nfull = sum(1 for r in data if r["full"])
    out("scored games: %d  (full-feature: %d, partial-imputed: %d)" % (n, nfull, n - nfull))
    if n < BURN_IN + 20:
        out("INSUFFICIENT scored games for walk-forward (need > %d). ABORT." % (BURN_IN + 20))
        _write(log); return

    # walk-forward expanding by date
    uniq = sorted(set(r["date"] for r in data))
    y_all, p0_all, p1_all = [], [], []
    coefs = []
    for dt in uniq:
        train = [r for r in data if r["date"] < dt]
        test = [r for r in data if r["date"] == dt]
        if len(train) < BURN_IN or not test:
            continue
        Xtr0 = np.array([[logit(r["home_prob"])] for r in train])
        Xtr1 = np.array([[logit(r["home_prob"])] + [r["feats"][c] for c in FCOLS] for r in train])
        ytr = np.array([r["y"] for r in train])
        if len(set(ytr.tolist())) < 2:
            continue
        sc0 = StandardScaler().fit(Xtr0); sc1 = StandardScaler().fit(Xtr1)
        m0 = LogisticRegression(C=1.0, max_iter=1000).fit(sc0.transform(Xtr0), ytr)
        m1 = LogisticRegression(C=1.0, max_iter=1000).fit(sc1.transform(Xtr1), ytr)
        coefs.append(m1.coef_[0][1:])  # feature coefs (skip logit term)
        Xte0 = np.array([[logit(r["home_prob"])] for r in test])
        Xte1 = np.array([[logit(r["home_prob"])] + [r["feats"][c] for c in FCOLS] for r in test])
        p0 = m0.predict_proba(sc0.transform(Xte0))[:, 1]
        p1 = m1.predict_proba(sc1.transform(Xte1))[:, 1]
        for r, a, b in zip(test, p0, p1):
            y_all.append(r["y"]); p0_all.append(a); p1_all.append(b)

    y = np.array(y_all); p0 = np.array(p0_all); p1 = np.array(p1_all)
    no = len(y)
    out("\nOOS scored predictions: %d  (burn-in %d)" % (no, BURN_IN))
    if no < 30:
        out("Too few OOS predictions. ABORT."); _write(log); return
    ll0, ll1 = ll(y, p0), ll(y, p1)
    delta = ll0 - ll1
    out("\n-- PRIMARY: log-loss --")
    out("  B0 (model only)      log-loss = %.5f" % ll0)
    out("  B1 (model+features)  log-loss = %.5f" % ll1)
    out("  delta (B0-B1)        = %+.5f   (PASS needs >= +0.00200 AND CI excludes 0)" % delta)

    # bootstrap CI of delta
    idx = np.arange(no); deltas = []
    for _ in range(1000):
        s = RNG.choice(idx, size=no, replace=True)
        deltas.append(ll(y[s], p0[s]) - ll(y[s], p1[s]))
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    out("  bootstrap 95%% CI     = [%+.5f, %+.5f]   excludes_0=%s" % (lo, hi, (lo > 0 or hi < 0)))

    out("\n-- SECONDARY (report only) --")
    out("  Brier  B0=%.5f  B1=%.5f  delta=%+.5f" % (brier(y, p0), brier(y, p1), brier(y, p0) - brier(y, p1)))
    try:
        from sklearn.metrics import roc_auc_score
        out("  AUC    B0=%.4f  B1=%.4f" % (roc_auc_score(y, p0), roc_auc_score(y, p1)))
    except Exception:
        pass

    out("\n-- SIGN CHECK (walk-forward-averaged feature coefs; expect all > 0) --")
    mc = np.mean(np.array(coefs), axis=0)
    signs_ok = True
    for c, v in zip(FCOLS, mc):
        ok = (v > 0) == (EXPECT_SIGN[c] > 0)
        signs_ok &= ok
        out("  %-12s coef=%+.4f  expect+  %s" % (c, v, "OK" if ok else "WRONG-SIGN"))

    out("\n-- TAIL CHECK (confident preds |p-0.5|>0.30) --")
    def tail_err(p):
        conf = np.abs(p - 0.5) > 0.30
        if conf.sum() == 0:
            return None, 0
        pred = (p[conf] >= 0.5).astype(int)
        return float(np.mean(pred != y[conf])), int(conf.sum())
    e0, n0c = tail_err(p0); e1, n1c = tail_err(p1)
    worst0 = float(np.max(-(y * np.log(np.clip(p0, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p0, 1e-6, 1)))))
    worst1 = float(np.max(-(y * np.log(np.clip(p1, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p1, 1e-6, 1)))))
    out("  B0 conf-err=%s (n=%d)  B1 conf-err=%s (n=%d)" % (
        ("%.3f" % e0 if e0 is not None else "NA"), n0c,
        ("%.3f" % e1 if e1 is not None else "NA"), n1c))
    out("  worst single-game log-loss  B0=%.3f  B1=%.3f  (B1<=B0+0.05 required)" % (worst0, worst1))
    tail_ok = (e1 is None or e0 is None or e1 <= e0 + 1e-9) and (worst1 <= worst0 + 0.05)

    # verdict
    ll_ok = (delta >= 0.002) and (lo > 0)
    out("\n=== VERDICT (vs pre-registered bar) ===")
    out("  1 log-loss>=+0.002 & CI excludes 0 : %s" % ll_ok)
    out("  2 all feature signs correct        : %s" % signs_ok)
    out("  3 no tail-variance inflation       : %s" % tail_ok)
    if ll_ok and signs_ok and tail_ok:
        v = "PASS -> PROMISING/GREENLIT for full-power July re-test, then gated retrain"
    elif delta < 0 or not signs_ok:
        v = "KILL -> harm or wrong-sign; do not pursue this feature set"
    else:
        v = "NULL -> inconclusive (within noise); re-test in July with more data"
    out("  OUTCOME: " + v)
    out("\nNote: n~300 is a PILOT; minimum detectable delta ~0.01-0.02, so a true")
    out("0.002-0.005 ROI-scale effect can read NULL here (see prereg power caveat).")
    _write(log)


def _write(log):
    os.makedirs("logs", exist_ok=True)
    fn = "logs/backtest_sp_features_%s.log" % datetime.date.today().isoformat()
    with open(fn, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    print("\nwrote %s" % fn)


if __name__ == "__main__":
    main()
