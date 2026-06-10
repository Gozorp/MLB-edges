#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
team_tiers.py -- season-to-date team-strength tiers (DISPLAY ONLY, read-only).
Pulls MLB standings (record + run differential), ranks by an equal-weight
z-score blend of run differential and winning %, and sorts all 30 teams into
Elite / Above Average / Average / Below Average / Poor by fixed z thresholds.
Writes docs/data/team_tiers.json. Sandboxed: any failure writes nothing and
never raises into the chain. Never touches the model/picks.
Usage: python tools/team_tiers.py
"""
import os, json, math, datetime, urllib.request, statistics

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("TEAM_TIERS_OUT") or os.path.join(ROOT, "docs", "data", "team_tiers.json")
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-teamtiers/1.0"}
# composite = 0.5*z(runDiff) + 0.5*z(winPct); thresholds set at the natural breaks.
TIERS = [("Elite", 1.00), ("Above Average", 0.30), ("Average", -0.15), ("Below Average", -0.75), ("Poor", -99.0)]

def _get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))

def _pythag(rs, ra):
    try:
        a = float(rs) ** 1.83; b = float(ra) ** 1.83
        return a / (a + b) if (a + b) else 0.5
    except Exception:
        return None

def _rd_desc(rd):
    if rd >= 80: return "a dominant run margin"
    if rd >= 30: return "a strong run margin"
    if rd >= 10: return "a solidly positive run margin"
    if rd >= -10: return "a roughly even run margin"
    if rd >= -30: return "a negative run margin"
    if rd >= -60: return "a poor run margin"
    return "a worst-in-class run margin"

def _luck(pct, py):
    if py is None: return ""
    d = pct - py
    if d >= 0.04: return ", winning above what that margin implies"
    if d <= -0.04: return ", winning below what that margin implies"
    return ""

def main():
    yr = datetime.datetime.now(datetime.timezone.utc).year
    try:
        j = _get("%s/standings?leagueId=103,104&season=%d&standingsTypes=regularSeason&hydrate=team" % (API, yr))
    except Exception as e:
        print("[team_tiers] standings fetch failed: %s; skip" % e); return
    rows = []
    for rec in j.get("records", []):
        for tr in rec.get("teamRecords", []):
            t = tr.get("team") or {}
            try:
                rows.append({"name": t.get("name"), "abbr": t.get("abbreviation"),
                             "w": int(tr.get("wins")), "l": int(tr.get("losses")),
                             "pct": float(tr.get("winningPercentage")),
                             "rs": int(tr.get("runsScored")), "ra": int(tr.get("runsAllowed")),
                             "rd": int(tr.get("runDifferential"))})
            except Exception:
                continue
    if len(rows) < 20:
        print("[team_tiers] only %d teams; skip" % len(rows)); return
    rds = [r["rd"] for r in rows]; pcts = [r["pct"] for r in rows]
    mrd, srd = statistics.mean(rds), (statistics.pstdev(rds) or 1.0)
    mp, sp = statistics.mean(pcts), (statistics.pstdev(pcts) or 1.0)
    for r in rows:
        r["z"] = 0.5 * ((r["rd"] - mrd) / srd) + 0.5 * ((r["pct"] - mp) / sp)
    rows.sort(key=lambda r: r["z"], reverse=True)
    def tier_of(z):
        for name, lo in TIERS:
            if z >= lo: return name
        return "Poor"
    tiers = {name: [] for name, _ in TIERS}
    for r in rows:
        rationale = "%d-%d (.%03d), %+d run differential -- %s%s." % (
            r["w"], r["l"], round(r["pct"] * 1000), r["rd"], _rd_desc(r["rd"]),
            _luck(r["pct"], _pythag(r["rs"], r["ra"])))
        tiers[tier_of(r["z"])].append({"name": r["name"], "abbr": r["abbr"], "w": r["w"], "l": r["l"],
                                       "pct": round(r["pct"], 3), "rd": r["rd"], "rationale": rationale})
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "season": yr,
           "basis": "Equal-weight z-score blend of run differential and winning %, season-to-date.",
           "tier_order": [n for n, _ in TIERS],
           "tiers": tiers}
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(OUT + ".tmp", OUT)  # atomic: no torn sidecar on crash/AV-lock
    print("[team_tiers] wrote %d teams -> %s" % (sum(len(v) for v in tiers.values()), OUT))
    for n, _ in TIERS:
        print("  %-14s %d: %s" % (n, len(tiers[n]), ", ".join(t["abbr"] for t in tiers[n])))

if __name__ == "__main__":
    main()
