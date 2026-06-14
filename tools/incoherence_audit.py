# -*- coding: utf-8 -*-
"""
tools/incoherence_audit.py  —  Incoherence Bucket descriptive audit (READ-ONLY)
================================================================================
Pre-registered in CALIBRATION_SPEC.md (addendum, LOCKED 2026-06-11). This is the
batch harness that scaffolds that audit. It is **strictly read-only**: it never
writes to data/state/, never touches docs/data/, never mutates a model, weight,
cap, or the ledger. It reads the OOS ledger + the archived per-day diag CSVs and
emits a DESCRIPTIVE report to the repo root (incoherence_audit_report.json) +
stdout. Nothing here is a gate; per the addendum it "changes NOTHING by itself".

Design: a single IDEMPOTENT batch job. The bucket is defined by the archived
diag `grade_reasons` ("Stage 1/2 disagree" — the grade engine's own delta>=0.12
trigger), which is already persisted per day, so the whole window is re-derived
deterministically on every run. Run it as a dry preview now; run the SAME script
at the end of the window in July. No streaming state, no first-write-wins.

Discipline (from the addendum):
  * Window = OOS, slate_date >= 2026-06-04 .. freeze-lift.
  * Directional claims require bucket n >= 25; below that -> INSUFFICIENT_N,
    counts only, no lean reported.
  * Anchor case surfaced first: 2026-06-11 SEA @ BAL.

Usage:
  python tools/incoherence_audit.py                 # window 2026-06-04 .. today
  python tools/incoherence_audit.py --start 2026-06-04 --end 2026-07-20
  python tools/incoherence_audit.py --no-write       # stdout only, write nothing
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import sys

csv.field_size_limit(10 ** 7)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LEDGER = os.path.join("docs", "data", "oos_ledger.jsonl")
DIAG_FMT = os.path.join("docs", "data", "picks_%s_diag.csv")
WINDOW_START = "2026-06-04"           # frozen era (CALIBRATION_SPEC §3/§4)
BUCKET_MARK = "stage 1/2 disagree"    # grade_reasons substring (case-insensitive)
MIN_DIRECTIONAL_N = 25                # addendum: directional claims need n>=25
HI_EDGE = 15.0                        # Q3 subset: |edge_pp| >= 15
ANCHOR = ("2026-06-11", "SEA @ BAL")  # addendum anchor case


def _f(v):
    try:
        x = float(v)
        return x if x == x else None      # drop NaN
    except (TypeError, ValueError):
        return None


def load_ledger():
    rows = []
    if not os.path.exists(LEDGER):
        return rows
    with open(LEDGER, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
    return rows


def load_diag(date):
    """Return {matchup: row} for one archived diag; {} if absent."""
    p = DIAG_FMT % date
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            k = (r.get("matchup") or "").strip()
            if k:
                out[k] = r            # last write wins (DH bare-key caveat, logged below)
    return out


def brier(p, y):
    return (p - y) ** 2 if (p is not None and y is not None) else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def build(start, end):
    led = load_ledger()
    results = {}      # (date,matchup) -> result row
    f5res = {}        # (date,matchup) -> f5_result row
    for r in led:
        d = r.get("slate_date") or ""
        if not (start <= d <= end):
            continue
        key = (d, (r.get("matchup") or "").strip())
        if r.get("phase") == "result":
            results[key] = r
        elif r.get("phase") == "f5_result":
            f5res[key] = r

    diag_cache = {}
    joined = []
    for (d, m), res in results.items():
        if d not in diag_cache:
            diag_cache[d] = load_diag(d)
        drow = diag_cache[d].get(m)
        if drow is None:
            continue                                  # no archived diag -> skip
        gr = (drow.get("grade_reasons") or "")
        in_bucket = BUCKET_MARK in gr.lower()
        f5h = _f(drow.get("f5_prob"))                 # home-ref
        fuh = _f(drow.get("full_prob"))               # home-ref
        rec = {
            "slate_date": d, "matchup": m,
            "in_bucket": in_bucket,
            "grade_reasons": gr,
            "f5_prob_homeref": f5h,
            "full_prob_homeref": fuh,
            "f5_full_delta": (abs(f5h - fuh) if (f5h is not None and fuh is not None) else None),
            "pick": res.get("pick"),
            "pick_prob": _f(res.get("pick_prob")),     # pick-ref
            "fair_prob": _f(drow.get("fair_prob")),    # pick-ref
            "edge_pp": _f(drow.get("edge_pp")),
            "outcome": (int(res["outcome"]) if res.get("outcome") is not None else None),
            "brier_pickref": _f(res.get("brier")),
            "winner": res.get("winner"),
            "home": res.get("home") or m.split("@")[-1].strip(),
            "away": res.get("away") or m.split("@")[0].strip(),
            "f5_home_win": (int(f5res[(d, m)]["f5_home_win"])
                            if (d, m) in f5res and f5res[(d, m)].get("f5_home_win") is not None else None),
            "f5_brier_homeref": (_f(f5res[(d, m)].get("f5_brier_homeref"))
                                 if (d, m) in f5res else None),
            "f5_tie": (bool(f5res[(d, m)].get("f5_tie")) if (d, m) in f5res else None),
        }
        joined.append(rec)
    return joined


def winrate_favored(recs, prob_key, home_win_key):
    """Win rate of the side a stage favored, vs the actual outcome for that stage."""
    hits, n = 0, 0
    for r in recs:
        p = r.get(prob_key)
        y = r.get(home_win_key)
        if p is None or y is None:
            continue
        fav_home = p >= 0.5
        n += 1
        # did the favored side win? y=1 means home won (that stage's outcome)
        if (fav_home and y == 1) or ((not fav_home) and y == 0):
            hits += 1
    return (hits / n if n else None), n


def q1_calibration_split(joined):
    inb = [r for r in joined if r["in_bucket"]]
    out = [r for r in joined if not r["in_bucket"]]
    def blk(recs):
        return {
            "n": len(recs),
            "full_pick_brier": mean([r["brier_pickref"] for r in recs]),
            "full_pick_accuracy": mean([(1.0 if r["outcome"] == 1 else 0.0)
                                        for r in recs if r["outcome"] is not None]),
            "f5_brier_homeref": mean([r["f5_brier_homeref"] for r in recs]),
        }
    return {"in_bucket": blk(inb), "out_of_bucket": blk(out)}


def q2_stage_leak(joined):
    inb = [r for r in joined if r["in_bucket"]]
    # Within bucket: does the F5-favored side or the FULL-favored side better
    # predict the FULL-game winner?  outcome here = home won the full game.
    inb_full_outcome = []
    for r in inb:
        hw = (1 if r["winner"] and r["home"] and r["winner"] == r["home"]
              else (0 if r["winner"] else None))
        rr = dict(r); rr["full_home_win"] = hw
        inb_full_outcome.append(rr)
    f5_wr, f5_n = winrate_favored(inb_full_outcome, "f5_prob_homeref", "full_home_win")
    fu_wr, fu_n = winrate_favored(inb_full_outcome, "full_prob_homeref", "full_home_win")
    return {
        "bucket_n": len(inb),
        "f5_favored_side_full_winrate": f5_wr, "f5_n": f5_n,
        "full_favored_side_full_winrate": fu_wr, "full_n": fu_n,
        "directional": len(inb) >= MIN_DIRECTIONAL_N,
        "note": ("OK to read directionally" if len(inb) >= MIN_DIRECTIONAL_N
                 else "INSUFFICIENT_N (<%d) — counts only, no lean" % MIN_DIRECTIONAL_N),
    }


def q3_market(joined):
    sub = [r for r in joined if r["in_bucket"] and r["edge_pp"] is not None
           and abs(r["edge_pp"]) >= HI_EDGE and r["outcome"] is not None
           and r["pick_prob"] is not None and r["fair_prob"] is not None]
    bm = mean([brier(r["pick_prob"], r["outcome"]) for r in sub])   # model
    bk = mean([brier(r["fair_prob"], r["outcome"]) for r in sub])   # market
    closer = None
    if bm is not None and bk is not None:
        closer = "model(pick_prob)" if bm < bk else ("market(fair_prob)" if bk < bm else "tie")
    return {
        "subset_n": len(sub), "model_brier": bm, "market_brier": bk,
        "closer_to_outcome": closer,
        "directional": len(sub) >= MIN_DIRECTIONAL_N,
        "note": ("OK to read directionally" if len(sub) >= MIN_DIRECTIONAL_N
                 else "INSUFFICIENT_N (<%d) — counts only, no lean" % MIN_DIRECTIONAL_N),
    }


def anchor_row(joined):
    for r in joined:
        if (r["slate_date"], r["matchup"]) == ANCHOR:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    joined = build(args.start, args.end)
    n_bucket = sum(1 for r in joined if r["in_bucket"])

    report = {
        "audit": "incoherence_bucket",
        "status": "READ-ONLY DESCRIPTIVE (changes nothing; see CALIBRATION_SPEC.md addendum)",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": {"start": args.start, "end": args.end},
        "n_joined_graded": len(joined),
        "n_in_bucket": n_bucket,
        "min_directional_n": MIN_DIRECTIONAL_N,
        "gate_met_for_directional": n_bucket >= MIN_DIRECTIONAL_N,
        "anchor_2026-06-11_SEA@BAL": anchor_row(joined),
        "Q1_calibration_split": q1_calibration_split(joined),
        "Q2_which_stage_leaks_alpha": q2_stage_leak(joined),
        "Q3_market_interaction_hi_edge": q3_market(joined),
    }

    print("=" * 70)
    print("INCOHERENCE BUCKET AUDIT  (READ-ONLY / DESCRIPTIVE)")
    print("window %s .. %s | joined graded=%d | in-bucket=%d (need >=%d for any lean)"
          % (args.start, args.end, len(joined), n_bucket, MIN_DIRECTIONAL_N))
    print("=" * 70)
    a = report["anchor_2026-06-11_SEA@BAL"]
    print("\nANCHOR 2026-06-11 SEA @ BAL:",
          ("NOT FOUND in window/diags yet" if a is None else ""))
    if a:
        print("   in_bucket=%s | f5(home)=%s full(home)=%s delta=%s | pick=%s pick_prob=%s "
              "fair=%s edge_pp=%s | outcome=%s winner=%s"
              % (a["in_bucket"], a["f5_prob_homeref"], a["full_prob_homeref"],
                 a["f5_full_delta"], a["pick"], a["pick_prob"], a["fair_prob"],
                 a["edge_pp"], a["outcome"], a["winner"]))
        print("   grade_reasons:", (a["grade_reasons"] or "")[:140])
    for qk in ("Q1_calibration_split", "Q2_which_stage_leaks_alpha", "Q3_market_interaction_hi_edge"):
        print("\n%s:" % qk)
        print("   " + json.dumps(report[qk], default=str))

    if not args.no_write:
        outp = "incoherence_audit_report.json"     # repo ROOT, NOT docs/data (never published)
        with open(outp, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print("\n[written] %s  (root, read-only artifact — not published)" % outp)
    else:
        print("\n[--no-write] nothing written")


if __name__ == "__main__":
    main()
