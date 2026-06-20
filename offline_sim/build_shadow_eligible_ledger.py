#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_shadow_eligible_ledger.py  --  P3 trap-detector training scaffold (OFFLINE).

*** OFFLINE / READ-ONLY ***  Writes only offline_sim/.  No model, no docs/data, no
git, no live path.  Freeze-safe July prep (see EDGE_TIGHTENING_PREREG.md P3).

Builds a frozen ledger of EVERY band-eligible candidate (NOT just staked bets) so
the future trap detector trains on a usable n instead of the dormant-book sample.
For each diag game with a market line it records the picked-side model_prob,
fair_prob, edge_pp, model_tier, the Goldilocks-band eligibility flag, and the
actual outcome (pick_won) from statsapi finals.

Coverage note: 2026 OOS only (the diags we have with real frozen-model preds +
Kalshi fair_prob). The 2023-2025 portion needs the walk-forward backtest cache
(grid_search infra) -> a July sandbox job, not buildable from current files.

Usage: python offline_sim/build_shadow_eligible_ledger.py
Output: offline_sim/shadow_eligible_ledger.csv
"""
import os, csv, glob, re, json, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "offline_sim", "shadow_eligible_ledger.csv")
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-shadowledger/1.0(offline)"}
CANON = {"CHW":"CWS","ARI":"AZ","OAK":"ATH","WSN":"WSH","SDP":"SD","SFG":"SF","TBR":"TB","KCR":"KC"}
cn = lambda x: CANON.get(str(x).strip(), str(x).strip())
# Goldilocks band (config.py)
MIN_EDGE, MAX_EDGE = 0.04, 0.15
MIN_FAIR = 0.42
MIN_MP, MAX_MP = 0.48, 0.72

def fnum(v):
    try: return float(v)
    except: return None

def get(u, tries=3):
    last=None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e: last=e; time.sleep(0.4)
    raise last

def finals(date):
    out={}
    try: j=get("%s/schedule?sportId=1&date=%s&hydrate=team"%(API,date))
    except: return out
    for d in j.get("dates",[]):
        for g in d.get("games",[]):
            if (g.get("status") or {}).get("abstractGameState")!="Final": continue
            t=g["teams"]; ha=cn(t["home"]["team"].get("abbreviation") or ""); aa=cn(t["away"]["team"].get("abbreviation") or "")
            hs=t["home"].get("score"); as_=t["away"].get("score")
            if hs is None or as_ is None: continue
            out[(aa,ha)] = 1 if hs>as_ else 0   # home win
    return out

def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows_out=[]
    diags=sorted(glob.glob(os.path.join(ROOT,"docs","data","picks_*_diag.csv")))
    fin_cache={}
    for f in diags:
        m=re.search(r"(\d{4}-\d{2}-\d{2})",f)
        if not m: continue
        date=m.group(1)
        csv.field_size_limit(10**7)
        for r in csv.DictReader(open(f,encoding="utf-8",errors="replace")):
            mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
            if not mm: continue
            away,home=cn(mm.group(1)),cn(mm.group(2))
            pick=(r.get("pick") or "").strip()
            if not pick or pick.upper()=="TBD": continue
            model_p=fnum(r.get("pick_prob"))
            if model_p is None:
                fp=fnum(r.get("full_prob"))
                model_p = fp if (fp is not None and cn(pick)==home) else (1-fp if fp is not None else None)
            fair=fnum(r.get("fair_prob")); edge_pp=fnum(r.get("edge_pp"))
            if model_p is None or fair is None or edge_pp is None:
                continue   # no market line -> no edge -> not a candidate
            edge=edge_pp/100.0
            elig = (MIN_MP<=model_p<=MAX_MP) and (fair>=MIN_FAIR) and (MIN_EDGE<=edge<=MAX_EDGE)
            if date not in fin_cache: fin_cache[date]=finals(date)
            hw=fin_cache[date].get((away,home))
            if hw is None: continue   # not final yet -> skip (no label)
            pick_won = 1 if ((cn(pick)==home and hw==1) or (cn(pick)==away and hw==0)) else 0
            # recover the true vigged decimal odds from ev_per_dollar = p_model*dec - 1
            ev=fnum(r.get("ev_per_dollar"))
            dec = round((ev+1.0)/model_p,4) if (ev is not None and model_p) else ""
            rows_out.append({"date":date,"matchup":"%s @ %s"%(away,home),"pick":cn(pick),
                "model_prob":round(model_p,4),"fair_prob":round(fair,4),"edge_pp":round(edge_pp,2),
                "ev_per_dollar":(round(ev,4) if ev is not None else ""),"dec_odds":dec,
                "model_tier":(r.get("tier") or r.get("model_tier") or "").strip(),
                "is_eligible":int(elig),"pick_won":pick_won})
    cols=["date","matchup","pick","model_prob","fair_prob","edge_pp","ev_per_dollar","dec_odds","model_tier","is_eligible","pick_won"]
    with open(OUT,"w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=cols); w.writeheader()
        for x in rows_out: w.writerow(x)
    elig=[x for x in rows_out if x["is_eligible"]]
    span=(rows_out[0]["date"],rows_out[-1]["date"]) if rows_out else ("-","-")
    print("shadow_eligible_ledger: %d candidates (w/ market line + final), span %s..%s -> %s"%(len(rows_out),span[0],span[1],OUT))
    if elig:
        wr=sum(x["pick_won"] for x in elig)/len(elig)
        print("  band-ELIGIBLE: n=%d  win%%=%.3f"%(len(elig),wr))
        # win rate by edge bucket (the P4/trap signal)
        import collections
        buck=collections.defaultdict(lambda:[0,0])
        for x in elig:
            b="04-06pp" if x["edge_pp"]<6 else ("06-08pp" if x["edge_pp"]<8 else ("08-10pp" if x["edge_pp"]<10 else "10-15pp"))
            buck[b][0]+=x["pick_won"]; buck[b][1]+=1
        for b in ("04-06pp","06-08pp","08-10pp","10-15pp"):
            if b in buck and buck[b][1]: print("    edge %s: n=%d win%%=%.3f"%(b,buck[b][1],buck[b][0]/buck[b][1]))
    allwr=sum(x["pick_won"] for x in rows_out)/len(rows_out) if rows_out else 0
    print("  ALL candidates (any edge): n=%d win%%=%.3f"%(len(rows_out),allwr))
    import collections as _c
    tc=_c.Counter(x["model_tier"] or "(blank)" for x in rows_out)
    print("  tier distribution (all candidates):", dict(tc))
    tce=_c.Counter(x["model_tier"] or "(blank)" for x in elig)
    print("  tier distribution (band-eligible):", dict(tce))

if __name__=="__main__":
    main()
