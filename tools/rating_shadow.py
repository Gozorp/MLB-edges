#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rating_shadow.py -- 5-day player-driven 0-100 ECOSYSTEM ratings + a SHADOW
decompression overlay.  DISPLAY / SHADOW ONLY -- never feeds the frozen XGBoost
model, the picks, or any stake.  (Track 1 of the user's two-track plan; Track 2 =
July Model-B sandbox retrain, see ECOSYSTEM_RATING_SPEC.md.)

WHAT IT DOES (statsapi-only -- no new external feed before the unattended trip):
  * Batter layer (0-100): per hitter from OPS (+) and K% (-), league-normalized.
    Team batter rating = mean of the team's top-9-by-PA hitters. (Hard-Hit% =
    July add via Savant; lineup-specific avg = refinement once lineups post.)
  * SP layer (0-100): per pitcher from K-BB% (+), WHIP (-), HR/9 (-). Today's
    projected starter (diag home/away_sp_name) drives the game; TBD -> staff mean.
  * BP layer (0-100): team relief execution (K-BB%, WHIP) blended with current
    fatigue from bullpen_meta (rest / strain tier).
  * Team Ecosystem Score (game day) = 0.45*batter + 0.35*SP_today + 0.20*BP.
  * Interval_Delta = clamp(K*(W - E), -CAP, +CAP) over the trailing 5-day window,
    where W = actual wins, E = sum of the frozen model's pre-game win probs for
    those games (K=2.5, CAP=7).  Expectation-relative, per the user's formula.
  * SHADOW decompression: the tier report showed the frozen model recovers <half
    the true tier spread.  For each game we log a candidate decompressed prob:
    adj_logit = logit(raw_home_prob) + GAMMA * z(eco_diff); confidence_delta =
    adj - raw.  LOGGED ONLY, NOT applied to any pick.  GAMMA is an untuned v0 to
    be EVALUATED (not tuned on the same data) -- see the spec's pass/fail bar.

