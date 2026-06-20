#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t30_watch.py  --  Per-game T-30 refresh + LOCK.   ***FEATURE BRANCH ONLY***

Branch: feat/t30-rolling-scheduler.  NOT on main.  NOT scheduled.  SHADOW output
only (writes offline_t30/, never docs/data).  Does not change the frozen model.
Enable on return per T30_SCHEDULER_README.md after a monitoring period.

GOAL
  Refresh + lock each game ~30 minutes before its own first pitch, and once a game
  is locked, NEVER let a later refresh alter it.

HOW (robust, cron-friendly — run every ~10-15 min by a scheduler when enabled)
  Each invocation:
    1. Pull today's schedule -> per-game first-pitch (gameDate, UTC) + status.
    2. Load the persistent lock store (data/state/t30_locks_<date>.json).
    3. Read the freshest predictions (docs/data/picks_<date>_diag.csv).  With
       --rebuild, run the FROZEN slate first so the locked value is a true T-30
       refresh (off by default in shadow mode).
    4. For each game:
         due = now_utc >= firstpitch - LEAD_MIN   (or status in Live/Final)
         if due and matchup NOT already locked and the pick is real (non-TBD):
             snapshot its current prediction into the lock store  == THE T-30 LOCK
       Already-locked games are left exactly as locked (the crucial rule).
    5. Emit a locked-merged SHADOW diag: locked games show their locked snapshot;
       unlocked games show fresh values.  Written to offline_t30/, NOT docs/data.

  "Exactly 30 min" == the first tick at/after T-30 (cron granularity).  The lock
  itself is exact and immutable.

GUARDS: time window; single-instance lock; writes only offline_t30/ + data/state/;
never touches docs/data, the model, or git.  Fully reversible (delete the branch).

