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

# ---- MMR layer (DISPLAY-ONLY): expected-vs-actual over a rolling window ----
# Rule-9 starting guesses; tune by eyeball. dz=1 -> ~56% expectancy; the
# best-vs-worst spread (~3.6z) -> ~70%, matching MLB parity.
MMR_WINDOW_D = 14
MMR_HALFLIFE_D = 7.0
MMR_K = 0.18
MMR_CLAMP = 0.60
MMR_SCALE = 10.0
HYST_MARGIN = 0.05
HYST_RUNS = 2

def _expected(za, zb):
    return 1.0 / (1.0 + 10.0 ** (-(za - zb) / MMR_SCALE))

def _recent_finals(now_utc):
    d2 = now_utc.date()
    d1 = d2 - datetime.timedelta(days=MMR_WINDOW_D)
    j = _get("%s/schedule?sportId=1&startDate=%s&endDate=%s&hydrate=team,linescore"
             % (API, d1.isoformat(), d2.isoformat()))
    out = []
    for d in j.get("dates", []):
        for g in d.get("games", []):
            if (g.get("gameType") or "R") != "R":
                continue
            if ((g.get("status") or {}).get("abstractGameState")) != "Final":
                continue
            ls = ((g.get("linescore") or {}).get("teams")) or {}
            hruns = (ls.get("home") or {}).get("runs")
            aruns = (ls.get("away") or {}).get("runs")
            if hruns is None or aruns is None:
                continue
            t = g.get("teams") or {}
            ha = (((t.get("home") or {}).get("team")) or {}).get("abbreviation")
            aa = (((t.get("away") or {}).get("team")) or {}).get("abbreviation")
            if ha and aa:
                out.append((d.get("date"), aa, int(aruns), ha, int(hruns)))
    return out

