#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spread_projection.py -- "The Spread" (projected run differential). DISPLAY ONLY.

A decoupled post-processing overlay: AFTER the frozen model picks the winner,
this forecasts the expected margin of victory for the favored team. It NEVER
touches the model, picks, parlay_builder, or the brain -- it only reads the
already-published diag + statsapi and writes docs/data/spread_<date>.json.
Fully sandboxed: any failure prints a warning and writes nothing.

Direction is ALWAYS the model's pick; magnitude is anchored on the model's win
probability (logit curve) so the spread can never contradict the pick, then
scaled by the three requested input families:
  1. Historical run production -> season run-differential gap (statsapi standings)
  2. Aggregate roster offense  -> last-14d run-diff + runs-scored gap (statsapi)
  3. Player-level hitting eff.  -> starting-lineup mean xwOBA gap (diag batters JSON)
  (+ opposing-starter texture  -> SP K% gap, from the diag)
The model's projected total (pred_runs_mc) is split by the spread into a
projected final score. The constants below are DISPLAY knobs, not model weights.

Usage: python tools/spread_projection.py [YYYY-MM-DD]
"""
import sys, os, csv, json, math, datetime, urllib.request

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-spread/1.0"}
CANON = {"CWS": "CHW", "AZ": "ARI", "ATH": "OAK", "WSN": "WSH", "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}

# ---- display heuristic knobs (NOT model weights) ----
# 2026-07-17 recalibration (TOTALS_MARGIN_RECAL_2026-07-17.md, n=488 graded
# games): pick_prob->margin is flat-to-inverted OOS and the median favored
# margin is +1, so the logit curve is re-anchored near "+1" with a gentle
# slope and hard-clamped -- the old K_BASE=1.5 / max 6.5 overshot badly
# (overlay MAE 3.58, exact-margin hit 7.9% vs 14.1% for flat +1).
# Values below are fallbacks; data/state/totals_margin_calibration.json wins.
K_BASE = 0.45           # logit coefficient (was 1.5)
M_INTERCEPT = 0.85      # anchors p~0.58 at ~+1 run
COEF = {"rd_season": 0.18, "rd_recent": 0.14, "off_recent": 0.10, "xwoba": 0.12, "sp": 0.10}
SCALE_MIN, SCALE_MAX = 0.9, 1.1
SPREAD_MIN, SPREAD_MAX = 0.5, 2.5
DEFAULT_TOTAL = 8.6
CAL_PATH = os.path.join("data", "state", "totals_margin_calibration.json")


def _load_cal():
    """Fitted margin curve + totals bands; silently fall back to constants."""
    try:
        with open(os.path.join(ROOT, CAL_PATH), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _totals_cal_lookup(slate):
    """(away, home, occurrence) -> calibrated-total row from
    picks_totals_<slate>.csv (written by tools/totals_overlay.py).
    DH-safe: the nth CSV row for a matchup is game n (schedule order),
    matching the nth diag row."""
    out = {}
    path = os.path.join(ROOT, "picks_totals_%s.csv" % slate)
    if not os.path.exists(path):
        path = os.path.join(ROOT, "docs", "data", "picks_totals_%s.csv" % slate)
    occ = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("pred_runs_cal"):
                    continue
                pair = (canon(r.get("away_team", "")), canon(r.get("home_team", "")))
                n = occ.get(pair, 0)
                occ[pair] = n + 1
                out[pair + (n,)] = r
    except Exception:
        pass
    return out


def canon(x): return CANON.get(str(x).strip(), str(x).strip())


def _num(v):
    try:
        f = float(v); return f if math.isfinite(f) else None
    except Exception:
        return None


def _clip(x, lo, hi): return lo if x < lo else hi if x > hi else x


def _standings(season):
    url = "%s/standings?leagueId=103,104&season=%s&standingsTypes=regularSeason&hydrate=team" % (API, season)
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40))
    out = {}
    for rec in j.get("records", []):
        for t in rec.get("teamRecords", []):
            ab = canon(((t.get("team") or {}).get("abbreviation")) or "")
            rs = _num(t.get("runsScored")); ra = _num(t.get("runsAllowed"))
            gp = _num(t.get("gamesPlayed"))
            if not gp:
                w = _num(t.get("wins")); l = _num(t.get("losses"))
                if w is not None and l is not None:
                    gp = w + l
            if ab and rs is not None and ra is not None and gp:
                out[ab] = {"rsg": rs / gp, "rag": ra / gp, "rdg": (rs - ra) / gp}
    return out


def _recent(start, end):
    url = "%s/schedule?sportId=1&startDate=%s&endDate=%s&hydrate=team,linescore" % (API, start, end)
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40))
    agg = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            t = g.get("teams") or {}
            ls = (g.get("linescore") or {}).get("teams") or {}
            a = canon(((t.get("away") or {}).get("team") or {}).get("abbreviation") or "")
            h = canon(((t.get("home") or {}).get("team") or {}).get("abbreviation") or "")
            ar = _num((ls.get("away") or {}).get("runs")); hr = _num((ls.get("home") or {}).get("runs"))
            if not a or not h or ar is None or hr is None:
                continue
            for tm, rf, rag in ((a, ar, hr), (h, hr, ar)):
                s = agg.setdefault(tm, {"rs": 0.0, "ra": 0.0, "g": 0})
                s["rs"] += rf; s["ra"] += rag; s["g"] += 1
    out = {}
    for tm, s in agg.items():
        if s["g"] > 0:
            out[tm] = {"rsg": s["rs"] / s["g"], "rag": s["ra"] / s["g"], "rdg": (s["rs"] - s["ra"]) / s["g"], "g": s["g"]}
    return out


def _lineup_xwoba(raw):
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        return None
    vals = [_num(b.get("xwoba")) for b in arr if isinstance(b, dict)]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def main():
    slate = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    season = slate[:4]
    diag = os.path.join(ROOT, "docs", "data", "picks_%s_diag.csv" % slate)
    if not os.path.exists(diag):
        print("[spread] no diag for %s; skip" % slate); return
    try:
        csv.field_size_limit(10 ** 7)
        rows = list(csv.DictReader(open(diag, encoding="utf-8", errors="replace")))
    except Exception as e:
        print("[spread] diag read failed: %s; skip" % e); return
    try:
        season_st = _standings(season)
    except Exception as e:
        print("[spread] standings fetch failed: %s; continuing without" % e); season_st = {}
    try:
        d0 = datetime.date.fromisoformat(slate)
        start = (d0 - datetime.timedelta(days=14)).isoformat()
        end = (d0 - datetime.timedelta(days=1)).isoformat()
        recent_st = _recent(start, end)
    except Exception as e:
        print("[spread] recent fetch failed: %s; continuing without" % e); recent_st = {}

    cal = _load_cal()
    mcal = (cal or {}).get("margin") or {}
    curve = mcal.get("curve") or {}
    m_int = curve.get("intercept", M_INTERCEPT)
    m_coef = curve.get("logit_coef", K_BASE)
    sp_lo = curve.get("clip_lo", SPREAD_MIN); sp_hi = curve.get("clip_hi", SPREAD_MAX)
    sc_lo, sc_hi = (mcal.get("scale_range") or [SCALE_MIN, SCALE_MAX])[:2]
    mpm = mcal.get("most_probable_margin") or {}
    mpm_val = int(mpm.get("value", 1)); mpm_prob = mpm.get("prob")
    tot_lookup = _totals_cal_lookup(slate)
    print("[spread] calibration: %s | totals rows with cal: %d"
          % ("fitted %s" % (cal or {}).get("fitted_utc") if cal else "fallback constants", len(tot_lookup)))

    games = {}
    occ_seen = {}   # DH-safe: nth diag row for a matchup pairs with game n
    for r in rows:
        m = (r.get("matchup") or "").strip()
        if "@" not in m:
            continue
        away, home = [x.strip() for x in m.split("@")]
        occ = occ_seen.get(m, 0)
        occ_seen[m] = occ + 1
        pick = (r.get("pick") or "").strip()
        p = _num(r.get("pick_prob")) or _num(r.get("p_model"))
        if not pick or pick == "TBD" or p is None:
            continue
        p = _clip(p, 0.5001, 0.999)              # pick-side prob, >= 0.5 by construction
        fav = pick
        fav_is_home = (canon(fav) == canon(home))
        opp = away if fav_is_home else home
        fc, oc = canon(fav), canon(opp)
        base = m_int + m_coef * math.log(p / (1 - p))

        def gap(tbl, key, scale):
            a = (tbl.get(fc) or {}).get(key); b = (tbl.get(oc) or {}).get(key)
            if a is None or b is None:
                return 0.0
            return _clip((a - b) / scale, -1.0, 1.0)

        rd_season = gap(season_st, "rdg", 1.5)
        rd_recent = gap(recent_st, "rdg", 2.0)
        off_recent = gap(recent_st, "rsg", 2.0)
        xw_home = _lineup_xwoba(r.get("home_top_5_batters_json"))
        xw_away = _lineup_xwoba(r.get("away_top_5_batters_json"))
        xw_fav = xw_home if fav_is_home else xw_away
        xw_opp = xw_away if fav_is_home else xw_home
        xwoba_g = _clip((xw_fav - xw_opp) / 0.04, -1, 1) if (xw_fav is not None and xw_opp is not None) else 0.0
        k_home = _num(r.get("home_sp_k_pct")); k_away = _num(r.get("away_sp_k_pct"))
        k_fav = k_home if fav_is_home else k_away
        k_opp = k_away if fav_is_home else k_home
        sp_g = _clip(((k_fav - k_opp) / 100.0) / 0.10, -1, 1) if (k_fav is not None and k_opp is not None) else 0.0

        scale = (1.0 + COEF["rd_season"] * rd_season + COEF["rd_recent"] * rd_recent
                 + COEF["off_recent"] * off_recent + COEF["xwoba"] * xwoba_g + COEF["sp"] * sp_g)
        scale = _clip(scale, sc_lo, sc_hi)
        spread = _clip(base * scale, sp_lo, sp_hi)

        # calibrated market-blend total (totals_overlay.py) beats the raw MC
        # total by ~0.6 runs MAE OOS; fall back to MC then to the constant.
        tcal = tot_lookup.get((canon(away), canon(home), occ), {})
        total = _num(tcal.get("pred_runs_cal")) or _num(r.get("pred_runs_mc")) or DEFAULT_TOTAL
        fav_runs = (total + spread) / 2.0
        opp_runs = (total - spread) / 2.0
        fr = max(0, int(round(fav_runs))); orr = max(0, int(round(opp_runs)))
        if fr <= orr:
            fr = orr + 1
        proj_home, proj_away = (fr, orr) if fav_is_home else (orr, fr)
        # empirical exact-margin distribution for this confidence bucket
        mdist = None
        if mcal:
            for rng, dd in (mcal.get("dist_by_pickprob") or {}).items():
                try:
                    lo_s, hi_s = rng.split("-")
                    if float(lo_s) <= p < float(hi_s):
                        mdist = dd; break
                except Exception:
                    continue
        # bare matchup for game 1 (back-compat), " (G2)" suffix for DH game 2+
        gkey = m if occ == 0 else "%s (G%d)" % (m, occ + 1)
        games[gkey] = {
            "favored": fav, "win_prob": round(p, 4), "spread": round(spread, 1),
            "proj_away": proj_away, "proj_home": proj_home,
            "proj_score": "%s %d-%d %s" % (away, proj_away, proj_home, home),
            "most_probable_margin": {"team": fav, "runs": mpm_val, "prob": mpm_prob},
            "margin_top5": (mdist or {}).get("top5"),
            "total_band": ({"cal": round(total, 2),
                            "p25": _num(tcal.get("total_p25")), "p75": _num(tcal.get("total_p75")),
                            "p10": _num(tcal.get("total_p10")), "p90": _num(tcal.get("total_p90")),
                            "most_probable_total": _num(tcal.get("most_probable_total"))}
                           if tcal else None),
            "components": {"base": round(base, 2), "scale": round(scale, 3),
                           "rd_season_gap": round(rd_season, 3), "rd_recent_gap": round(rd_recent, 3),
                           "off_recent_gap": round(off_recent, 3), "xwoba_gap": round(xwoba_g, 3),
                           "sp_k_gap": round(sp_g, 3), "total_runs": round(total, 2),
                           "total_basis": tcal.get("cal_basis") or ("mc" if _num(r.get("pred_runs_mc")) else "default")},
        }

    if not games:
        print("[spread] 0 gradable games; skip"); return
    out = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "date": slate, "n_games": len(games),
           "basis": "Display-only projected run differential, recalibrated 2026-07-17: gentle logit curve anchored at the empirical median margin (+1) x production scale (season + last-14d run diff, rolling runs, lineup xwOBA, SP K%), score split on the market-blend calibrated total. Decoupled overlay, not a model output.",
           "games": games}
    outp = os.path.join(ROOT, "docs", "data", "spread_%s.json" % slate)
    try:
        with open(outp + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        os.replace(outp + ".tmp", outp)  # atomic: no torn sidecar on crash/AV-lock
        print("[spread] wrote %d games -> %s" % (len(games), outp))
    except Exception as e:
        print("[spread] write failed: %s" % e)


if __name__ == "__main__":
    main()
