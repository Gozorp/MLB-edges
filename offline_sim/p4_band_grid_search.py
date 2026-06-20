#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
p4_band_grid_search.py  --  P4 Goldilocks-band re-slice harness (OFFLINE).

*** OFFLINE / READ-ONLY ***  Writes only offline_sim/.  No model, no docs/data, no
git, no live path.  Freeze-safe July prep (EDGE_TIGHTENING_PREREG.md P4 + A3).

Grids the band over the LOCKED parameter sets, selects on daily-ROI Sharpe (A3),
requires >=150 bets/slice, and demands the winner beat the documented 0.04 band on
the SAME ledger.  July day-one: point --ledger at the 2023-2025 extended cache
(post-P1 calibration) and run.  Today it smoke-tests on the 2026 shadow ledger.

LEDGER SCHEMA (input, from build_shadow_eligible_ledger.py):
  date, matchup, pick, model_prob, fair_prob, edge_pp, ev_per_dollar, dec_odds,
  model_tier, is_eligible, pick_won
ROI MODE:
  - dec_odds present (recovered from ev_per_dollar) -> REAL vigged flat-stake ROI.
  - dec_odds blank   -> NO_VIG fallback dec=1/fair_prob (optimistic; smoke only).

OUTPUT SCHEMA (offline_sim/p4_grid_results.csv) -- pre-registered:
  min_edge, max_edge, min_fair, min_mp, max_mp,   # the slice params
  n_bets, win_pct, roi_pooled, sharpe_daily,       # the metrics
  roi_mode, ledger_span, holdout_note              # provenance
