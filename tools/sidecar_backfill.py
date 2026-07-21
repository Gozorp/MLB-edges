#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sidecar_backfill.py -- ensure the display-only sidecars exist (and are published)
for the newest baked slate. Companion to bake_guard.py.

THE FAILURE IT FIXES
--------------------
predict.py + the bake put the PICKS live, but several DISPLAY sidecars --
spread, platoon (SP handedness/splits), player_vectors (O/U overlay), combo
(correlated-combo chip), feature_coverage (telemetry) -- are generated only by
the LATER steps of run_local_slate.py (steps 3.5-3.6) plus spread_projection.py.
If that run doesn't complete (the same stall that strands a slate), those
sidecars never generate: the dashboard fetches them, the Worker returns 503, and
the features SILENTLY vanish -- no console error, the page just renders without
them. That is exactly what happened to 2026-07-21 (spread/platoon/player_vectors/
combo all missing while the picks were fine), and it's why display features are
"inconsistent" -- present only when run_local_slate happens to finish.

publish_local.py cannot heal it: these sidecars aren't in its candidate file
list at all, and its `git reset --hard origin/main` drops any docs/data file not
already on origin. So a missing display sidecar is never self-repaired today.

WHAT THIS DOES  (idempotent, additive, DISPLAY-ONLY -> freeze-safe)
------------------------------------------------------------------
  1. Pick the newest slate date from docs/data/manifest.json (or --date).
  2. For each expected display sidecar MISSING from docs/data, run its writer
     (subprocess, per-writer timeout, non-fatal); re-check the file appeared.
  3. With --push, if anything new was written, additively git-add just those
     files, commit, pull --rebase --autostash, push. Never reset --hard.

kprops is intentionally NOT in the set: the OddsAPI key has been dead since
2026-05-21, so kprops is expected-absent and would otherwise churn every cycle.
None of these writers touch weights/model -- safe under the model freeze.
Shares tools/job_lock.py so it can't interleave with the other jobs.

USAGE
  python tools/sidecar_backfill.py --check           # report missing; exit 1 if any
  python tools/sidecar_backfill.py                    # generate missing locally
  python tools/sidecar_backfill.py --push             # generate + publish
  python tools/sidecar_backfill.py --date 2026-07-21 --push
"""
import argparse
import atexit
import datetime
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
DD = os.path.join("docs", "data")
PY = sys.executable or "python"
PER_WRITER_TIMEOUT = 150  # seconds; a hung feed can't wedge the guard

# expected display sidecar  ->  (filename template, writer argv template)
# Order = cheapest/most-reliable first. All write directly into docs/data/.
SIDECARS = [
    ("spread",           "spread_{d}.json",           ["tools/spread_projection.py", "{d}"]),
    ("combo",            "combo_{d}.json",            ["tools/correlated_combo.py", "{d}"]),
    ("feature_coverage", "feature_coverage_{d}.json", ["tools/feature_coverage_report.py", "{d}"]),
    ("platoon",          "platoon_{d}.json",          ["tools/platoon_enrichment.py", "{d}"]),
    ("player_vectors",   "player_vectors_{d}.json",   ["tools/player_vectors.py", "{d}"]),
]


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "sidecar_backfill_log.txt"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def newest_slate_date():
    try:
        dates = json.load(open(os.path.join(DD, "manifest.json"), encoding="utf-8")).get("dates", [])
        return dates[0] if dates else None
    except Exception as e:
        log("cannot read manifest (%r)" % e)
        return None


def _path(fname_tmpl, d):
    return os.path.join(DD, fname_tmpl.format(d=d))


def missing_for(d):
    """List of (name, fname, argv) whose docs/data file is absent."""
    out = []
    for name, fname_tmpl, argv_tmpl in SIDECARS:
        p = _path(fname_tmpl, d)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            out.append((name, fname_tmpl, argv_tmpl))
    return out


def run_writer(name, argv_tmpl, d):
    argv = [PY] + [a.format(d=d) for a in argv_tmpl]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=PER_WRITER_TIMEOUT)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[-160:]
        log("  ran %-16s rc=%d  %s" % (name, r.returncode, tail))
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log("  ran %-16s TIMEOUT after %ds (will retry next cycle)" % (name, PER_WRITER_TIMEOUT))
        return False
    except Exception as e:
        log("  ran %-16s ERROR %r" % (name, e))
        return False


def _git(*a, check=False):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    log("$ git %s -> %d%s" % (" ".join(a), r.returncode, ("  " + out[:240].replace("\n", " ")) if out else ""))
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed" % " ".join(a))
    return r.returncode


def safe_push(files, d):
    files = sorted(p for p in set(files) if os.path.exists(p))
    if not files:
        return True
    _git("add", "--", *files, check=True)
    if _git("diff", "--cached", "--quiet") == 0:
        log("  nothing staged (files already tracked+identical)")
        return True
    _git("commit", "-m", "sidecar_backfill: restore missing display sidecars for %s "
         "[additive, display-only]" % d, check=True)
    for attempt in (1, 2):
        _git("pull", "--rebase", "--autostash", "origin", "main")
        if _git("push", "origin", "main") == 0:
            log("  pushed on attempt %d" % attempt)
            return True
        log("  push rejected (attempt %d) -- rebasing, retrying" % attempt)
    log("  PUSH FAILED after retry -- commit is local; next cycle retries")
    return False


def main():
    ap = argparse.ArgumentParser(description="Backfill missing display sidecars for the newest slate.")
    ap.add_argument("--check", action="store_true", help="report missing sidecars; exit 1 if any")
    ap.add_argument("--push", action="store_true", help="commit+push newly generated sidecars")
    ap.add_argument("--date", help="slate date YYYY-MM-DD (default: newest in manifest)")
    args = ap.parse_args()

    d = args.date or newest_slate_date()
    if not d:
        log("no slate date resolved -- nothing to do")
        return 0

    miss = missing_for(d)
    if args.check:
        if miss:
            log("CHECK %s: missing %s" % (d, ", ".join(n for n, _, _ in miss)))
            return 1
        log("CHECK %s: all display sidecars present" % d)
        return 0

    if not miss:
        log("%s: all display sidecars present -- nothing to backfill" % d)
        return 0

    # serialize with run_local_slate / publish_local / bake_guard
    try:
        import job_lock
        if not job_lock.acquire(wait=30):
            log("job_lock held by another job -- skipping (it may be generating these)")
            return 0
        atexit.register(job_lock.release)
    except Exception as e:
        log("job_lock unavailable (%r) -- proceeding without mutex" % e)

    log("%s: backfilling %d missing sidecar(s): %s" % (d, len(miss), ", ".join(n for n, _, _ in miss)))
    written = []
    for name, fname_tmpl, argv_tmpl in miss:
        run_writer(name, argv_tmpl, d)
        p = _path(fname_tmpl, d)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            written.append(p)
        else:
            log("  %s still absent after writer (feed down?) -- will retry next cycle" % name)

    if not written:
        log("no sidecars produced this run")
        return 0

    if args.push:
        ok = safe_push(written, d)
        log("DONE (%d written, push=%s)" % (len(written), "ok" if ok else "FAILED"))
        return 0 if ok else 2
    log("DONE (%d written locally; run with --push to publish)" % len(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
