#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""correlated_combo.py -- within-game Correlated Combo engine (DISPLAY-ONLY).

Replaces the dead-on-C-days 'Best Pick' module with a mathematically honest
correlated double: full-game moneyline + first-5 lead, same team. Everything
is computed from data the pipeline already publishes -- the frozen model is
never touched and no new market feed is invented.

Honesty constraints (locked project decisions this tool respects):
  * NO Over/Under legs: the totals product is PAUSED (pred_runs r=0.05,
    2026-06-03). Run-suppression signals appear as CONTEXT, not bets.
  * NO priced pitcher-prop legs: no props market feed exists. SP dominance
    (K-ceiling flags) enters as correlation context only.
  * The ML leg is the only market-anchored leg (Kalshi fair). The F5 leg is
    model-priced; the combo is labeled a "model-priced double".

Correlation: estimated EMPIRICALLY from our own graded OOS ledger
(docs/data/oos_ledger.jsonl), which scores both legs per game (tie-aware F5).
We take the historical conditional ratio r = P(full win | F5 win) /
P(full win | F5 loss) on pick-side pairs, then rescale to today's marginals
(pF5, pFull) so the implied joint is marginal-consistent and Frechet-bounded:
    x = pFull / (pF5*r + (1-pF5));  p11 = clamp(r*x*pF5, bounds)
Independence baseline = pF5*pFull; correlation lift = p11 - baseline.
F5 pushes (historical rate reported) void that leg; joint is stated for
decided-F5 games.

Consensus validation (user rule: unanimous or reject): every published data
sheet gets a verdict -- grade, tier, market band (the model's own Goldilocks
bands: <4 noise / 4-8 goldilocks / 8-15 caution / >15 trap), stage coherence
(F5 agrees with full + no Stage-1/2-disagree flag), Claude executive review,
team-tier class gap, and hot/cold streaks. ONE veto kills the combo; the
losing sheet is named in the payload. Dominance/weather/bullpen are context.