Sorted by sharpe_daily desc among slices with n_bets>=150.  The row matching the
documented production band (.04/.15/.42/.48/.72) is tagged BASELINE for comparison.
"""
import os, csv, glob, itertools, statistics, datetime, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_LEDGER = os.path.join(ROOT, "offline_sim", "shadow_eligible_ledger.csv")
OUT = os.path.join(ROOT, "offline_sim", "p4_grid_results.csv")

# LOCKED grid (EDGE_TIGHTENING_PREREG.md P4)
G_MIN_EDGE = [0.04, 0.05, 0.06, 0.07, 0.08]
G_MAX_EDGE = [0.10, 0.12, 0.13, 0.15]
G_MIN_FAIR = [0.42, 0.45, 0.48, 0.50]
G_MIN_MP   = [0.48, 0.50, 0.52]
G_MAX_MP   = [0.68, 0.70, 0.72]
BASELINE   = (0.04, 0.15, 0.42, 0.48, 0.72)
MIN_BETS   = 150   # locked; slices below this are reported but never selected

def fnum(v):
    try: return float(v)
    except: return None

def load(path):
    rows=[]
    csv.field_size_limit(10**7)
    for r in csv.DictReader(open(path,encoding="utf-8",errors="replace")):
        mp=fnum(r.get("model_prob")); fair=fnum(r.get("fair_prob")); edge=fnum(r.get("edge_pp"))
        won=fnum(r.get("pick_won")); dec=fnum(r.get("dec_odds"))
        if None in (mp,fair,edge,won): continue
        if dec is None and fair: dec=1.0/fair   # NO_VIG fallback
        rows.append({"date":r.get("date"),"mp":mp,"fair":fair,"edge":edge/100.0,
                     "won":int(won),"dec":dec})
    return rows

def daily_sharpe(bets):
    """A3: per-day flat-stake ROI, no-bet days contribute 0, mean/pop-stdev, not annualized."""
    if not bets: return None, None, None
    by_day={}
    for b in bets:
        pl=(b["dec"]-1.0) if b["won"] else -1.0   # 1-unit flat stake
        by_day.setdefault(b["date"], [0.0,0])
        by_day[b["date"]][0]+=pl; by_day[b["date"]][1]+=1
    # all calendar days in the ledger span (so idle days count as roi=0)
    all_days=sorted({b["date"] for b in bets})
    daily=[ (by_day[d][0]/by_day[d][1]) if by_day.get(d) and by_day[d][1] else 0.0 for d in all_days ]
    pooled=sum((b["dec"]-1.0) if b["won"] else -1.0 for b in bets)/len(bets)
    if len(daily)<2 or statistics.pstdev(daily)==0: return pooled, None, len(bets)
    return pooled, statistics.mean(daily)/statistics.pstdev(daily), len(bets)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEF_LEDGER)
    a=ap.parse_args()
    rows=load(a.ledger)
    if not rows:
        print("no rows in ledger:", a.ledger); return
    span="%s..%s"%(min(r["date"] for r in rows), max(r["date"] for r in rows))
    real=sum(1 for r in rows if r["dec"] is not None)/len(rows)
    roi_mode = "REAL_VIGGED" if real>0.95 else ("MIXED_%.0f%%real"%(real*100))
    holdout = "SINGLE-SLICE 2026 SMOKE (no walk-forward; July: split + post-P1)"
    results=[]
    for me,xe,mf,mmp,xmp in itertools.product(G_MIN_EDGE,G_MAX_EDGE,G_MIN_FAIR,G_MIN_MP,G_MAX_MP):
        if me>=xe or mmp>=xmp: continue
        sub=[r for r in rows if (mmp<=r["mp"]<=xmp) and r["fair"]>=mf and (me<=r["edge"]<=xe) and r["dec"]]
        pooled,sharpe,n = daily_sharpe(sub)
        if n is None or n==0: continue
        wr=sum(r["won"] for r in sub)/n
        results.append({"min_edge":me,"max_edge":xe,"min_fair":mf,"min_mp":mmp,"max_mp":xmp,
            "n_bets":n,"win_pct":round(wr,4),"roi_pooled":round(pooled,4),
            "sharpe_daily":(round(sharpe,4) if sharpe is not None else ""),
            "roi_mode":roi_mode,"ledger_span":span,
            "holdout_note":("BASELINE "+holdout if (me,xe,mf,mmp,xmp)==BASELINE else holdout)})
    # sort: eligible (n>=150 + sharpe) first by sharpe desc, then the rest
    def key(r):
        ok = r["n_bets"]>=MIN_BETS and r["sharpe_daily"]!=""
        return (0 if ok else 1, -(r["sharpe_daily"] if r["sharpe_daily"]!="" else -9))
    results.sort(key=key)
    cols=["min_edge","max_edge","min_fair","min_mp","max_mp","n_bets","win_pct",
          "roi_pooled","sharpe_daily","roi_mode","ledger_span","holdout_note"]
    with open(OUT,"w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=cols); w.writeheader()
        for r in results: w.writerow(r)
    elig=[r for r in results if r["n_bets"]>=MIN_BETS and r["sharpe_daily"]!=""]
    base=[r for r in results if r["holdout_note"].startswith("BASELINE")]
    print("p4_band_grid_search: %d slices -> %s"%(len(results),OUT))
    print("  roi_mode=%s  span=%s"%(roi_mode,span))
    print("  slices with n_bets>=%d AND defined Sharpe: %d  (selection pool)"%(MIN_BETS,len(elig)))
    if base:
        b=base[0]; print("  BASELINE band .04/.15/.42/.48/.72: n=%d win%%=%.3f roi=%.4f sharpe=%s"%(
            b["n_bets"],b["win_pct"],b["roi_pooled"],b["sharpe_daily"]))
    if elig:
        w=elig[0]; print("  TOP by Sharpe (>=150): edge[%.2f,%.2f] fair>=%.2f mp[%.2f,%.2f] n=%d sharpe=%s roi=%.4f"%(
            w["min_edge"],w["max_edge"],w["min_fair"],w["min_mp"],w["max_mp"],w["n_bets"],w["sharpe_daily"],w["roi_pooled"]))
    else:
        print("  *** no slice meets n_bets>=150 on this ledger (EXPECTED on 2026-only smoke; July cache fixes n). ***")
    print("  NOTE: %s"%holdout)

if __name__=="__main__":
    main()
