#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_local.py -- publish TODAY's local picks to origin/main (the public cloud
dashboard), cleanly, regardless of how drifted the working clone is.

Clean-room publish:
  1. gate -- today's diag must parse + have >=1 game (never publish garbage)
  2. save the publishable output files to a temp dir
  3. fetch + `git reset --hard origin/main`  (drops line-ending drift + any stuck
     commit + catches up behind-N; UNTRACKED files like our tools/bats are kept)
  4. restore today's outputs over the clean tree
  5. `git add` ONLY those files, commit, push

Runs on the user's authed Windows git. Arg: short label for the commit.
"""
import sys, os, glob, csv, json, shutil, subprocess, datetime, tempfile
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
csv.field_size_limit(10 ** 7)
label = sys.argv[1] if len(sys.argv) > 1 else "local"
TODAY = datetime.datetime.utcnow().date().isoformat()
YEST = (datetime.datetime.utcnow().date() - datetime.timedelta(days=1)).isoformat()


class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for st in self.streams:
            try: st.write(s); st.flush()
            except Exception: pass
    def flush(self):
        for st in self.streams:
            try: st.flush()
            except Exception: pass
try:
    _logf = open("publish_local_log.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, _logf); sys.stderr = _Tee(sys.__stderr__, _logf)
except Exception as _e:
    print("log-tee setup failed: %r" % (_e,))
print("=== publish_local run %s  TODAY=%s ===" % (datetime.datetime.now().isoformat(timespec="seconds"), TODAY))

def git(*a):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    print("$ git " + " ".join(a) + " -> " + str(r.returncode))
    o = ((r.stdout or "") + (r.stderr or "")).strip()
    if o:
        print("  " + o[:400].replace("\n", "\n  "))
    return r.returncode

candidates = [
    "docs/data/picks_%s_diag.csv" % TODAY,
    "docs/data/picks_totals_%s.csv" % TODAY,
    "docs/data/picks_%s_news_overrides.csv" % TODAY,
    "docs/data/parlay_%s.txt" % TODAY,
    "docs/data/manifest.json",
    "docs/data/claude_picks/%s.json" % TODAY,
    "docs/data/postgame/%s.json" % YEST,
    "data/savant_hitters_2026.csv",
    "data/state/weights_state.json",
    "models/calibration_v1.json",
    "docs/data/daily_variance_%s.md" % TODAY,
    "docs/data/daily_variance_%s.json" % TODAY,
    "docs/data/weekly_baseline.json",
    "docs/data/weekly_baseline_%s.md" % YEST,
    "docs/data/bullpen_meta_%s.json" % TODAY,
    "docs/data/bullpen_meta_%s.json" % YEST,
    "docs/data/series_meta_%s.json" % TODAY,
    "docs/data/series_meta_%s.json" % YEST,
    "docs/data/streaks_%s.json" % TODAY,
    "docs/data/streaks_%s.json" % YEST,
    "docs/data/sp_hr_recent_%s.json" % TODAY,
    "docs/data/sp_hr_recent_%s.json" % YEST,
    "docs/data/weather_runs_%s.json" % TODAY,
    "docs/data/weather_runs_%s.json" % YEST,
    "docs/data/oos_ledger.jsonl",
    "docs/data/skip_shadow_ledger.jsonl",
    "docs/data/team_tiers.json",
    "docs/data/spread_%s.json" % TODAY,
    "docs/data/spread_%s.json" % YEST,
    "docs/data/sp_projection_%s.json" % TODAY,
    "docs/data/sp_projection_%s.json" % YEST,
    "docs/data/provisional_lean_%s.json" % TODAY,
    "docs/data/provisional_lean_%s.json" % YEST,
]
present = [f for f in candidates if os.path.exists(f)]

diag = "docs/data/picks_%s_diag.csv" % TODAY
if not os.path.exists(diag):
    print("publish ABORT: no diag for %s (nothing to publish)" % TODAY); raise SystemExit(0)
try:
    with open(diag, encoding="utf-8", newline="") as _fh:
        _rows = list(csv.reader(_fh))
    n = len(_rows) - 1
    assert n >= 1, "no data rows"
    _hdr = len(_rows[0])
    _torn = [i for i, _r in enumerate(_rows[1:], 2) if len(_r) != _hdr]
    assert not _torn, "torn/short row(s) at line %s (cols != %d) -- refusing to publish a truncated diag" % (_torn[:5], _hdr)
    print("gate: %s OK (%d games, %d cols, all rows intact)" % (diag, n, _hdr))
except Exception as e:
    print("publish ABORT: %s invalid (%r)" % (diag, e)); raise SystemExit(1)

def _candidate_ok(f):
    """Fail-closed tear check before promoting a candidate: CSVs rectangular
    (row width == header width), JSON parses, JSONL parses per line. Torn
    sidecars are dropped with a warning; the publish keeps moving. (The diag
    is gate-checked above and aborts the whole publish instead.)"""
    try:
        if f.endswith(".csv"):
            with open(f, encoding="utf-8", newline="") as fh:
                rws = list(csv.reader(fh))
            if not rws:
                print("candidate DROPPED (empty): %s" % f); return False
            w = len(rws[0])
            bad = [i for i, r in enumerate(rws[1:], 2) if len(r) != w]
            if bad:
                print("candidate DROPPED (torn row(s) %s): %s" % (bad[:5], f)); return False
        elif f.endswith(".json"):
            json.load(open(f, encoding="utf-8"))
        elif f.endswith(".jsonl"):
            for _ln in open(f, encoding="utf-8"):
                _ln = _ln.strip()
                if _ln:
                    json.loads(_ln)
    except Exception as e:
        print("candidate DROPPED (invalid %r): %s" % (e, f)); return False
    return True


present = [f for f in present if _candidate_ok(f)]
print("publish set after tear-check: %d file(s)" % len(present))

tmp = tempfile.mkdtemp(prefix="mlbpub_")
for f in present:
    d = os.path.join(tmp, f)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(f, d)
print("saved %d output file(s) to publish" % len(present))

# auto-heal stale git locks: a crashed git process leaves .git/index.lock, which
# then blocks EVERY future reset/commit (this exact failure stalled the cutover).
# A live git op holds index.lock for milliseconds; anything >120s old is stale.
# Critical for unattended jobs (brain/postgame/self-learn) during the Japan trip.
import time as _t
_now = _t.time()
for _lk in glob.glob(".git/*.lock") + glob.glob(".git/objects/*.lock"):
    try:
        _age = _now - os.path.getmtime(_lk)
        if _age > 120:
            os.remove(_lk); print("removed stale git lock (%.0fs old): %s" % (_age, _lk))
        else:
            print("WARNING: fresh lock left in place (%.0fs old): %s" % (_age, _lk))
    except Exception as _e:
        print("lock check failed for %s: %r" % (_lk, _e))

subprocess.run(["git","rebase","--abort"],capture_output=True)
subprocess.run(["git","merge","--abort"],capture_output=True)
git("fetch", "origin", "main")
if git("reset", "--hard", "origin/main") != 0:
    shutil.rmtree(tmp, ignore_errors=True)
    print("publish ABORT: could not sync to origin"); raise SystemExit(1)

for f in present:
    os.makedirs(os.path.dirname(f), exist_ok=True)
    shutil.copy2(os.path.join(tmp, f), f + ".tmp")
    os.replace(f + ".tmp", f)  # atomic restore: a crash here can never leave a torn tracked file
shutil.rmtree(tmp, ignore_errors=True)

git("add", "--", *present)
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
    print("publish: nothing changed vs origin -- already up to date"); raise SystemExit(0)
git("commit", "-m", "local-publish: %s %s" % (label, datetime.datetime.now().isoformat(timespec="minutes")))
rc = git("push", "origin", "main")
if rc != 0:
    # A push can return a "cannot lock ref ... is at <ourcommit>" retry-race error
    # AFTER it actually landed. Re-check origin/main before declaring failure -- this is
    # the phantom "PUBLISH FAILED" that scared us on the 2026-06-04 slate push.
    _lh = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True)
    _oh = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True).stdout.strip()
    if _lh and _lh == _oh:
        print("note: push returned an error but origin/main already == our commit %s "
              "(retry-race false negative) -- treating as success" % _lh[:9])
        rc = 0
print("PUBLISH OK -> origin/main (public dashboard now shows your local picks)" if rc == 0 else "PUBLISH FAILED -- see above")