Output: docs/data/combo_<date>.json (payload consumed by the dashboard card).
Usage:  python tools/correlated_combo.py [YYYY-MM-DD] | --selftest
"""
import csv
import datetime
import io
import json
import os
import sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
CANON = {"CWS": "CHW", "AZ": "ARI", "ATH": "OAK", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
GRADE_PASS = ("A", "A-", "B+", "B")
COLD = ("ICE", "COLD", "COOL")
HOT = ("HOT", "BLAZING", "WARM")
MAX_COMBOS = 3


def canon(x):
    return CANON.get(str(x or "").strip(), str(x or "").strip())


def _f(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def _read_json(path):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# --------------------------------------------------------------------------
# correlation from the graded ledger
# --------------------------------------------------------------------------
def ledger_correlation(ledger_path):
    """Pick-side (F5 win, full win) pairs -> conditional ratio + phi."""
    rows = []
    if os.path.exists(ledger_path):
        for line in io.open(ledger_path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    preds = {(r.get("slate_date"), r.get("matchup")): r
             for r in rows if r.get("phase") == "predict"}
    f5_extra = {(r.get("slate_date"), r.get("matchup")): r
                for r in rows if r.get("phase") == "f5_result"}
    n11 = n10 = n01 = n00 = pushes = 0
    for r in rows:
        if r.get("phase") != "result" or r.get("status") != "Final":
            continue
        if r.get("no_pick") or r.get("outcome") is None:
            continue
        key = (r.get("slate_date"), r.get("matchup"))
        f5src = r if "f5_tie" in r else f5_extra.get(key)
        if not f5src:
            continue
        if f5src.get("f5_tie"):
            pushes += 1
            continue
        f5hw = f5src.get("f5_home_win")
        if f5hw is None:
            continue
        m = r.get("matchup") or ""
        if "@" not in m:
            continue
        home_tok = canon(m.split("@")[1])
        pick_side_home = canon(r.get("pick")) == home_tok
        f5_pick_win = f5hw if pick_side_home else 1 - f5hw
        full_win = int(r.get("outcome"))
        if f5_pick_win and full_win:
            n11 += 1
        elif f5_pick_win:
            n10 += 1
        elif full_win:
            n01 += 1
        else:
            n00 += 1
    n = n11 + n10 + n01 + n00
    out = {"n_pairs": n, "n11": n11, "n10": n10, "n01": n01, "n00": n00,
           "f5_pushes": pushes,
           "f5_push_rate": round(pushes / (n + pushes), 4) if (n + pushes) else None}
    if n11 + n10 >= 20 and n01 + n00 >= 20:
        p_w = n11 / (n11 + n10)
        p_l = n01 / (n01 + n00)
        out["p_full_given_f5w"] = round(p_w, 4)
        out["p_full_given_f5l"] = round(p_l, 4)
        out["ratio_r"] = round(p_w / p_l, 4) if p_l > 0 else 4.0
        import math
        den = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
        out["phi"] = round((n11 * n00 - n10 * n01) / den, 4) if den else None
        out["source"] = "oos_ledger empirical (frozen-window graded pairs)"
    else:
        # thin-ledger fallback: a conservative prior ratio, loudly labeled.
        out["ratio_r"] = 2.5
        out["source"] = "PRIOR (ledger too thin: %d pairs) -- conservative r=2.5" % n
    return out


def joint_probability(p_f5, p_full, ratio_r):
    """Marginal-consistent joint P(F5 win AND full win), Frechet-bounded."""
    r = max(ratio_r, 1.0)          # correlated legs: r >= 1 by construction
    x = p_full / (p_f5 * r + (1.0 - p_f5))          # = P(full | F5 loss)
    p11 = r * x * p_f5
    lo = max(0.0, p_f5 + p_full - 1.0)
    hi = min(p_f5, p_full)
    return min(max(p11, lo), hi)


# --------------------------------------------------------------------------
# consensus sheets
# --------------------------------------------------------------------------
def band_of(edge_pp):
    e = abs(edge_pp)
    if e < 4:
        return "noise"
    if e < 8:
        return "goldilocks"
    if e <= 15:
        return "caution"
    return "trap"


def claude_verdict(claude_doc, matchup):
    if not claude_doc:
        return None
    blob = json.dumps(claude_doc)
    if matchup not in blob:
        return None
    # defensive: find the game's entry in whatever list shape the brain wrote
    stack = [claude_doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            vals = json.dumps(node)
            if matchup in vals:
                for k in ("verdict", "action", "call", "decision", "review"):
                    v = str(node.get(k, "")).upper()
                    if "CONFIRM" in v:
                        return "CONFIRM"
                    if "DOWNGRADE" in v:
                        return "DOWNGRADE"
                    if "OVERRIDE" in v:
                        return "OVERRIDE"
                stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def tier_z(tiers_doc, team):
    if not isinstance(tiers_doc, dict):
        return None
    node = tiers_doc.get("teams") or tiers_doc.get("tiers") or {}
    if isinstance(node, list):
        for t in node:
            if canon(t.get("team")) == canon(team):
                return _f(t.get("composite") or t.get("z") or t.get("composite_z"))
        return None
    ent = node.get(canon(team)) or node.get(team)
    if isinstance(ent, dict):
        return _f(ent.get("composite") or ent.get("z") or ent.get("composite_z"))
    return _f(ent)


def streak_bucket(streaks_doc, team, window="7d"):
    try:
        return str(streaks_doc["teams"][canon(team)][window]["bucket"]).upper()
    except Exception:
        return None


def evaluate_game(r, ctx):
    """One diag row -> combo candidate with the full gate ledger."""
    m = (r.get("matchup") or "").strip()
    if "@" not in m:
        return None
    away, home = [t.strip() for t in m.split("@")]
    away_t, home_t = canon(away.split("(")[0]), canon(home.split("(")[0])
    pick = canon(r.get("pick"))
    if not pick or pick in ("TBD", "NO_PICK"):
        return None
    pick_prob = _f(r.get("pick_prob"))
    f5_home = _f(r.get("f5_prob"))
    fair = _f(r.get("fair_prob"))
    edge = _f(r.get("edge_pp"))
    if pick_prob is None or f5_home is None:
        return None
    pick_is_home = pick == home_t
    opp = away_t if pick_is_home else home_t
    p_f5 = f5_home if pick_is_home else (1.0 - f5_home)
    p_full = pick_prob

    gates = []

    def gate(sheet, verdict, note):
        gates.append({"sheet": sheet, "verdict": verdict, "note": note})

    grade = (r.get("grade") or "").strip()
    gate("grade", "pass" if grade in GRADE_PASS else "veto",
         "final grade %s (need %s)" % (grade or "?", "/".join(GRADE_PASS)))

    tier = (r.get("tier") or "").strip().upper()
    tier_ok = tier and not tier.startswith(("SKIP", "PENDING"))
    gate("conviction_tier", "pass" if tier_ok else "veto", "tier %s" % (tier or "?"))

    band = band_of(edge) if edge is not None else "n/a"
    # STRICT Goldilocks per Joe's 07-12 directive: 4-8pp ONLY (caution band
    # 8-15 no longer passes; it had been allowed in v1).
    gate("market_band", "pass" if band == "goldilocks" else "veto",
         "%+.1fpp vs fair -> %s band (strict 4-8 goldilocks)" % (edge or 0.0, band))

    reasons = r.get("grade_reasons") or ""
    coherent = p_f5 >= 0.5 and "Stage 1/2 disagree" not in reasons
    gate("stage_coherence", "pass" if coherent else "veto",
         "F5 side prob %.1f%%%s" % (p_f5 * 100,
         "" if "Stage 1/2 disagree" not in reasons else " + Stage 1/2 disagree flag"))

    cv = claude_verdict(ctx.get("claude"), m)
    gate("claude_review", "n/a" if cv is None else ("pass" if cv == "CONFIRM" else "veto"),
         "no review published today" if cv is None else cv)

    pz, oz = tier_z(ctx.get("tiers"), pick), tier_z(ctx.get("tiers"), opp)
    if pz is None or oz is None:
        gate("team_tiers", "n/a", "tier sheet unavailable for matchup")
    else:
        gap = pz - oz
        gate("team_tiers", "pass" if gap > -1.0 else "veto",
             "composite z gap %+.2f (veto below -1.00)" % gap)

    pb = streak_bucket(ctx.get("streaks"), pick)
    ob = streak_bucket(ctx.get("streaks"), opp)
    if pb is None or ob is None:
        gate("streaks", "n/a", "streak sheet unavailable")
    else:
        clash = pb in COLD[:2] and ob in HOT[:2]
        gate("streaks", "veto" if clash else "pass",
             "pick 7d %s vs opp %s" % (pb, ob))

    unanimous = all(g["verdict"] != "veto" for g in gates)

    # ---- correlation math -------------------------------------------------
    corr = ctx["corr"]
    p11 = joint_probability(p_f5, p_full, corr.get("ratio_r", 2.5))
    indep = p_f5 * p_full
    lift = p11 - indep

    # ---- context (never gates) --------------------------------------------
    context = {}
    sp_name = (r.get("home_sp_name") if pick_is_home else r.get("away_sp_name")) or ""
    dom = ctx.get("dominance") or {}
    flag = None
    try:
        ent = (dom.get("sps") or {}).get(sp_name.strip())
        if isinstance(ent, dict):
            flag = ent.get("flag") or ent.get("ceiling") or ent.get("tier")
    except Exception:
        pass
    if flag:
        context["sp_dominance"] = "%s: %s K-ceiling (suppression links F5+ML jointly)" % (sp_name, flag)
    strain = _f(r.get("pen_strain_pick_side"))
    if strain is not None:
        context["pen_strain_pick_side"] = strain
    wx = ctx.get("weather") or {}
    try:
        for g in (wx.get("games") or wx.get("matchups") or []):
            if canon(g.get("home")) == home_t or (g.get("matchup") and m in str(g.get("matchup"))):
                if g.get("tilt") or g.get("badge"):
                    context["weather"] = str(g.get("tilt") or g.get("badge"))
                break
    except Exception:
        pass

    narrative = ("%s ML + %s F5 lead: the same win path drives both legs -- "
                 "historically, when the pick leads after 5 it wins the game "
                 "%.0f%% of the time vs %.0f%% when it trails (ledger, n=%d)."
                 % (pick, pick, 100 * ctx["corr"].get("p_full_given_f5w", 0.79),
                    100 * ctx["corr"].get("p_full_given_f5l", 0.31),
                    ctx["corr"].get("n_pairs", 0))
                 if ctx["corr"].get("p_full_given_f5w") else
                 "%s ML + %s F5 lead: one win path, two correlated legs." % (pick, pick))

    return {"matchup": m, "pick": pick, "opponent": opp,
            "legs": [
                {"type": "ML", "sel": "%s wins" % pick, "prob": round(p_full, 4),
                 "fair": round(fair, 4) if fair is not None else None,
                 "edge_pp": round(edge, 2) if edge is not None else None,
                 "band": band, "market": "kalshi fair (devigged)"},
                {"type": "F5", "sel": "%s leads after 5 (tie voids leg)" % pick,
                 "prob": round(p_f5, 4), "fair": None, "edge_pp": None,
                 "band": None, "market": "model-priced (no F5 market feed)"}],
            "joint_prob": round(p11, 4),
            "independence_prob": round(indep, 4),
            "correlation_lift_pp": round(lift * 100, 2),
            "grade": grade, "tier": tier,
            "gates": gates, "unanimous": unanimous,
            "narrative": narrative, "context": context}


# --------------------------------------------------------------------------
def build(date, root=None):
    root = root or ROOT
    diag = os.path.join(root, "picks_%s_diag.csv" % date)
    if not os.path.exists(diag):
        diag = os.path.join(root, "docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(diag):
        return None, "no diag for %s" % date
    dd = os.path.join(root, "docs", "data")
    ctx = {
        "corr": ledger_correlation(os.path.join(dd, "oos_ledger.jsonl")),
        "claude": _read_json(os.path.join(root, "claude_picks", "%s.json" % date)),
        "tiers": _read_json(os.path.join(dd, "team_tiers.json")),
        "streaks": _read_json(os.path.join(dd, "streaks_%s.json" % date)),
        "dominance": _read_json(os.path.join(dd, "dominance_%s.json" % date)),
        "weather": _read_json(os.path.join(dd, "weather_runs_%s.json" % date)),
    }
    csv.field_size_limit(10 ** 7)
    cands = []
    with io.open(diag, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            c = evaluate_game(r, ctx)
            if c:
                cands.append(c)
    passers = [c for c in cands if c["unanimous"]]
    passers.sort(key=lambda c: (c["legs"][0]["band"] != "goldilocks",
                                -(c["correlation_lift_pp"] +
                                  (c["legs"][0]["edge_pp"] or 0))))

    # ---- cross-market LEG POOL (Joe's step-6 architecture) -----------------
    # Every PRICED leg that individually survives strict-goldilocks +
    # unanimous consensus, across all live markets, sorted by edge desc.
    # ML is live (Kalshi fair). O/U is a reserved slot (totals PAUSED by the
    # locked 06-03 decision until the rebuild validates). K-props join as
    # SHADOW legs when the odds adapter has a key + data.
    pool = []
    for c in cands:
        ml = c["legs"][0]
        if c["unanimous"] and ml["band"] == "goldilocks" and ml["edge_pp"] is not None:
            pool.append({"market": "ML", "matchup": c["matchup"],
                         "sel": ml["sel"], "model_prob": ml["prob"],
                         "market_prob": ml["fair"], "edge_pp": ml["edge_pp"],
                         "status": "live", "unanimous": True})
    kprops = _read_json(os.path.join(dd, "kprops_%s.json" % date))
    if kprops and kprops.get("status") == "ok":
        for leg in kprops.get("legs", []):
            e = _f(leg.get("edge_pp"))
            if e is not None and 4.0 <= e < 8.0:
                leg = dict(leg)
                leg.update({"market": "K_PROP", "status": "shadow_validation",
                            "unanimous": None,
                            "note": "projection-vs-market edge; SHADOW until "
                                    "the pre-registered OOS gate passes"})
                pool.append(leg)
    pool.sort(key=lambda x: -(x.get("edge_pp") or 0))
    markets_status = {
        "ML": "live (Kalshi devigged fair)",
        "OU": "reserved -- totals PAUSED (locked 2026-06-03, pred_runs r=0.05); "
              "unlocks after the rebuild passes its OOS validation protocol",
        "K_PROP": (("live-shadow (%d legs)" % sum(1 for x in pool if x["market"] == "K_PROP"))
                   if (kprops and kprops.get("status") == "ok")
                   else (kprops or {}).get("status_note",
                        "awaiting ODDS_API_KEY in .env -- adapter shipped "
                        "(tools/kprop_odds.py)"))}
    rejected = [{"matchup": c["matchup"], "pick": c["pick"],
                 "joint_prob": c["joint_prob"],
                 "blocked_by": [g["sheet"] for g in c["gates"] if g["verdict"] == "veto"],
                 "notes": [g["note"] for g in c["gates"] if g["verdict"] == "veto"]}
                for c in cands if not c["unanimous"]]
    payload = {
        "date": date,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": "correlated-combo v1 (display-only; frozen model untouched)",
        "scope_notes": [
            "Legs limited to ML + F5 same team: totals product is PAUSED "
            "(2026-06-03, pred_runs r=0.05) and no props market feed exists; "
            "K-dominance and run-environment signals appear as context only.",
            "ML is the only market-anchored leg; the double is model-priced."],
        "correlation": ctx["corr"],
        "combos": passers[:MAX_COMBOS],
        "combo_pool": pool,
        "markets_status": markets_status,
        "rejected": rejected,
        "n_games": len(cands)}
    out_path = os.path.join(dd, "combo_%s.json" % date)
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, out_path)
    return payload, out_path


# --------------------------------------------------------------------------
def selftest():
    import tempfile, shutil, math
    tmp = tempfile.mkdtemp(prefix="combo_selftest_")
    try:
        dd = os.path.join(tmp, "docs", "data")
        os.makedirs(dd)
        # planted ledger: strong F5->full correlation
        with io.open(os.path.join(dd, "oos_ledger.jsonl"), "w", encoding="utf-8") as f:
            for i in range(60):   # F5 win -> full win 80%
                f.write(json.dumps({"phase": "result", "status": "Final",
                    "slate_date": "2026-06-%02d" % (i % 28 + 1), "matchup": "AA @ BB",
                    "pick": "BB", "outcome": 1 if i % 10 < 8 else 0,
                    "f5_tie": False, "f5_home_win": 1}) + "\n")
            for i in range(60):   # F5 loss -> full win 30%
                f.write(json.dumps({"phase": "result", "status": "Final",
                    "slate_date": "2026-05-%02d" % (i % 28 + 1), "matchup": "AA @ BB",
                    "pick": "BB", "outcome": 1 if i % 10 < 3 else 0,
                    "f5_tie": False, "f5_home_win": 0}) + "\n")
        corr = ledger_correlation(os.path.join(dd, "oos_ledger.jsonl"))
        assert corr["n_pairs"] == 120 and abs(corr["p_full_given_f5w"] - 0.8) < 1e-6
        assert abs(corr["ratio_r"] - (0.8 / 0.3)) < 1e-3
        # joint math: marginal consistency + Frechet bounds
        pf5, pfull, r = 0.58, 0.62, corr["ratio_r"]
        p11 = joint_probability(pf5, pfull, r)
        x = pfull / (pf5 * r + 1 - pf5)
        assert abs((p11 + (1 - pf5) * x) - pfull) < 1e-9      # reconstructs marginal
        assert max(0, pf5 + pfull - 1) <= p11 <= min(pf5, pfull)
        assert p11 > pf5 * pfull                              # positive correlation
        # diag fixture: one unanimous passer, one grade-veto
        diag = os.path.join(tmp, "picks_2026-07-12_diag.csv")
        cols = ["matchup", "pick", "f5_prob", "full_prob", "pick_prob", "fair_prob",
                "edge_pp", "tier", "grade", "grade_reasons", "pen_strain_pick_side",
                "home_sp_name", "away_sp_name"]
        with io.open(diag, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerow({"matchup": "AA @ BB", "pick": "BB", "f5_prob": "0.58",
                        "pick_prob": "0.62", "full_prob": "0.62", "fair_prob": "0.565",
                        "edge_pp": "5.5", "tier": "GOLD", "grade": "B+",
                        "grade_reasons": "", "pen_strain_pick_side": "0.4",
                        "home_sp_name": "Ace Man", "away_sp_name": "Other Guy"})
            w.writerow({"matchup": "CC @ DD", "pick": "DD", "f5_prob": "0.60",
                        "pick_prob": "0.61", "full_prob": "0.61", "fair_prob": "0.55",
                        "edge_pp": "6.0", "tier": "GOLD", "grade": "C",
                        "grade_reasons": "", "pen_strain_pick_side": "",
                        "home_sp_name": "X", "away_sp_name": "Y"})
        payload, out = build("2026-07-12", root=tmp)
        assert payload and os.path.exists(out)
        assert len(payload["combos"]) == 1
        c = payload["combos"][0]
        assert c["matchup"] == "AA @ BB" and c["unanimous"]
        assert c["correlation_lift_pp"] > 0
        # cross-market pool: the surviving ML leg, sorted, statuses present
        assert len(payload["combo_pool"]) == 1
        assert payload["combo_pool"][0]["market"] == "ML"
        assert payload["combo_pool"][0]["unanimous"] is True
        assert set(payload["markets_status"]) == {"ML", "OU", "K_PROP"}
        assert any(g["sheet"] == "claude_review" and g["verdict"] == "n/a"
                   for g in c["gates"])                        # missing sheet != veto
        assert len(payload["rejected"]) == 1
        assert payload["rejected"][0]["blocked_by"] == ["grade"]
        # stage-incoherent pick (F5 side < 0.5) must veto; caution band (10pp)
        # must now ALSO veto under the strict 4-8 rule
        with io.open(diag, "a", encoding="utf-8", newline="") as f:
            w2 = csv.DictWriter(f, fieldnames=cols)
            w2.writerow(
                {"matchup": "EE @ FF", "pick": "EE", "f5_prob": "0.60",
                 "pick_prob": "0.60", "full_prob": "0.60", "fair_prob": "0.54",
                 "edge_pp": "6.0", "tier": "GOLD", "grade": "B+",
                 "grade_reasons": "", "pen_strain_pick_side": "",
                 "home_sp_name": "X", "away_sp_name": "Y"})
            w2.writerow(
                {"matchup": "GG @ HH", "pick": "HH", "f5_prob": "0.60",
                 "pick_prob": "0.62", "full_prob": "0.62", "fair_prob": "0.52",
                 "edge_pp": "10.0", "tier": "GOLD", "grade": "B+",
                 "grade_reasons": "", "pen_strain_pick_side": "",
                 "home_sp_name": "X", "away_sp_name": "Y"})
        payload, _ = build("2026-07-12", root=tmp)
        rej = {r["matchup"]: r for r in payload["rejected"]}
        assert "EE @ FF" in rej and "stage_coherence" in rej["EE @ FF"]["blocked_by"]
        assert "GG @ HH" in rej and "market_band" in rej["GG @ HH"]["blocked_by"]
        print("SELFTEST PASS -- corr recovery, marginal-consistent joint, "
              "Frechet bounds, unanimous gate + named vetoes, n/a-not-veto")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    date = None
    for a in sys.argv[1:]:
        if len(a) == 10 and a[4] == "-":
            date = a
    date = date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    payload, out = build(date)
    if payload is None:
        print("[combo] %s" % out)
        return 0                    # non-fatal in the chain
    print("[combo] %s: %d combos / %d rejected (of %d games) -> %s"
          % (date, len(payload["combos"]), len(payload["rejected"]),
             payload["n_games"], out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
