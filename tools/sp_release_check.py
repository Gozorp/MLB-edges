# -*- coding: utf-8 -*-
"""
sp_release_check.py -- short-lived SP-release watcher. Run on a SCHEDULE (Windows
schtasks / cron), NOT as a while-True daemon: each invocation checks once and exits,
so a crash just misses one tick and the next tick self-recovers.

CHECK CHEAP, ACT RARELY:
  * ONE statsapi /schedule call lists every game's probable (a few KB).
  * It rebuilds the slate ONLY when a game the LAST build left PENDING (probable not
    yet announced, parsed from the diag's why_skipped) now has BOTH probables. The
    rebuild shrinks that pending set, so it is self-debouncing -- it cannot re-fire
    for the same game.

GUARDS (keep overhead low + safe for unattended runs):
  * window      -- only act 06:00-16:00 local (when probables post); else no-op.
  * lock        -- single-instance; a rebuild in flight (<10 min old lock) blocks the next tick.
  * daily cap   -- at most DAILY_REBUILD_CAP rebuilds/day (a flapping API can't trigger 50).

FREEZE NOTE: triggers the FROZEN chain (run_local_slate + publish_local) -- it does
NOT touch the model, weights, config, or any stake. New automation, so it ships STAGED
(no scheduled task registered); enable via SETUP_SP_WATCH.bat when ready.

Usage: sp_release_check.py [YYYY-MM-DD] [--dry]
  default date = UTC today (matches publish_local's TODAY). --dry = report only, never rebuild.
"""
import sys, os, json, re, csv, time, datetime, subprocess, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-spwatch/1.0"}
STATE_DIR = os.path.join("data", "state")
LOCK = os.path.join(STATE_DIR, "sp_watch.lock")
LOCK_TTL_S = 600
WINDOW = (6, 16)            # inclusive local-hour window
DAILY_REBUILD_CAP = 6
PY = sys.executable
# Collapse all abbr variants to one canonical form (statsapi side) so diag matchups
# (ARI/OAK/CHW/...) and statsapi (AZ/ATH/CWS/...) compare cleanly.
CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
def cn(x): return CANON.get(str(x).strip(), str(x).strip())


def _get(url, timeout=20, retries=2, sleep=0.5):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(sleep)
    raise last


def _pending(date):
    """{(canon_away, canon_home)} the last build left PENDING because the probable
    was not yet announced (NOT thin-data SPs, which won't resolve via an announcement)."""
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        return set()
    csv.field_size_limit(10 ** 7)
    out = set()
    for r in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
        if "Probable SP not yet announced" in (r.get("why_skipped") or ""):
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", (r.get("matchup") or ""))
            if mm:
                out.add((cn(mm.group(1)), cn(mm.group(2))))
    return out


def _state_path(date):
    return os.path.join(STATE_DIR, "sp_watch_%s.json" % date)


def main():
    raw = sys.argv[1:]
    dry = "--dry" in raw
    pos = [a for a in raw if not a.startswith("--")]
    date = pos[0] if pos else datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    now = datetime.datetime.now()
    if not (WINDOW[0] <= now.hour <= WINDOW[1]):
        print("sp_release_check: %02d:00 outside window %s -> noop" % (now.hour, WINDOW)); return
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL_S:
        print("sp_release_check: fresh lock (rebuild in flight) -> skip"); return

    pending = _pending(date)
    if not pending:
        print("sp_release_check %s: no PENDING-probable games -> noop" % date); return

    j = _get("%s/schedule?sportId=1&date=%s&hydrate=probablePitcher,team" % (API, date))
    resolved = set()
    for d in j.get("dates", []):
        for g in d.get("games", []):
            t = g["teams"]
            both = ((t["away"].get("probablePitcher") or {}).get("id")
                    and (t["home"].get("probablePitcher") or {}).get("id"))
            if both:
                resolved.add((cn(t["away"]["team"].get("abbreviation")),
                              cn(t["home"]["team"].get("abbreviation"))))
    newly = sorted(pending & resolved)
    print("sp_release_check %s: pending=%d, newly-announced=%d %s"
          % (date, len(pending), len(newly), newly))
    if not newly:
        return

    st = {}
    sp = _state_path(date)
    if os.path.exists(sp):
        try: st = json.load(open(sp))
        except Exception: st = {}
    if st.get("rebuilds", 0) >= DAILY_REBUILD_CAP:
        print("sp_release_check: daily rebuild cap reached -> skip"); return

    if dry:
        print("sp_release_check: --dry -> WOULD rebuild %s for %d newly-announced game(s)" % (date, len(newly)))
        return

    os.makedirs(STATE_DIR, exist_ok=True)
    open(LOCK, "w").write("%d %s" % (os.getpid(), now.isoformat()))
    try:
        subprocess.run([PY, "tools/run_local_slate.py", date], check=False, cwd=ROOT)
        subprocess.run([PY, "tools/publish_local.py", "nightly"], check=False, cwd=ROOT)
        st["rebuilds"] = st.get("rebuilds", 0) + 1
        st["last_rebuild_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        json.dump(st, open(sp, "w"))
        print("sp_release_check: rebuilt + published %s (rebuild #%d today)" % (date, st["rebuilds"]))
    finally:
        try: os.remove(LOCK)
        except OSError: pass


if __name__ == "__main__":
    main()