Usage:  python tools/t30_watch.py [YYYY-MM-DD] [--rebuild] [--lead 30] [--dry]
"""
import sys, os, csv, json, re, time, datetime, subprocess, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-t30/1.0(branch,shadow)"}
CANON = {"CHW":"CWS","ARI":"AZ","OAK":"ATH","WSN":"WSH","SDP":"SD","SFG":"SF","TBR":"TB","KCR":"KC"}
cn = lambda x: CANON.get(str(x).strip(), str(x).strip())
STATE_DIR = os.path.join("data", "state")
SHADOW_DIR = os.path.join("offline_t30")
LOCK_COLS = ["pick","pick_side","full_prob","f5_prob","fair_prob","edge_pp","model_tier","pred_winp_mc"]
WINDOW = (8, 23)          # only act during plausible game hours (local)
SINGLE_LOCK_TTL = 600

def get(u, tries=3):
    last=None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e: last=e; time.sleep(0.4)
    raise last

def now_utc(): return datetime.datetime.now(datetime.timezone.utc)

def schedule_map(date):
    """bare matchup 'AWY @ HOM' -> {first_pitch(dt utc), state}."""
    out={}
    try: j=get("%s/schedule?sportId=1&date=%s&hydrate=team"%(API,date))
    except Exception as e:
        print("t30: schedule fetch failed:",e); return out
    for d in j.get("dates",[]):
        for g in d.get("games",[]):
            t=g["teams"]; aa=cn(t["away"]["team"].get("abbreviation") or ""); ha=cn(t["home"]["team"].get("abbreviation") or "")
            gd=g.get("gameDate")
            try: fp=datetime.datetime.fromisoformat(gd.replace("Z","+00:00")) if gd else None
            except Exception: fp=None
            out["%s @ %s"%(aa,ha)]={"first_pitch":fp,"state":(g.get("status") or {}).get("abstractGameState","")}
    return out

def read_diag(date):
    p=os.path.join("docs","data","picks_%s_diag.csv"%date)
    if not os.path.exists(p): return None,[],{}
    csv.field_size_limit(10**7)
    with open(p,encoding="utf-8",errors="replace") as fh:
        rd=csv.DictReader(fh); rows=list(rd); cols=rd.fieldnames or []
    by={}
    for r in rows:
        mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
        if mm: by["%s @ %s"%(cn(mm.group(1)),cn(mm.group(2)))]=r
    return cols,rows,by

def main():
    raw=sys.argv[1:]; dry="--dry" in raw; rebuild="--rebuild" in raw
    lead=30
    if "--lead" in raw:
        try: lead=int(raw[raw.index("--lead")+1])
        except Exception: lead=30
    pos=[a for a in raw if not a.startswith("--") and not a.isdigit()]
    date=pos[0] if pos else now_utc().date().isoformat()

    os.makedirs(STATE_DIR,exist_ok=True); os.makedirs(SHADOW_DIR,exist_ok=True)
    lh=os.path.join(STATE_DIR,"t30_watch.lock")
    if os.path.exists(lh) and (time.time()-os.path.getmtime(lh))<SINGLE_LOCK_TTL:
        print("t30: another instance in flight -> skip"); return
    open(lh,"w").write(str(os.getpid()))
    try:
        hr=datetime.datetime.now().hour
        if not (WINDOW[0]<=hr<=WINDOW[1]):
            print("t30: %02d:00 outside window %s -> noop"%(hr,WINDOW)); return

        if rebuild and not dry:
            print("t30: --rebuild -> running frozen slate for",date)
            subprocess.run([sys.executable,"tools/run_local_slate.py",date],check=False,cwd=ROOT)

        sched=schedule_map(date)
        cols,rows,by=read_diag(date)
        if not cols:
            print("t30: no diag for",date,"-> nothing to lock"); return

        lp=os.path.join(STATE_DIR,"t30_locks_%s.json"%date)
        locks=json.load(open(lp)) if os.path.exists(lp) else {}

        newly=[]
        for mk,info in sched.items():
            fp=info["first_pitch"]; state=info["state"]
            due = (state in ("Live","Final")) or (fp is not None and now_utc() >= fp - datetime.timedelta(minutes=lead))
            if not due or mk in locks: continue
            row=by.get(mk)
            if not row: continue
            pick=(row.get("pick") or "").strip()
            if not pick or pick.upper()=="TBD": continue   # don't lock a non-pick
            locks[mk]={c:row.get(c) for c in LOCK_COLS}
            locks[mk]["locked_at_utc"]=now_utc().isoformat(timespec="seconds")
            locks[mk]["first_pitch"]=fp.isoformat() if fp else None
            locks[mk]["lead_min"]=lead
            newly.append(mk)

        if not dry:
            json.dump(locks,open(lp,"w"),indent=1)

        # locked-merged SHADOW diag (locked rows overwrite fresh; unlocked stay fresh)
        merged=[]
        for r in rows:
            mm=re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})",r.get("matchup") or "")
            mk="%s @ %s"%(cn(mm.group(1)),cn(mm.group(2))) if mm else None
            rr=dict(r)
            if mk and mk in locks:
                for c in LOCK_COLS:
                    if locks[mk].get(c) is not None: rr[c]=locks[mk][c]
                sw=(rr.get("stress_warnings") or "")
                if "locked_at_T30" not in sw:
                    rr["stress_warnings"]=(sw+";locked_at_T30") if sw else "locked_at_T30"
            merged.append(rr)
        out_cols=cols+([] if "stress_warnings" in cols else ([ "stress_warnings"] if any("stress_warnings" in m for m in merged) else []))
        outp=os.path.join(SHADOW_DIR,"picks_%s_diag_LOCKED.csv"%date)
        if not dry:
            with open(outp,"w",newline="",encoding="utf-8") as fh:
                w=csv.DictWriter(fh,fieldnames=out_cols,extrasaction="ignore"); w.writeheader()
                for m in merged: w.writerow(m)

        print("t30 %s (lead %dmin%s): %d games scheduled, %d locked total, %d newly locked %s"%(
            date,lead," DRY" if dry else "",len(sched),len(locks),len(newly),newly))
        print("  shadow diag -> %s (NOT published; main untouched)"%outp)
    finally:
        try: os.remove(lh)
        except OSError: pass

if __name__=="__main__":
    main()
