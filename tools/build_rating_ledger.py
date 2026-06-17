#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rating_ledger.py -- Track-2 (July sandbox) prep. READ-ONLY.

Recomputes Team_Ecosystem_Score + Interval_Delta AS-OF each historical game date
(leakage-safe: team stats through date-1; interval from the prior 5 days), joined to
the frozen model's home_prob, the actual outcome, and the team tier. Output CSV feeds
the July Model-B isolation test (see ECOSYSTEM_RATING_SPEC.md). Builds NO model.

As-of ratings here are TEAM-LEVEL via statsapi byDateRange (the live shadow uses
player-level current-season ratings; player-level historical = per-player byDateRange,
a July compute decision). Composite uses fixed current-season league means as the
normalizer (documented); raw components are also emitted so the sandbox can rescale.

Usage: python tools/build_rating_ledger.py [START YYYY-MM-DD] [END YYYY-MM-DD]
       (no args -> validation slice = last 2 diag dates)
Output: data/rating_feature_ledger.csv  (append-safe; dedups by date+matchup)
"""
import sys, os, csv, json, re, time, glob, math, datetime, statistics, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent":"mlb_edge-ratingledger/1.0"}
CANON = {"CHW":"CWS","ARI":"AZ","OAK":"ATH","WSN":"WSH","SDP":"SD","SFG":"SF","TBR":"TB","KCR":"KC"}
cn=lambda x: CANON.get(str(x).strip(),str(x).strip())
SEASON_START="2026-03-01"; K_DELTA,DELTA_CAP,INTERVAL_DAYS=2.5,7.0,5
W_BAT,W_ARM=0.55,0.45  # team-level ledger: batter vs combined-arm (SP+BP merged at team level)

def get(u,tries=3):
    last=None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e: last=e; time.sleep(0.4)
    raise last
def fnum(v):
    try: return float(v)
    except: return None
def ip_outs(ip):
    f=fnum(ip);
    if f is None: return 0
    w=int(f); fr=round((f-w)*10); fr=0 if fr>2 else fr; return w*3+fr
def ncdf(z): return 0.5*(1+math.erf(z/math.sqrt(2)))

# tiers
tj=json.load(open("docs/data/team_tiers.json"))
abbr2tier={}
for tier,teams in tj["tiers"].items():
    for t in teams: abbr2tier[cn(t["abbr"])]=tier

# team id map
id2abbr={}; abbr2id={}
for t in get("%s/teams?sportId=1&season=2026"%API).get("teams",[]):
    if t.get("id"): id2abbr[t["id"]]=cn(t.get("abbreviation") or ""); abbr2id[cn(t.get("abbreviation") or "")]=t["id"]

# fixed league normalizers (current season) for the 0-100 composite
def league_norms():
    hj=get("%s/teams/stats?stats=season&group=hitting&season=2026&sportId=1"%API)
    ops=[]; kp=[]
    for sp in hj.get("stats",[{}])[0].get("splits",[]):
        st=sp.get("stat") or {}; o=fnum(st.get("ops")); pa=fnum(st.get("plateAppearances")) or 0
        so=fnum(st.get("strikeOuts")) or 0
        if o is not None: ops.append(o)
        if pa: kp.append(100*so/pa)
    pj=get("%s/teams/stats?stats=season&group=pitching&season=2026&sportId=1"%API)
    era=[]; kbb=[]
    for sp in pj.get("stats",[{}])[0].get("splits",[]):
        st=sp.get("stat") or {}; e=fnum(st.get("era")); bf=fnum(st.get("battersFaced")) or 0
        so=fnum(st.get("strikeOuts")) or 0; bb=fnum(st.get("baseOnBalls")) or 0
        if e is not None: era.append(e)
        if bf: kbb.append(100*(so-bb)/bf)
    m=lambda a:(statistics.mean(a),statistics.pstdev(a) or 1e-9)
    return {"ops":m(ops),"kp":m(kp),"era":m(era),"kbb":m(kbb)}
LN=league_norms()

_cache={}
def team_asof(team_id, end_date, group):
    k=(team_id,end_date,group)
    if k in _cache: return _cache[k]
    u="%s/teams/%s/stats?stats=byDateRange&group=%s&startDate=%s&endDate=%s&season=2026&sportId=1"%(API,team_id,group,SEASON_START,end_date)
    try:
        j=get(u); sp=j.get("stats",[{}])[0].get("splits",[])
        st=sp[0].get("stat") if sp else {}
    except Exception:
        st={}
    _cache[k]=st or {}; return _cache[k]

def eco_asof(ab, end_date):
    tid=abbr2id.get(ab)
    if not tid: return None
    h=team_asof(tid,end_date,"hitting"); p=team_asof(tid,end_date,"pitching")
    ops=fnum(h.get("ops")); pa=fnum(h.get("plateAppearances")) or 0; so=fnum(h.get("strikeOuts")) or 0
    kp=(100*so/pa) if pa else None
    era=fnum(p.get("era")); bf=fnum(p.get("battersFaced")) or 0; pso=fnum(p.get("strikeOuts")) or 0; pbb=fnum(p.get("baseOnBalls")) or 0
    kbb=(100*(pso-pbb)/bf) if bf else None
    if ops is None or era is None: return None
    zb=0.75*((ops-LN["ops"][0])/LN["ops"][1]) + 0.25*(-((kp-LN["kp"][0])/LN["kp"][1]) if kp is not None else 0)
    za=0.5*(-((era-LN["era"][0])/LN["era"][1])) + 0.5*(((kbb-LN["kbb"][0])/LN["kbb"][1]) if kbb is not None else 0)
    bat100=100*ncdf(zb); arm100=100*ncdf(za)
    return {"bat":round(bat100,1),"arm":round(arm100,1),
            "eco":round(W_BAT*bat100+W_ARM*arm100,1),
            "ops":ops,"kp":round(kp,1) if kp is not None else None,"era":era,
            "kbb":round(kbb,1) if kbb is not None else None}

def games_on(date):
    p="docs/data/picks_%s_diag.csv"%date
    if not os.path.exists(p): return []
    csv.field_size_limit(10**7); out=[]
    for r in csv.DictReader(open(p,encoding="utf-8",errors="replace")):
        mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
        fp=fnum(r.get("full_prob")) or fnum(r.get("pick_prob"))
        if not mm or fp is None: continue
        out.append((cn(mm.group(1)),cn(mm.group(2)),fp))
    return out

def finals_on(date):
    out={}
    try: j=get("%s/schedule?sportId=1&date=%s&hydrate=team"%(API,date))
    except: return out
    for d in j.get("dates",[]):
        for gm in d.get("games",[]):
            if (gm.get("status") or {}).get("abstractGameState")!="Final": continue
            tt=gm["teams"]; ha=cn(tt["home"]["team"].get("abbreviation") or ""); aa=cn(tt["away"]["team"].get("abbreviation") or "")
            hs=tt["home"].get("score"); as_=tt["away"].get("score")
            if hs is None: continue
            out[(aa,ha)]=(1 if hs>as_ else 0)
    return out

def interval_delta_asof(date):
    d0=datetime.date.fromisoformat(date)
    W=defaultdict(float); E=defaultdict(float)
    for i in range(1,INTERVAL_DAYS+1):
        dt=(d0-datetime.timedelta(days=i)).isoformat()
        g=games_on(dt); f=finals_on(dt) if g else {}
        gp={(a,h):fp for a,h,fp in g}
        for (a,h),won in f.items():
            fp=gp.get((a,h))
            if fp is None: continue
            E[h]+=fp; E[a]+=1-fp; W[h]+=won; W[a]+=1-won
    return {t:round(max(-DELTA_CAP,min(DELTA_CAP,K_DELTA*(W[t]-E[t]))),2) for t in set(list(W)+list(E))}

def main():
    args=[a for a in sys.argv[1:]]
    diag_dates=sorted(re.search(r"(\d{4}-\d{2}-\d{2})",f).group(1) for f in glob.glob("docs/data/picks_*_diag.csv"))
    if len(args)>=2: dates=[d for d in diag_dates if args[0]<=d<=args[1]]
    else: dates=diag_dates[-2:]  # validation slice
    outp="data/rating_feature_ledger.csv"
    os.makedirs("data",exist_ok=True)
    seen=set()
    if os.path.exists(outp):
        for r in csv.DictReader(open(outp,encoding="utf-8")): seen.add((r["date"],r["away"],r["home"]))
    new=0; rows=[]
    for date in dates:
        g=games_on(date);
        if not g: continue
        f=finals_on(date); delta=interval_delta_asof(date)
        end=(datetime.date.fromisoformat(date)-datetime.timedelta(days=1)).isoformat()
        for a,h,fp in g:
            if (date,a,h) in seen: continue
            won=f.get((a,h))
            if won is None: continue
            he=eco_asof(h,end); ae=eco_asof(a,end)
            if not he or not ae: continue
            rows.append({"date":date,"away":a,"home":h,"home_prob":round(fp,4),"home_won":won,
                "ht_tier":abbr2tier.get(h,"?"),"at_tier":abbr2tier.get(a,"?"),
                "home_eco":he["eco"],"away_eco":ae["eco"],"eco_diff":round(he["eco"]-ae["eco"],1),
                "home_delta":delta.get(h,0.0),"away_delta":delta.get(a,0.0),
                "home_bat":he["bat"],"home_arm":he["arm"],"away_bat":ae["bat"],"away_arm":ae["arm"]})
            new+=1
    cols=["date","away","home","home_prob","home_won","ht_tier","at_tier","home_eco","away_eco","eco_diff","home_delta","away_delta","home_bat","home_arm","away_bat","away_arm"]
    mode="a" if os.path.exists(outp) else "w"
    with open(outp,mode,newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=cols)
        if mode=="w": w.writeheader()
        for r in rows: w.writerow(r)
    print("rating_feature_ledger: +%d rows over %s..%s -> %s (total dates scanned %d)"%(new,dates[0] if dates else "-",dates[-1] if dates else "-",outp,len(dates)))
    for r in rows[:6]:
        print("  %s %s@%s prob %.2f won %d | eco %s vs %s (diff %+.1f) delta %+.1f/%+.1f"%(
            r["date"],r["away"],r["home"],r["home_prob"],r["home_won"],r["home_eco"],r["away_eco"],r["eco_diff"],r["home_delta"],r["away_delta"]))

if __name__=="__main__":
    main()
