#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
platoon_enrichment.py -- SP handedness + platoon splits sidecar. DISPLAY ONLY.

For every probable/announced starter on the slate, fetches from statsapi:
  * throwing hand (R/L)
  * season splits vs RHB and vs LHB (IP, HR, BB, HBP, K)
and computes a per-side run-prevention number on the ERA scale.

NOTE ON "ERA vs L/R": true earned-run average cannot be split by batter
handedness (earned runs belong to innings, not batters) and no source
publishes it -- statsapi returns era=None on vs-hand splits for every
pitcher. The standard substitute is per-side FIP on the ERA scale:
    FIP_side = (13*HR + 3*(BB+HBP) - 2*K) / IP + cFIP
with cFIP calibrated so league FIP == league ERA for the season. That is
what ships in the era_vs_r / era_vs_l fields the dashboard renders as
"[ERA vs R: X.XX | ERA vs L: Y.YY]".

Writes docs/data/platoon_<date>.json (atomic). Keyed by SP name (diag join)
and by gamePk (exact). Batter handedness needs no fetch -- bat_side already
rides in the diag's top-5 batter JSON.

Usage: python tools/platoon_enrichment.py [YYYY-MM-DD]
Sandboxed: any failure prints a warning and writes nothing.
"""
import datetime
import json
import os
import sys
import urllib.request

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-platoon/1.0"}
FALLBACK_CFIP = 3.15
MIN_IP_SIDE = 3.0        # below this the number is noise; still shown, flagged


def _get(url, timeout=20):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout))


def _ip_float(ip_str):
    """'45.2' baseball notation -> 45 + 2/3."""
    try:
        s = str(ip_str)
        whole, _, frac = s.partition(".")
        return int(whole or 0) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(frac or "0", 0.0)
    except Exception:
        return 0.0


def _league_cfip(season):
    """cFIP = leagueERA - league FIP components; fallback constant on any miss."""
    try:
        j = _get("%s/teams/stats?season=%d&group=pitching&stats=season&sportIds=1"
                 % (API, season))
        hr = bb = hbp = k = 0
        ip = era_ip = 0.0
        era_w = 0.0
        for sp in (j.get("stats") or [{}])[0].get("splits", []):
            st = sp.get("stat", {})
            _ip = _ip_float(st.get("inningsPitched"))
            if not _ip:
                continue
            hr += int(st.get("homeRuns") or 0)
            bb += int(st.get("baseOnBalls") or 0)
            hbp += int(st.get("hitBatsmen") or 0)
            k += int(st.get("strikeOuts") or 0)
            ip += _ip
            try:
                era_w += float(st.get("era")) * _ip
                era_ip += _ip
            except (TypeError, ValueError):
                pass
        if ip < 100 or era_ip < 100:
            return FALLBACK_CFIP
        league_era = era_w / era_ip
        return round(league_era - (13 * hr + 3 * (bb + hbp) - 2 * k) / ip, 3)
    except Exception as e:
        print("[platoon] league cFIP fetch failed (%r); using %.2f" % (e, FALLBACK_CFIP))
        return FALLBACK_CFIP


def _sp_splits(pid, season, cfip):
    out = {"era_vs_r": None, "era_vs_l": None, "ip_vs_r": 0.0, "ip_vs_l": 0.0,
           "small_sample": False}
    j = _get("%s/people/%d/stats?stats=statSplits&group=pitching&season=%d&sitCodes=vr,vl"
             % (API, pid, season))
    for s in j.get("stats", []):
        for sp in s.get("splits", []):
            code = (sp.get("split") or {}).get("code")
            st = sp.get("stat", {})
            ip = _ip_float(st.get("inningsPitched"))
            if code not in ("vr", "vl") or ip <= 0:
                continue
            fip = (13 * int(st.get("homeRuns") or 0)
                   + 3 * (int(st.get("baseOnBalls") or 0) + int(st.get("hitBatsmen") or 0))
                   - 2 * int(st.get("strikeOuts") or 0)) / ip + cfip
            side = "r" if code == "vr" else "l"
            out["era_vs_" + side] = round(max(fip, 0.0), 2)
            out["ip_vs_" + side] = round(ip, 1)
    if 0 < min(out["ip_vs_r"] or 0, out["ip_vs_l"] or 0) < MIN_IP_SIDE:
        out["small_sample"] = True
    return out


def main():
    slate = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    season = int(slate[:4])
    try:
        sched = _get("%s/schedule?sportId=1&date=%s&hydrate=probablePitcher" % (API, slate))
    except Exception as e:
        print("[platoon] schedule fetch failed: %s; skip" % e)
        return
    games = [g for d in sched.get("dates", []) for g in d.get("games", [])]
    if not games:
        print("[platoon] no games for %s; skip" % slate)
        return
    cfip = _league_cfip(season)

    pitchers, by_pk = {}, {}
    cache = {}
    for g in sorted(games, key=lambda x: x.get("gameNumber") or 1):
        pk = g.get("gamePk")
        entry = {}
        for side in ("away", "home"):
            p = (g.get("teams", {}).get(side, {}) or {}).get("probablePitcher") or {}
            pid, name = p.get("id"), (p.get("fullName") or "").strip()
            if not pid or not name:
                continue
            if pid not in cache:
                rec = {"id": pid, "name": name, "hand": None}
                try:
                    people = _get("%s/people/%d" % (API, pid))
                    rec["hand"] = ((people.get("people") or [{}])[0]
                                   .get("pitchHand", {}) or {}).get("code")
                except Exception as e:
                    print("[platoon] hand fetch failed for %s: %s" % (name, e))
                try:
                    rec.update(_sp_splits(pid, season, cfip))
                except Exception as e:
                    print("[platoon] splits fetch failed for %s: %s" % (name, e))
                cache[pid] = rec
            pitchers[name] = cache[pid]
            entry[side + "_sp"] = name
        if pk and entry:
            by_pk[str(pk)] = entry

    if not pitchers:
        print("[platoon] no announced starters resolved; skip")
        return
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "date": slate, "season": season, "cfip": cfip,
           "basis": ("Per-side FIP on the ERA scale (13*HR+3*(BB+HBP)-2*K)/IP + cFIP; "
                     "true ERA is not defined for batter-hand splits."),
           "pitchers": pitchers, "by_pk": by_pk}
    outp = os.path.join(ROOT, "docs", "data", "platoon_%s.json" % slate)
    tmp = outp + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, outp)
    print("[platoon] wrote %d pitchers / %d games -> %s (cFIP=%.2f)"
          % (len(pitchers), len(by_pk), outp, cfip))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[platoon] WARN unexpected failure %r -- nothing written" % (e,))
