#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model_guardrails.py -- data-driven guardrail state for the pick pipeline.

Computes, from verified graded history (MODEL_IMPROVEMENT_AUDIT_2026-07-17):
  1. CALIBRATION DRIFT (rolling 30d): predicted-vs-actual win rate per
     pick_prob bucket. Any bucket with n>=15 running > ALERT_GAP_PP hot
     fires a pipeline alert -- the model is hallucinating confidence again.
  2. TIER SELF-DEMOTION (rolling 60d): a staked tier (DIAMOND/PLATINUM)
     only keeps its stake multiplier while its rolling win rate beats the
     unstaked GOLD benchmark; otherwise it is demoted to GOLD sizing until
     it re-earns. (Audit: DIAMOND .500 / PLATINUM .543 vs GOLD .576.)
  3. BLIND-SPOT TEAMS: from docs/data/team_predictability.json -- teams the
     model calls right < 46% of the time over >= 25 games. Staked picks in
     their games are capped to SKIP. Self-releasing as accuracy recovers.

Writes data/state/model_guardrails.json (atomic). main_predict reads it at
import time and applies the caps at the display/tier layer -- the frozen
model itself is untouched. Missing/stale state = static defaults only.

Usage: python tools/model_guardrails.py   (no args; wired into
run_local_slate BEFORE predict so each slate applies fresh state)
"""
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
S = lambda x: (x if isinstance(x, str) else "") if x is not None else ""
csv.field_size_limit(10 ** 7)

OUT = os.path.join("data", "state", "model_guardrails.json")
CAL_WINDOW_D = 30
TIER_WINDOW_D = 60
ALERT_GAP_PP = 8.0
PROB_CEILING = 0.70
BLIND_ACC = 0.46
BLIND_MIN_N = 25
BUCKETS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01))


def _truth():
    truth = {}
    for f in glob.glob("docs/data/postgame/*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for m, i in (d.get("by_matchup", {}) or {}).items():
            if not isinstance(i, dict) or "(G" in S(m):
                continue
            sc = S(i.get("final_score")).strip()
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(m))
            if mm and re.match(r"^\d+-\d+$", sc):
                a, b = map(int, sc.split("-"))
                truth[(date, mm.group(1), mm.group(2))] = (a, b)
    for f in glob.glob("docs/data/_results_*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for g in d.get("games", []):
            if g.get("status") != "Final":
                continue
            try:
                truth[(date, S(g["away"]).strip(), S(g["home"]).strip())] = \
                    (int(g["away_runs"]), int(g["home_runs"]))
            except Exception: pass
    return truth


def _graded(truth, since_iso):
    """[(date, pick_prob, tier, won)] for graded picks on/after since_iso."""
    rows, seen = [], set()
    for f in sorted(glob.glob("picks_20??-??-??_diag.csv")
                    + glob.glob("docs/data/picks_20??-??-??_diag.csv")):
        md = re.search(r"picks_(\d{4}-\d\d-\d\d)_diag", f)
        if not md or md.group(1) < since_iso:
            continue
        date = md.group(1)
        try:
            rdr = list(csv.DictReader(open(f, encoding="utf-8", errors="replace")))
        except Exception:
            continue
        for r in rdr:
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(r.get("matchup")))
            if not mm:
                continue
            away, home = mm.group(1), mm.group(2)
            if away in ("AL", "NL"):
                continue
            key = (date, away, home)
            if key in seen or key not in truth:
                continue
            seen.add(key)
            pick = S(r.get("pick")).strip()
            # calibrate on the RAW model probability when the guarded column
            # exists (post-2026-07-17 diags), else pick_prob
            p = S(r.get("pick_prob_raw")) or S(r.get("pick_prob")) or S(r.get("p_model"))
            if not pick or pick == "TBD" or pick not in (away, home) or not p:
                continue
            try:
                p = float(p)
            except ValueError:
                continue
            ar, hr = truth[key]
            if ar == hr:
                continue
            won = (hr > ar) if pick == home else (ar > hr)
            rows.append((date, p, S(r.get("tier")).strip(), int(won)))
    return rows


def main():
    today = datetime.date.today()
    truth = _truth()
    cal_rows = _graded(truth, (today - datetime.timedelta(days=CAL_WINDOW_D)).isoformat())
    tier_rows = _graded(truth, (today - datetime.timedelta(days=TIER_WINDOW_D)).isoformat())

    # 1) calibration drift
    buckets, worst_gap, alert = [], 0.0, False
    for lo, hi in BUCKETS:
        b = [(p, w) for (_, p, _, w) in cal_rows if lo <= p < hi]
        if not b:
            continue
        n = len(b)
        pred = sum(p for p, _ in b) / n
        act = sum(w for _, w in b) / n
        gap = (pred - act) * 100.0
        if n >= 15:
            worst_gap = max(worst_gap, gap)
            if gap > ALERT_GAP_PP:
                alert = True
        buckets.append({"lo": lo, "hi": min(hi, 1.0), "n": n,
                        "pred": round(pred, 3), "actual": round(act, 3),
                        "gap_pp": round(gap, 1)})

    # 2) tier self-demotion vs the GOLD benchmark
    def _rate(tier):
        b = [w for (_, _, t, w) in tier_rows if t == tier]
        return (len(b), (sum(b) / len(b) if b else None))
    n_gold, gold_rate = _rate("GOLD")
    demotions, tier_stats = {}, {}
    for t in ("DIAMOND", "PLATINUM"):
        n_t, r_t = _rate(t)
        tier_stats[t] = {"n": n_t, "win_rate": round(r_t, 3) if r_t is not None else None}
        if (gold_rate is not None and r_t is not None
                and n_t >= 10 and n_gold >= 20 and r_t < gold_rate):
            demotions[t] = "GOLD"
    tier_stats["GOLD"] = {"n": n_gold,
                          "win_rate": round(gold_rate, 3) if gold_rate is not None else None}

    # 3) blind-spot teams (from the team-predictability sidecar)
    blind, blind_detail = [], []
    try:
        tp = json.load(open("docs/data/team_predictability.json", encoding="utf-8"))
        for t in tp.get("teams", []):
            if not t.get("thin") and t.get("n", 0) >= BLIND_MIN_N and t.get("acc", 1.0) < BLIND_ACC:
                blind.append(t["team"])
                blind_detail.append({"team": t["team"], "acc": t["acc"], "n": t["n"]})
    except Exception as e:
        print("[guardrails] team_predictability unavailable (%r) -- no blind-spot caps" % (e,))

    prev = {}
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        pass

    out = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows_days": {"calibration": CAL_WINDOW_D, "tiers": TIER_WINDOW_D},
        "prob_ceiling": PROB_CEILING,
        "calibration": {"buckets": buckets, "worst_gap_pp": round(worst_gap, 1),
                        "alert_gap_pp": ALERT_GAP_PP, "alert": alert,
                        "n_graded": len(cal_rows)},
        "tier_demotions": demotions,
        "tier_stats": tier_stats,
        "blindspot_teams": blind,
        "blindspot_detail": blind_detail,
        "spec": "MODEL_IMPROVEMENT_AUDIT_2026-07-17.md",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, OUT)
    print("[guardrails] wrote %s" % OUT)
    print("[guardrails] calibration: worst gap %+.1fpp over %d graded (alert=%s)"
          % (worst_gap, len(cal_rows), alert))
    print("[guardrails] tier demotions: %s | stats: %s" % (demotions or "none", tier_stats))
    print("[guardrails] blind-spot teams: %s" % (", ".join(blind) or "none"))

    # loud alert only on state CHANGES or an active calibration alarm
    changed = (alert and not (prev.get("calibration") or {}).get("alert")) \
        or (demotions != (prev.get("tier_demotions") or {})) \
        or (sorted(blind) != sorted(prev.get("blindspot_teams") or []))
    if changed:
        msg = ("model guardrails changed: cal_alert=%s worst_gap=%+.1fpp "
               "demotions=%s blindspots=%s"
               % (alert, worst_gap, demotions or "{}", ",".join(blind) or "-"))
        try:
            subprocess.run([sys.executable, "tools/pipeline_alert.py", msg], timeout=60)
        except Exception as e:
            print("[guardrails] alert send failed (%r)" % (e,))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[guardrails] WARN unexpected failure %r -- state not updated" % (e,))
