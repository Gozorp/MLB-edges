#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oos_ledger.py -- append-only out-of-sample prediction ledger (FROZEN-window).
Pure logging: never touches the model, parlay_builder, the brain, or any pick.
Captures the model's RAW pre-executive win prob (pick_prob) at slate build, then
scores it against finals once games go Final. Fully sandboxed.
Ledger: docs/data/oos_ledger.jsonl (append-only JSONL). Usage: oos_ledger.py [YYYY-MM-DD]
"""
import sys, os, csv, json, hashlib, datetime, urllib.request
ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.environ.get("OOS_LEDGER") or os.path.join(ROOT, "docs", "data", "oos_ledger.jsonl")
API="https://statsapi.mlb.com/api/v1"; UA={"User-Agent":"mlb_edge-oosledger/1.0"}
CANON={"CWS":"CHW","AZ":"ARI","ATH":"OAK","WSN":"WSH","SDP":"SD","SFG":"SF","TBR":"TB","KCR":"KC"}
def canon(x): return CANON.get(str(x).strip(), str(x).strip())
def _utc(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def _sig(p):
    try:
        with open(p,"rb") as f: return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception: return None
def _f(x):
    try: return float(x)
    except Exception: return None
def _read():
    out=[]
    if os.path.exists(LEDGER):
        for line in open(LEDGER,encoding="utf-8"):
            line=line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    return out
def _append(rec):
    with open(LEDGER,"a",encoding="utf-8") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
def _finals(date):
    url="%s/schedule?sportId=1&date=%s&hydrate=team,linescore"%(API,date)
    j=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=25)); out={}
    for d in j.get("dates",[]):
        for g in d.get("games",[]):
            t=g.get("teams",{}); ls=g.get("linescore",{}).get("teams",{})
            a=canon(((t.get("away") or {}).get("team") or {}).get("abbreviation"))
            h=canon(((t.get("home") or {}).get("team") or {}).get("abbreviation"))
            ar=(ls.get("away") or {}).get("runs"); hr=(ls.get("home") or {}).get("runs")
            out[(a,h)]=(g.get("status",{}).get("detailedState",""),ar,hr)
    return out
def log_predictions(slate):
    path=os.path.join(ROOT,"docs","data","picks_%s_diag.csv"%slate)
    if not os.path.exists(path): path=os.path.join(ROOT,"picks_%s_diag.csv"%slate)
    if not os.path.exists(path): print("[oos] no diag for %s"%slate); return 0
    seen={(r.get("slate_date"),r.get("matchup")) for r in _read() if r.get("phase")=="predict"}
    cal=_sig(os.path.join(ROOT,"models","calibration_v1.json"))
    frozen=os.path.exists(os.path.join(ROOT,"data","state","weights_freeze.json"))
    csv.field_size_limit(10**7); n=0
    for r in csv.DictReader(open(path,encoding="utf-8",errors="replace")):
        m=(r.get("matchup") or "").strip()
        if not m or "@" not in m: continue
        if (slate,m) in seen: continue
        away,home=[x.strip() for x in m.split("@")]; pick=(r.get("pick") or "").strip(); pp=_f(r.get("pick_prob"))
        rec={"phase":"predict","slate_date":slate,"logged_at_utc":_utc(),"matchup":m,
             "away":canon(away),"home":canon(home),"pick":pick,
             "pick_side":("home" if pick and canon(pick)==canon(home) else "away" if pick and canon(pick)==canon(away) else None),
             "pick_prob":pp,"f5_prob":_f(r.get("f5_prob")),"full_prob":_f(r.get("full_prob")),
             "f5_full_delta":_f(r.get("f5_full_delta")),"fair_prob":_f(r.get("fair_prob")),"edge_pp":_f(r.get("edge_pp")),
             "pre_cap_grade":(r.get("pre_cap_grade") or "").strip() or None,
             "post_cap_grade":(r.get("grade") or "").strip() or None,
             "model_tier":(r.get("tier") or "").strip() or None,
             "has_pick":bool(pp is not None and pick not in ("","TBD","NO_PICK")),
             "weights_frozen":frozen,"calibrator_sig":cal}
        _append(rec); seen.add((slate,m)); n+=1
    print("[oos] +%d predictions for %s"%(n,slate)); return n
def finalize():
    led=_read()
    preds={(r["slate_date"],r["matchup"]):r for r in led if r.get("phase")=="predict"}
    done={(r["slate_date"],r["matchup"]) for r in led if r.get("phase")=="result"}
    pend={}
    for k,r in preds.items():
        if k not in done: pend.setdefault(k[0],[]).append(r)
    n=0
    for date,recs in pend.items():
        try: F=_finals(date)
        except Exception as e: print("[oos] finals %s failed: %s"%(date,e)); continue
        for r in recs:
            fin=F.get((canon(r["away"]),canon(r["home"])))
            if not fin: continue
            st,ar,hr=fin
            if st!="Final" or ar is None or hr is None:
                if st in ("Postponed","Cancelled","Suspended"):
                    _append({"phase":"result","slate_date":date,"matchup":r["matchup"],"scored_at_utc":_utc(),"status":st,"voided":True}); n+=1
                continue
            winner=canon(r["away"]) if ar>hr else canon(r["home"]); pp=r.get("pick_prob")
            if not r.get("has_pick") or pp is None:
                _append({"phase":"result","slate_date":date,"matchup":r["matchup"],"scored_at_utc":_utc(),"status":"Final","away_runs":ar,"home_runs":hr,"winner":winner,"no_pick":True}); n+=1; continue
            outcome=1 if canon(r.get("pick"))==winner else 0
            _append({"phase":"result","slate_date":date,"matchup":r["matchup"],"scored_at_utc":_utc(),"status":"Final","away_runs":ar,"home_runs":hr,"winner":winner,"pick":r.get("pick"),"pick_prob":pp,"outcome":outcome,"pick_correct":bool(outcome),"brier":round((pp-outcome)**2,4)}); n+=1
    print("[oos] finalized %d"%n); return n
def main():
    slate=sys.argv[1] if len(sys.argv)>1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try: finalize()
    except Exception as e: print("[oos] finalize err (non-fatal): %s"%e)
    try: log_predictions(slate)
    except Exception as e: print("[oos] predict err (non-fatal): %s"%e)
if __name__=="__main__": main()
