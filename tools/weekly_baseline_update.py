#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/weekly_baseline_update.py — Tier-2 WEEKLY BASELINE UPDATE (READ-ONLY).

Computes CURRENT rolling league baselines (14d + 30d) from statsapi /teams/stats
(per-team splits -> summed = true league), plus a season xwOBA proxy from the Savant
CSV, then reports DRIFT vs the model's FROZEN priors (LG_* in mlb_edge/config.py)
WITHOUT modifying config, weights, or any execution state.

Writes (both read-only reference, published nightly/weekly):
  docs/data/weekly_baseline.json      -> consumed by tools/daily_variance_report.py for
                                         league-RELATIVE deviation thresholds
  docs/data/weekly_baseline_<date>.md -> human 'Weekly Baseline Update' (values + drift + why)

Freeze-safe: pure stdlib+urllib, no model mutation. Run weekly: python tools/weekly_baseline_update.py
"""
import os, re, sys, csv, json, datetime, collections, urllib.request, urllib.parse
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
API = "https://statsapi.mlb.com/api/v1"

def _get(path, **p):
    u = API + path + ("?" + urllib.parse.urlencode(p) if p else "")
    req = urllib.request.Request(u, headers={"Accept": "application/json", "User-Agent": "mlb_edge-baseline/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))

def _team_splits(group, start, end):
    d = _get("/teams/stats", stats="byDateRange", group=group, sportId="1", season=str(end.year),
             startDate=start.isoformat(), endDate=end.isoformat())
    return d.get("stats", [{}])[0].get("splits", [])

def league_pitching(start, end):
    agg = collections.Counter()
    for s in _team_splits("pitching", start, end):
        st = s.get("stat", {})
        for k in ("strikeOuts", "baseOnBalls", "homeRuns", "battersFaced", "inningsPitched", "earnedRuns"):
            try: agg[k] += float(st.get(k, 0) or 0)
            except Exception: pass
    bf = agg["battersFaced"] or 1.0; ip = agg["inningsPitched"] or 1.0
    return {"k_pct": round(100 * agg["strikeOuts"] / bf, 2), "bb_pct": round(100 * agg["baseOnBalls"] / bf, 2),
            "hr9": round(9 * agg["homeRuns"] / ip, 3), "era": round(9 * agg["earnedRuns"] / ip, 2),
            "bf": int(bf), "ip": round(ip, 1)}

def league_hitting(start, end):
    agg = collections.Counter()
    for s in _team_splits("hitting", start, end):
        st = s.get("stat", {})
        for k in ("hits", "atBats", "baseOnBalls", "hitByPitch", "sacFlies", "totalBases", "plateAppearances", "strikeOuts", "runs"):
            try: agg[k] += float(st.get(k, 0) or 0)
            except Exception: pass
    ab = agg["atBats"] or 1.0; pa = agg["plateAppearances"] or 1.0
    obpd = (agg["atBats"] + agg["baseOnBalls"] + agg["hitByPitch"] + agg["sacFlies"]) or 1.0
    return {"avg": round(agg["hits"] / ab, 3), "obp": round((agg["hits"] + agg["baseOnBalls"] + agg["hitByPitch"]) / obpd, 3),
            "slg": round(agg["totalBases"] / ab, 3), "k_pct": round(100 * agg["strikeOuts"] / pa, 2),
            "runs": int(agg["runs"]), "pa": int(pa)}

def power_ranking(start, end):
    rs, ra, gp = {}, {}, {}
    for s in _team_splits("hitting", start, end):
        ab = (s.get("team") or {}).get("name"); st = s.get("stat", {})
        if ab:
            try: rs[ab] = float(st.get("runs", 0) or 0); gp[ab] = float(st.get("gamesPlayed", 0) or 0)
            except Exception: pass
    for s in _team_splits("pitching", start, end):
        ab = (s.get("team") or {}).get("name"); st = s.get("stat", {})
        if ab:
            try: ra[ab] = float(st.get("runs", 0) or 0)
            except Exception: pass
    rows = []
    for ab in (set(rs) & set(ra)):
        g = gp.get(ab, 0) or 1.0
        rows.append([ab, round((rs[ab] - ra[ab]) / g, 2), int(rs.get(ab, 0)), int(ra.get(ab, 0))])
    rows.sort(key=lambda x: -x[1])
    return rows

def season_xwoba_proxy():
    p = "data/savant_hitters_2026.csv"
    if not os.path.exists(p): return None
    xw = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            v = float(r.get("xwoba"))
            if v > 0: xw.append(v)
        except Exception: pass
    if not xw: return None
    return {"xwoba_mean": round(sum(xw) / len(xw), 4), "n_hitters": len(xw)}

def config_priors():
    out = {}
    try:
        txt = open("mlb_edge/config.py", encoding="utf-8").read()
        for k in ("LG_K_PCT", "LG_BB_PCT", "LG_WOBA", "LG_XWOBA", "LG_HARDHIT_PCT", "LG_BULLPEN_XERA", "LG_BULLPEN_XWOBA"):
            m = re.search(r'^%s\s*(?::\s*float)?\s*=\s*([0-9.]+)' % k, txt, re.M)
            if m: out[k] = float(m.group(1))
    except Exception: pass
    return out

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now.date() - datetime.timedelta(days=1)   # complete days only
    s14 = end - datetime.timedelta(days=14); s30 = end - datetime.timedelta(days=30)
    rep = {"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_end": end.isoformat(),
           "windows": {}, "season": {}, "config_priors": config_priors(), "drift": {}, "power_ranking_14d": []}
    for tag, s in (("14d", s14), ("30d", s30)):
        try: rep["windows"][tag] = {"start": s.isoformat(), "pitching": league_pitching(s, end), "hitting": league_hitting(s, end)}
        except Exception as e: rep["windows"][tag] = {"error": repr(e)}
    try: rep["season"]["xwoba_proxy"] = season_xwoba_proxy()
    except Exception as e: rep["season"]["error"] = repr(e)
    try: rep["power_ranking_14d"] = power_ranking(s14, end)
    except Exception: rep["power_ranking_14d"] = []
    pri = rep["config_priors"]; w14 = rep["windows"].get("14d", {}).get("pitching", {})
    if pri.get("LG_K_PCT") and w14.get("k_pct"):
        rep["drift"]["k_pct"] = {"rolling14": w14["k_pct"], "prior": pri["LG_K_PCT"], "delta": round(w14["k_pct"] - pri["LG_K_PCT"], 2)}
    if pri.get("LG_BB_PCT") and w14.get("bb_pct"):
        rep["drift"]["bb_pct"] = {"rolling14": w14["bb_pct"], "prior": pri["LG_BB_PCT"], "delta": round(w14["bb_pct"] - pri["LG_BB_PCT"], 2)}

    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/weekly_baseline.json.tmp", "w", encoding="utf-8") as _fh:
        json.dump(rep, _fh, indent=1)
    os.replace("docs/data/weekly_baseline.json.tmp", "docs/data/weekly_baseline.json")  # atomic + explicit utf-8

    L = ["# Weekly Baseline Update — %s" % end.isoformat(),
         "_Generated %s · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_" % rep["generated"], ""]
    def fp(p): return ("K%% %.2f · BB%% %.2f · HR/9 %.3f · ERA %.2f" % (p.get("k_pct",0), p.get("bb_pct",0), p.get("hr9",0), p.get("era",0))) if p else "n/a"
    def fh(h): return ("AVG %.3f · OBP %.3f · SLG %.3f · K%% %.2f · %d R" % (h.get("avg",0), h.get("obp",0), h.get("slg",0), h.get("k_pct",0), h.get("runs",0))) if h else "n/a"
    L.append("## League pitching")
    for tag in ("14d", "30d"):
        w = rep["windows"].get(tag, {}); L.append("- **%s** (from %s): %s" % (tag, w.get("start","?"), fp(w.get("pitching"))))
    L.append(""); L.append("## League hitting")
    for tag in ("14d", "30d"):
        w = rep["windows"].get(tag, {}); L.append("- **%s**: %s" % (tag, fh(w.get("hitting"))))
    xwp = rep["season"].get("xwoba_proxy")
    L.append(""); L.append("## Season xwOBA (proxy)")
    L.append(("- mean xwOBA **%.4f** across %d qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_" % (xwp["xwoba_mean"], xwp["n_hitters"])) if xwp else "- _Savant CSV unavailable._")
    L.append(""); L.append("## Drift vs frozen model priors")
    if rep["drift"]:
        for k, v in rep["drift"].items():
            d = v["delta"]; verdict = "stable" if abs(d) <= (1.0 if k == "k_pct" else 0.7) else "**DRIFT**"
            L.append("- **%s**: rolling-14d %.2f vs prior %.2f → Δ%+.2f (%s)" % (k, v["rolling14"], v["prior"], d, verdict))
        L.append("")
        L.append("_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._")
    else:
        L.append("_priors unavailable._")
    pr = rep.get("power_ranking_14d") or []
    if pr:
        L.append(""); L.append("## 14-day power ranking (run differential / game)")
        for i, row in enumerate(pr, 1):
            ab, diff, rsv, rav = row
            L.append("- %2d. **%s** %+.2f R/G  (RS %d / RA %d)" % (i, ab, diff, rsv, rav))
    _mp = "docs/data/weekly_baseline_%s.md" % end.isoformat()
    with open(_mp + ".tmp", "w", encoding="utf-8") as _fh:
        _fh.write("\n".join(L) + "\n")
    os.replace(_mp + ".tmp", _mp)  # atomic
    print("Weekly Baseline Update -> docs/data/weekly_baseline.json + weekly_baseline_%s.md" % end.isoformat())
    if rep["drift"].get("k_pct"):
        kk = rep["drift"]["k_pct"]; print("  league K%% 14d=%.2f (prior %.2f, d=%+.2f) · power-ranking teams=%d" % (kk["rolling14"], kk["prior"], kk["delta"], len(pr)))

if __name__ == "__main__":
    main()
