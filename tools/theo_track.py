#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
theo_track.py -- backtest the "Theoretical chances" TOY vs actual outcomes.

DISPLAY ONLY / read-only. Faithfully re-computes the LIVE toy (the JS port in
docs/index.html) over historical diags, grades the toy's favored side vs the
final score, and writes docs/data/theo_track.json (record + calibration). It
NEVER touches the model, picks, parlay_builder, or the brain. Fully sandboxed:
any failure prints a warning and writes nothing rather than raising.

The toy: league PA prior tilted by the OPPOSING starter's K% + a tiny bounded
lineup-concentration offense nudge -> 24 base-out Markov inning sim -> 9-fold
convolution -> bullpen-xwOBA-gap leverage tilt -> P(home win), clamped 1-99%.
Inputs are the same three diag columns the live card uses.

Usage: python tools/theo_track.py [sims]      (default sims=8000 (stable estimate; live card draws 1200/load))
"""
import sys, os, csv, json, glob, math, random, datetime, urllib.request, re

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("THEO_TRACK_OUT") or os.path.join(ROOT, "docs", "data", "theo_track.json")
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-theotrack/1.0"}
CANON = {"CWS": "CHW", "AZ": "ARI", "ATH": "OAK", "WSN": "WSH", "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
LEAGUE = [0.690, 0.085, 0.140, 0.045, 0.004, 0.036]   # out,bb,1b,2b,3b,hr


def canon(x): return CANON.get(str(x).strip(), str(x).strip())


def _num(v):
    try:
        f = float(v); return f if math.isfinite(f) else None
    except Exception:
        return None


def _kpct(v):
    n = _num(v)
    if n is None: return 0.22
    if n > 1: n /= 100.0
    return min(max(n, 0.10), 0.40)


def _rates(oppK, offMod):
    pa = LEAGUE[:]; kT = oppK - 0.22
    pa[0] *= (1 + 1.4 * kT)
    for j in range(1, 6): pa[j] *= (1 - 0.9 * kT)
    m = max(min(offMod, 0.15), -0.15)
    pa[0] *= (1 - 0.6 * m); pa[5] *= (1 + 2.0 * m); pa[3] *= (1 + 1.2 * m); pa[2] *= (1 + 0.8 * m)
    s = 0.0
    for j in range(6):
        pa[j] = max(pa[j], 1e-9); s += pa[j]
    return [x / s for x in pa]


def _inning(pa, sims):
    cum = []; c = 0.0
    for p in pa: c += p; cum.append(c)
    maxR = 16; counts = [0.0] * (maxR + 1); rnd = random.random
    for _ in range(sims):
        on1 = on2 = on3 = False; outs = runs = 0
        while outs < 3:
            x = rnd(); ev = 0
            while ev < 5 and x > cum[ev]: ev += 1
            if ev == 0:
                outs += 1
            elif ev == 1:
                if on1 and on2 and on3: runs += 1
                elif on1 and on2: on3 = True
                elif on1: on2 = True
                on1 = True
            elif ev == 2:
                if on3: runs += 1
                on3 = on2; on2 = on1; on1 = True
            elif ev == 3:
                runs += (1 if on3 else 0) + (1 if on2 else 0)
                on3 = on1; on2 = True; on1 = False
            elif ev == 4:
                runs += (1 if on1 else 0) + (1 if on2 else 0) + (1 if on3 else 0)
                on1 = False; on2 = False; on3 = True
            else:
                runs += 1 + (1 if on1 else 0) + (1 if on2 else 0) + (1 if on3 else 0)
                on1 = on2 = on3 = False
        counts[min(runs, maxR)] += 1.0
    t = sum(counts); return [v / t for v in counts]


def _conv(a, b):
    out = [0.0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b): out[i + j] += ai * bj
    return out


def _game(inn):
    p = inn[:]
    for _ in range(8): p = _conv(p, inn)
    s = sum(p); return [v / s for v in p]


def _lev(pmf, supp):
    if not supp: return pmf
    out = [v * math.exp(-abs(supp) * 0.04 * i) for i, v in enumerate(pmf)]
    s = sum(out); return [v / s for v in out]


def _wp(h, a):
    ca = []; c = 0.0
    for v in a: c += v; ca.append(c)
    more = tie = 0.0
    for i, hi in enumerate(h):
        below = ca[i - 1] if i - 1 >= 0 else 0.0
        more += hi * below
        if i < len(a): tie += hi * a[i]
    return more + 0.5 * tie


def toy_wp_home(r, sims):
    homeK = _kpct(r.get("home_sp_k_pct")); awayK = _kpct(r.get("away_sp_k_pct"))
    hC = _num(r.get("home_lineup_concentration")); aC = _num(r.get("away_lineup_concentration"))
    hOff = ((hC - 0.5) * 0.10) if hC is not None else 0.0
    aOff = ((aC - 0.5) * 0.10) if aC is not None else 0.0
    homePA = _rates(awayK, hOff); awayPA = _rates(homeK, aOff)   # offense faces the OTHER starter
    gap = _num(r.get("hl_bullpen_xwoba_gap")) or 0.0
    homeInn = _inning(homePA, sims); awayInn = _inning(awayPA, sims)
    awayInn = _lev(awayInn, max(gap, 0) * 8); homeInn = _lev(homeInn, max(-gap, 0) * 8)
    wp = _wp(_game(homeInn), _game(awayInn))
    return min(max(wp, 0.01), 0.99)


def finals_range(start, end):
    url = "%s/schedule?sportId=1&startDate=%s&endDate=%s&hydrate=team,linescore" % (API, start, end)
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40))
    win = {}
    for d in j.get("dates", []):
        ds = d.get("date")
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final": continue
            t = g["teams"]; ls = g.get("linescore", {}).get("teams", {})
            a = canon(t["away"]["team"]["abbreviation"]); h = canon(t["home"]["team"]["abbreviation"])
            ar = (ls.get("away") or {}).get("runs"); hr = (ls.get("home") or {}).get("runs")
            if ar is None or hr is None: continue
            win[(ds, a, h)] = a if ar > hr else h
    return win


def main():
    sims = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("THEO_SIMS", "8000"))
    random.seed(7)   # deterministic track record (no per-run jitter)
    files = sorted(glob.glob(os.path.join(ROOT, "docs", "data", "picks_*_diag.csv")))
    dates = [re.search(r"picks_(\d{4}-\d{2}-\d{2})_diag", f).group(1) for f in files if re.search(r"picks_(\d{4}-\d{2}-\d{2})_diag", f)]
    if not dates:
        print("[theo_track] no diags found; skip"); return
    try:
        win = finals_range(min(dates), max(dates))
    except Exception as e:
        print("[theo_track] finals fetch failed: %s; skip" % e); return
    recs = []
    csv.field_size_limit(10 ** 7)
    for f in files:
        mm = re.search(r"picks_(\d{4}-\d{2}-\d{2})_diag", f)
        if not mm: continue
        date = mm.group(1)
        try:
            rows = list(csv.DictReader(open(f, encoding="utf-8", errors="replace")))
        except Exception:
            continue
        for r in rows:
            m = (r.get("matchup") or "").strip()
            if "@" not in m: continue
            away, home = [x.strip() for x in m.split("@")]
            w = win.get((date, canon(away), canon(home)))
            if not w: continue
            if _num(r.get("home_sp_k_pct")) is None and _num(r.get("away_sp_k_pct")) is None: continue
            wp = toy_wp_home(r, sims)
            fav = canon(home) if wp >= 0.5 else canon(away)
            conf = wp if wp >= 0.5 else 1 - wp
            recs.append((conf, fav == w))
    n = len(recs); wins = sum(1 for x in recs if x[1])
    if n == 0:
        print("[theo_track] 0 gradable games; skip"); return
    bins = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80), (0.80, 1.01)]
    cal = []
    for lo, hi in bins:
        sub = [x for x in recs if lo <= x[0] < hi]
        if sub:
            cal.append({"bin": "%d-%d" % (lo * 100, hi * 100), "n": len(sub),
                        "pred": round(sum(x[0] for x in sub) / len(sub), 3),
                        "actual": round(sum(1 for x in sub if x[1]) / len(sub), 3)})
    mean_pred = sum(x[0] for x in recs) / n
    hit = wins / n
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "window": {"start": min(dates), "end": max(dates)}, "n_games": n, "sims": sims,
           "record": {"w": wins, "l": n - wins, "pct": round(hit, 3)},
           "mean_pred": round(mean_pred, 3), "actual_hit": round(hit, 3),
           "overconfidence_pp": round((mean_pred - hit) * 100, 1),
           "calibration": cal}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("[theo_track] n=%d record=%d-%d (%.1f%%) mean_pred=%.1f%% overconf=%+.1fpp -> %s"
          % (n, wins, n - wins, hit * 100, mean_pred * 100, (mean_pred - hit) * 100, OUT))


if __name__ == "__main__":
    main()