Writes docs/data/rating_shadow_<date>.json.  Fully sandboxed.
Usage: python tools/rating_shadow.py [YYYY-MM-DD]
"""
import sys, os, csv, json, re, time, glob, math, datetime, statistics, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-ratingshadow/1.0"}
CANON = {"CHW":"CWS","ARI":"AZ","OAK":"ATH","WSN":"WSH","SDP":"SD","SFG":"SF","TBR":"TB","KCR":"KC"}
cn = lambda x: CANON.get(str(x).strip(), str(x).strip())

# ---- weights / constants (documented; tune only in the July sandbox) ----
W_BAT, W_SP, W_BP = 0.45, 0.35, 0.20
K_DELTA, DELTA_CAP = 2.5, 7.0
INTERVAL_DAYS = 5
GAMMA = 0.35            # shadow decompression strength (untuned v0)
LINEUP_N = 9


def get(u, tries=3):
    last=None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last=e; time.sleep(0.4)
    raise last

def fnum(v):
    try: return float(v)
    except: return None

def ip_outs(ip):
    f=fnum(ip)
    if f is None: return 0
    w=int(f); fr=round((f-w)*10); fr=0 if fr>2 else fr
    return w*3+fr

def _norm_name(s):
    import unicodedata
    s=unicodedata.normalize("NFKD",s or "").encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z ]","",s).strip()

def ncdf(z):  # standard normal CDF, no scipy
    return 0.5*(1+math.erf(z/math.sqrt(2)))

def zmap(val, mean, sd, sign=1):
    if val is None or sd in (None,0): return None
    return sign*(val-mean)/sd

def to100(z):
    if z is None: return None
    return round(100*ncdf(z),1)


def slate_games(date):
    p=os.path.join("docs","data","picks_%s_diag.csv"%date)
    if not os.path.exists(p): p="picks_%s_diag.csv"%date
    if not os.path.exists(p): return []
    csv.field_size_limit(10**7)
    out=[]
    for r in csv.DictReader(open(p,encoding="utf-8",errors="replace")):
        mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
        if not mm: continue
        fp=fnum(r.get("full_prob")) or fnum(r.get("pick_prob"))
        out.append({"matchup":(r.get("matchup") or "").strip(),
                    "away":cn(mm.group(1)),"home":cn(mm.group(2)),
                    "home_prob":fp,
                    "home_sp":(r.get("home_sp_name") or "").strip(),
                    "away_sp":(r.get("away_sp_name") or "").strip()})
    return out


def team_id_map(season):
    out={}
    for t in get("%s/teams?sportId=1&season=%d"%(API,season)).get("teams",[]):
        if t.get("id"): out[t["id"]]=cn(t.get("abbreviation") or "")
    return out


def build_player_ratings(season, id2abbr):
    """team -> {'batters':[(name,rating,pa)], 'sp':{normname:rating}, 'bp_z_parts'}"""
    # hitters
    hj=get("%s/stats?stats=season&group=hitting&season=%d&sportId=1&playerPool=all&limit=4000"%(API,season))
    hrows=[]
    for sp in hj.get("stats",[{}])[0].get("splits",[]):
        st=sp.get("stat") or {}; ab=cn((sp.get("team") or {}).get("abbreviation") or id2abbr.get((sp.get("team") or {}).get("id")) or "")
        pa=fnum(st.get("plateAppearances")) or 0; ops=fnum(st.get("ops"))
        kpct=(100*(fnum(st.get("strikeOuts")) or 0)/pa) if pa else None
        if pa<60 or ops is None: continue
        hrows.append({"team":ab,"name":sp.get("player",{}).get("fullName","?"),"pa":pa,"ops":ops,"k":kpct})
    ops_mean=statistics.mean([h["ops"] for h in hrows]); ops_sd=statistics.pstdev([h["ops"] for h in hrows]) or 1e-9
    k_mean=statistics.mean([h["k"] for h in hrows if h["k"] is not None]); k_sd=statistics.pstdev([h["k"] for h in hrows if h["k"] is not None]) or 1e-9
    batters=defaultdict(list)
    for h in hrows:
        z=0.75*zmap(h["ops"],ops_mean,ops_sd,1)+0.25*zmap(h["k"],k_mean,k_sd,-1)
        batters[h["team"]].append((h["name"],to100(z),h["pa"]))
    # pitchers
    pj=get("%s/stats?stats=season&group=pitching&season=%d&sportId=1&playerPool=all&limit=4000"%(API,season))
    sprows=[]; bp_by_team=defaultdict(lambda: defaultdict(float))
    for sp in pj.get("stats",[{}])[0].get("splits",[]):
        st=sp.get("stat") or {}; ab=cn((sp.get("team") or {}).get("abbreviation") or id2abbr.get((sp.get("team") or {}).get("id")) or "")
        gp=fnum(st.get("gamesPitched")) or 0; gs=fnum(st.get("gamesStarted")) or 0
        outs=ip_outs(st.get("inningsPitched"))
        if outs<=0 or not ab: continue
        bf=fnum(st.get("battersFaced")) or 0; so=fnum(st.get("strikeOuts")) or 0; bb=fnum(st.get("baseOnBalls")) or 0
        h=fnum(st.get("hits")) or 0; hr=fnum(st.get("homeRuns")) or 0
        kbb=(100*(so-bb)/bf) if bf else None; whip=(bb+h)/(outs/3); hr9=9*hr/(outs/3)
        is_sp=gs>=1 and (gs/gp)>=0.5
        if is_sp and bf>=40:
            sprows.append({"team":ab,"name":sp.get("player",{}).get("fullName","?"),"kbb":kbb,"whip":whip,"hr9":hr9})
        elif not is_sp:
            a=bp_by_team[ab]
            for k,v in (("outs",outs),("bb",bb),("h",h),("so",so),("bf",bf)): a[k]+=v
    # SP ratings
    kbbs=[s["kbb"] for s in sprows if s["kbb"] is not None]; whips=[s["whip"] for s in sprows]; hr9s=[s["hr9"] for s in sprows]
    kbb_m,kbb_s=statistics.mean(kbbs),statistics.pstdev(kbbs) or 1e-9
    whip_m,whip_s=statistics.mean(whips),statistics.pstdev(whips) or 1e-9
    hr9_m,hr9_s=statistics.mean(hr9s),statistics.pstdev(hr9s) or 1e-9
    sp_rating=defaultdict(dict); sp_team=defaultdict(list)
    for s in sprows:
        z=0.5*zmap(s["kbb"],kbb_m,kbb_s,1)+0.3*zmap(s["whip"],whip_m,whip_s,-1)+0.2*zmap(s["hr9"],hr9_m,hr9_s,-1)
        r=to100(z); sp_rating[s["team"]][_norm_name(s["name"])]=r; sp_team[s["team"]].append(r)
    # BP execution rating (team) via relief K-BB% and WHIP, league-normalized
    bp_exec={}; teams=list(bp_by_team.keys())
    bp_kbb={}; bp_whip={}
    for t,a in bp_by_team.items():
        ip=a["outs"]/3 or 1; bp_kbb[t]=(100*(a["so"]-a["bb"])/a["bf"]) if a["bf"] else None; bp_whip[t]=(a["bb"]+a["h"])/ip
    kv=[v for v in bp_kbb.values() if v is not None]; wv=[v for v in bp_whip.values() if v is not None]
    km,ks=statistics.mean(kv),statistics.pstdev(kv) or 1e-9; wm,ws=statistics.mean(wv),statistics.pstdev(wv) or 1e-9
    for t in teams:
        z=0.6*zmap(bp_kbb[t],km,ks,1)+0.4*zmap(bp_whip[t],wm,ws,-1) if bp_kbb[t] is not None else 0
        bp_exec[t]=to100(z)
    return batters, sp_rating, sp_team, bp_exec


def bp_health(date, bp_exec):
    """blend relief execution (0-100) with current fatigue from bullpen_meta."""
    out={}
    bm=None
    for p in ("docs/data/bullpen_meta_%s.json"%date,):
        if os.path.exists(p):
            try: bm=json.load(open(p))
            except: bm=None
    fat={}
    if bm:
        for ab,info in bm.get("teams",{}).items():
            s=info.get("team_summary") or {}; tier=(s.get("ceiling_tier") or "").upper()
            pen={"STRAINED":-12,"HIGH":-8,"ELEVATED":-8,"MODERATE":-3,"NORMAL":0,"FRESH":+5}.get(tier,0)
            fat[cn(ab)]=pen
    for t,ex in bp_exec.items():
        out[t]=round(max(0,min(100,(ex if ex is not None else 50)+fat.get(t,0))),1)
    return out


def interval_delta(date):
    """K*(W-E) per team over the trailing INTERVAL_DAYS, from diags + statsapi finals."""
    d0=datetime.date.fromisoformat(date)
    win=[(d0-datetime.timedelta(days=i)).isoformat() for i in range(1,INTERVAL_DAYS+1)]
    W=defaultdict(float); E=defaultdict(float)
    # team-perspective expected wins from each game's home_prob, actual from finals
    id2=None
    for dt in win:
        p=os.path.join("docs","data","picks_%s_diag.csv"%dt)
        if not os.path.exists(p): continue
        games={}
        csv.field_size_limit(10**7)
        for r in csv.DictReader(open(p,encoding="utf-8",errors="replace")):
            mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
            fp=fnum(r.get("full_prob")) or fnum(r.get("pick_prob"))
            if not mm or fp is None: continue
            games[(cn(mm.group(1)),cn(mm.group(2)))]=fp
        if not games: continue
        try: j=get("%s/schedule?sportId=1&date=%s&hydrate=team"%(API,dt))
        except: continue
        for dd in j.get("dates",[]):
            for gm in dd.get("games",[]):
                if (gm.get("status") or {}).get("abstractGameState")!="Final": continue
                tt=gm["teams"]; ha=cn(tt["home"]["team"].get("abbreviation") or ""); aa=cn(tt["away"]["team"].get("abbreviation") or "")
                hs=tt["home"].get("score"); as_=tt["away"].get("score")
                if hs is None: continue
                fp=games.get((aa,ha))
                if fp is None: continue
                E[ha]+=fp; E[aa]+=(1-fp)
                W[ha]+=1 if hs>as_ else 0; W[aa]+=1 if as_>hs else 0
    delta={}
    for t in set(list(W)+list(E)):
        delta[t]=round(max(-DELTA_CAP,min(DELTA_CAP,K_DELTA*(W[t]-E[t]))),2)
    return delta, {t:(round(W[t],1),round(E[t],2)) for t in set(list(W)+list(E))}


def main(date):
    season=int(date[:4])
    id2=team_id_map(season)
    batters, sp_rating, sp_team, bp_exec = build_player_ratings(season, id2)
    bph = bp_health(date, bp_exec)
    delta, we = interval_delta(date)

    def team_batter(t):
        lst=sorted(batters.get(t,[]),key=lambda x:-x[2])[:LINEUP_N]
        vals=[x[1] for x in lst if x[1] is not None]
        return round(statistics.mean(vals),1) if vals else 50.0
    def sp_for(t, name):
        r=sp_rating.get(t,{}).get(_norm_name(name)) if name and name.upper()!="TBD" else None
        if r is not None: return r, "named"
        st=sp_team.get(t,[])
        return (round(statistics.mean(st),1) if st else 50.0), ("staff_avg" if st else "default")

    teams_out={}
    games=slate_games(date)
    eco={}
    for g in games:
        for side,t,spn in (("home",g["home"],g["home_sp"]),("away",g["away"],g["away_sp"])):
            if t in eco: continue
            bat=team_batter(t); spr,spsrc=sp_for(t,spn); bp=bph.get(t,50.0)
            score=round(W_BAT*bat+W_SP*spr+W_BP*bp,1)
            eco[t]={"batter":bat,"sp_today":spr,"sp_src":spsrc,"bp_health":bp,
                    "ecosystem_score":score,"interval_delta":delta.get(t,0.0),
                    "interval_W_E":we.get(t)}
    # decompression shadow per game
    scores=[v["ecosystem_score"]+v["interval_delta"] for v in eco.values()] or [50]
    em=statistics.mean(scores); es=statistics.pstdev(scores) or 1e-9
    games_out=[]
    for g in games:
        he=eco.get(g["home"]); ae=eco.get(g["away"])
        rp=g["home_prob"]
        row={"matchup":g["matchup"],"home":g["home"],"away":g["away"],
             "raw_home_prob":round(rp,4) if rp is not None else None,
             "home_eco":round((he["ecosystem_score"]+he["interval_delta"]),1) if he else None,
             "away_eco":round((ae["ecosystem_score"]+ae["interval_delta"]),1) if ae else None}
        if rp is not None and he and ae:
            ediff_z=((he["ecosystem_score"]+he["interval_delta"])-(ae["ecosystem_score"]+ae["interval_delta"]))/es
            p=min(.9999,max(.0001,rp))
            adj=1/(1+math.exp(-(math.log(p/(1-p))+GAMMA*ediff_z)))
            row["shadow_decompressed_prob"]=round(adj,4)
            row["confidence_delta_pp"]=round(100*(adj-p),1)
        games_out.append(row)

    out={"date":date,"generated_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
         "schema":"v1-shadow","scope":"DISPLAY/SHADOW ONLY -- never feeds model/picks/stake",
         "weights":{"batter":W_BAT,"sp":W_SP,"bp":W_BP,"K_delta":K_DELTA,"delta_cap":DELTA_CAP,
                    "interval_days":INTERVAL_DAYS,"gamma":GAMMA,"lineup_n":LINEUP_N},
         "method":("0-100 ecosystem = 0.45*batter(top9 OPS/K%) + 0.35*SP_today(K-BB/WHIP/HR9) + 0.20*BP(relief exec + bullpen_meta fatigue); "
                   "Interval_Delta=clamp(2.5*(W-E),-7,7) trailing 5d; shadow decompress logit += 0.35*z(eco_diff). statsapi-only. Hard-Hit%/lineup-specific = July."),
         "teams":eco,"games":games_out}
    p=os.path.join("docs","data","rating_shadow_%s.json"%date)
    with open(p+".tmp","w",encoding="utf-8") as f: json.dump(out,f,indent=1)
    os.replace(p+".tmp",p)
    print("rating_shadow %s: %d teams, %d games -> %s"%(date,len(eco),len(games_out),p))
    for t in sorted(eco,key=lambda x:-(eco[x]["ecosystem_score"]+eco[x]["interval_delta"]))[:6]:
        e=eco[t]; print("  %-4s eco %.1f (bat %.0f sp %.0f bp %.0f) + delta %+.1f  W-E %s"%(
            t,e["ecosystem_score"]+e["interval_delta"],e["batter"],e["sp_today"],e["bp_health"],e["interval_delta"],e["interval_W_E"]))
    flips=[g for g in games_out if g.get("confidence_delta_pp") is not None and abs(g["confidence_delta_pp"])>=4]
    print("  shadow moves >=4pp:",len(flips))
    for g in sorted(flips,key=lambda x:-abs(x["confidence_delta_pp"]))[:6]:
        print("    %-16s raw %.2f -> shadow %.2f (%+.1fpp) eco %s vs %s"%(
            g["matchup"][:16],g["raw_home_prob"],g["shadow_decompressed_prob"],g["confidence_delta_pp"],g["home_eco"],g["away_eco"]))


if __name__=="__main__":
    d=sys.argv[1] if len(sys.argv)>1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    main(d)