def _prev_tiers(path):
    """Continuity from the previously published output: {abbr: {tier, pending}}."""
    try:
        with open(path, encoding="utf-8") as f:
            pj = json.load(f)
        prev = {}
        for tname, arr in (pj.get("tiers") or {}).items():
            for t in arr or []:
                if t.get("abbr"):
                    prev[t["abbr"]] = {"tier": tname, "pending": t.get("pending")}
        return prev
    except Exception:
        return {}

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
    TIER_NAMES = [n for n, _ in TIERS]
    def tier_of(z):
        for name, lo in TIERS:
            if z >= lo: return name
        return "Poor"

    # ---- MMR: expected-vs-actual surprises over the rolling window ----
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    zmap = {r["abbr"]: r["z"] for r in rows}
    mmr = {ab: 0.0 for ab in zmap}
    events = {ab: [] for ab in zmap}
    try:
        for gd, aa, aruns, ha, hruns in _recent_finals(now_utc):
            if aa not in zmap or ha not in zmap:
                continue
            try:
                age = (now_utc.date() - datetime.date.fromisoformat(gd)).days
            except Exception:
                age = MMR_WINDOW_D
            dec = 0.5 ** (max(0, age) / MMR_HALFLIFE_D)
            e_away = _expected(zmap[aa], zmap[ha])
            act = 1.0 if aruns > hruns else 0.0
            wgt = 1.0 + 0.25 * min(3.0, abs(zmap[aa] - zmap[ha]))
            s_away = (act - e_away) * wgt * dec   # zero-sum: home gets -s_away
            mmr[aa] += MMR_K * s_away
            mmr[ha] -= MMR_K * s_away
            if abs(s_away) >= 0.10:
                if act == 1.0 and e_away < 0.45:
                    events[aa].append("upset W vs %s (%s)" % (ha, gd[5:]))
                    events[ha].append("upset L vs %s (%s)" % (aa, gd[5:]))
                elif act == 0.0 and e_away > 0.55:
                    events[ha].append("upset W vs %s (%s)" % (aa, gd[5:]))
                    events[aa].append("upset L vs %s (%s)" % (ha, gd[5:]))
    except Exception as e:
        print("[team_tiers] mmr window unavailable (%s); adjustments zeroed" % e)
    for ab in mmr:
        mmr[ab] = max(-MMR_CLAMP, min(MMR_CLAMP, mmr[ab]))

    # ---- hysteresis: previous published tier + pending counters ----
    prev = _prev_tiers(OUT)
    tiers = {name: [] for name, _ in TIERS}
    movements = []
    for r in rows:
        ab = r["abbr"]
        adj = mmr.get(ab, 0.0)
        eff = r["z"] + adj
        nat = tier_of(eff)
        p = prev.get(ab) or {}
        ptier = p.get("tier") if p.get("tier") in TIER_NAMES else None
        pend = p.get("pending") if isinstance(p.get("pending"), dict) else None
        movement, moved_from, pending_out = None, None, None
        if ptier is None:
            tier_final = nat
        elif nat == ptier:
            tier_final = ptier
        else:
            pi = TIER_NAMES.index(ptier)
            up = TIER_NAMES.index(nat) < pi
            if up:
                clear = eff >= TIERS[pi - 1][1] + HYST_MARGIN
            else:
                clear = eff <= TIERS[pi][1] - HYST_MARGIN
            direction = "up" if up else "down"
            runs = (pend.get("runs", 0) + 1) if (clear and pend and pend.get("dir") == direction) else (1 if clear else 0)
            if clear and runs >= HYST_RUNS:
                tier_final = TIER_NAMES[pi - 1] if up else TIER_NAMES[pi + 1]
                movement = "promoted" if up else "demoted"
                moved_from = ptier
                movements.append("%s %s: %s -> %s (eff %+0.2f, mmr %+0.2f)"
                                 % (ab, movement, ptier, tier_final, eff, adj))
            else:
                tier_final = ptier
                if clear:
                    pending_out = {"dir": direction, "runs": runs}
        rationale = "%d-%d (.%03d), %+d run differential -- %s%s." % (
            r["w"], r["l"], round(r["pct"] * 1000), r["rd"], _rd_desc(r["rd"]),
            _luck(r["pct"], _pythag(r["rs"], r["ra"])))
        if adj >= 0.15:
            rationale += " Results running above tier expectation (MMR %+0.2f)." % adj
        elif adj <= -0.15:
            rationale += " Results running below tier expectation (MMR %+0.2f)." % adj
        entry = {"name": r["name"], "abbr": ab, "w": r["w"], "l": r["l"],
                 "pct": round(r["pct"], 3), "rd": r["rd"], "rationale": rationale,
                 "z": round(r["z"], 3), "mmr": round(adj, 3), "eff": round(eff, 3)}
        if movement:
            entry["movement"] = movement
            entry["moved_from"] = moved_from
        if pending_out:
            entry["pending"] = pending_out
        if events.get(ab):
            entry["upsets"] = events[ab][:2]
        tiers[tier_final].append(entry)
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "season": yr,
           "basis": "Equal-weight z-score blend of run differential and winning %, season-to-date.",
           "mmr_basis": ("Dynamic layer: 14d expected-vs-actual (Elo expectancy on composite z, "
                         "scale 10), upset-weighted, 7d half-life, K=0.18, clamp 0.60z; tier flips "
                         "need boundary +/-0.05 for 2 consecutive runs, one step per run. "
                         "Display-only starting-guess parameters; the model never reads tiers."),
           "movements": movements,
           "tier_order": [n for n, _ in TIERS],
           "tiers": tiers}
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(OUT + ".tmp", OUT)  # atomic: no torn sidecar on crash/AV-lock
    print("[team_tiers] wrote %d teams -> %s" % (sum(len(v) for v in tiers.values()), OUT))
    for n, _ in TIERS:
        print("  %-14s %d: %s" % (n, len(tiers[n]), ", ".join(t["abbr"] for t in tiers[n])))
    print("  movements: %s" % ("; ".join(movements) if movements else "none (hysteresis holding)"))

if __name__ == "__main__":
    main()
