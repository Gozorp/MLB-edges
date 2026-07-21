#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bake_guard.py -- self-healing bake reconciler for the mlb_edge dashboard.

THE FAILURE IT FIXES
--------------------
predict.py writes the day's slate to the repo ROOT (picks_<date>_diag.csv plus
its news_overrides / totals / parlay siblings). A SEPARATE bake step -- and only
that step, inside run_local_slate.py -- copies those into docs/data/ and rebuilds
docs/data/manifest.json. The dashboard loads ONLY dates that are baked into
docs/data/ AND listed in the manifest. publish_local.py cannot rescue a stranded
slate either: its _slate_today() globs docs/data/ (never root), so a slate that
was generated but never baked is invisible to the publisher forever.

That is exactly how 2026-07-21 went missing: predict.py produced a complete,
valid 15-game root slate at 06:15, but run_local_slate's bake never ran, so the
picks file + manifest entry never landed and the site stayed on 2026-07-20.

WHAT THIS DOES  (idempotent, ADDITIVE, non-destructive)
-------------------------------------------------------
It NEVER deletes, NEVER regenerates a prediction, NEVER runs a model, and NEVER
touches the working tree with `git reset`. Safe to run on any schedule.

  1. Find every root picks_<date>_diag.csv that is MISSING from docs/data/ --
     those are stranded slates. With --update-newer it also re-bakes dates whose
     root file is newer than the baked copy (the re-run / late-SP case).
  2. Gate each candidate: the root diag must parse as CSV with >=1 data row AND
     be STABLE (mtime older than --min-age-sec, default 90s) so a file still
     mid-write is never published.
  3. Atomically copy the diag + its siblings (news_overrides, totals, parlay)
     into docs/data/  (.tmp + os.replace -- an interrupted copy can't tear).
  4. Rebuild docs/data/manifest.json from the diag files actually present in
     docs/data/  (identical logic to run_local_slate.py), writing only if the
     date set actually changed.
  5. With --push, and ONLY if something changed, git-add just the affected
     docs/data files (picks + every matching sidecar) + manifest, commit,
     pull --rebase --autostash, and push. No reset --hard, ever.

Shares tools/job_lock.py with run_local_slate + publish_local, so it can never
interleave with their reset/commit/push phases.

USAGE
  python tools/bake_guard.py --check                 # dry-run; exit 1 if stranded
  python tools/bake_guard.py                          # heal docs/data locally (no git)
  python tools/bake_guard.py --push                   # heal + safe commit/push if changed
  python tools/bake_guard.py --push --update-newer    # also re-bake newer re-runs
"""
import argparse
import atexit
import csv
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys

# repo root, regardless of where we're invoked from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
csv.field_size_limit(10 ** 7)

DD = os.path.join("docs", "data")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# same set run_local_slate.py bakes, keyed per date
SIBLINGS = [
    "picks_{d}_diag.csv",
    "picks_{d}_news_overrides.csv",
    "picks_totals_{d}.csv",
    "parlay_{d}.txt",
]
DEFAULT_MIN_AGE = 90  # seconds a root file must be stable before we trust it


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "bake_guard_log.txt"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _date_of(path):
    m = DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def _diag_rows(path):
    """Number of data rows in a diag CSV (0 => treat as invalid/empty)."""
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return sum(1 for _ in csv.DictReader(fh))
    except Exception:
        return -1


def _stable(path, min_age):
    try:
        return (datetime.datetime.now().timestamp() - os.path.getmtime(path)) >= min_age
    except OSError:
        return False


def _atomic_copy(src, dst):
    shutil.copy2(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)  # atomic: an interrupted copy can't leave a torn file


def _root_map():
    out = {}
    for f in glob.glob("picks_*_diag.csv"):
        d = _date_of(f)
        if d:
            out[d] = f
    return out


def _dd_map():
    out = {}
    for f in glob.glob(os.path.join(DD, "picks_*_diag.csv")):
        d = _date_of(f)
        if d:
            out[d] = f
    return out


def find_candidates(update_newer, min_age):
    """Return (to_bake, skipped) where to_bake is a list of (date, reason)
    and skipped is a list of (date, reason). Pure inspection; no writes."""
    root, dd = _root_map(), _dd_map()
    to_bake, skipped = [], []
    for d in sorted(root):
        src = root[d]
        if d not in dd:
            reason = "missing from docs/data"
        elif update_newer and os.path.getmtime(src) > os.path.getmtime(dd[d]) + 2:
            reason = "root newer than baked copy"
        else:
            continue
        rows = _diag_rows(src)
        if rows < 1:
            skipped.append((d, "root diag empty/unreadable (rows=%d)" % rows))
            continue
        if not _stable(src, min_age):
            skipped.append((d, "root diag still mid-write (<%ds old)" % min_age))
            continue
        to_bake.append((d, "%s; %d games" % (reason, rows)))
    return to_bake, skipped


def bake(dates):
    """Copy every present sibling for each date root->docs/data. Returns the
    list of docs/data file paths written."""
    os.makedirs(DD, exist_ok=True)
    written = []
    for d in dates:
        for pat in SIBLINGS:
            src = pat.format(d=d)
            if os.path.exists(src):
                dst = os.path.join(DD, os.path.basename(src))
                _atomic_copy(src, dst)
                written.append(dst)
                log("  baked %s -> %s" % (src, dst))
    return written


def rebuild_manifest():
    """Rewrite manifest.json from docs/data diag files. Returns True if the
    date set changed (only then do we write, to stay idempotent)."""
    dates = sorted({_date_of(f) for f in glob.glob(os.path.join(DD, "picks_*_diag.csv"))
                    if _date_of(f)}, reverse=True)
    mpath = os.path.join(DD, "manifest.json")
    try:
        cur = json.load(open(mpath, encoding="utf-8")).get("dates", [])
    except Exception:
        cur = None
    if cur == dates:
        return False
    tmp = mpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"dates": dates}, fh, indent=2)
    os.replace(tmp, mpath)
    log("  manifest.json -> %d dates (was %s)" % (len(dates), "?" if cur is None else len(cur)))
    return True


def _git(*a, check=False):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    log("$ git %s -> %d%s" % (" ".join(a), r.returncode, ("  " + out[:300].replace("\n", " ")) if out else ""))
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(a), out[:300]))
    return r.returncode


def safe_push(dates, extra_files):
    """Additive publish: stage only the affected slate files + manifest, commit,
    rebase onto origin, push. Never resets the working tree. Retries the push
    once after a fresh rebase if the first push is rejected by a concurrent job."""
    stage = set(extra_files)
    stage.add(os.path.join(DD, "manifest.json"))
    for d in dates:
        stage.update(glob.glob(os.path.join(DD, "*%s*" % d)))  # picks + every sidecar for the date
    stage = sorted(p for p in stage if os.path.exists(p))
    if not stage:
        log("  nothing to stage")
        return True
    _git("add", "--", *stage, check=True)
    if _git("diff", "--cached", "--quiet") == 0:
        log("  staged tree identical to HEAD -- nothing to commit")
        return True
    msg = "bake_guard: publish stranded slate(s) %s [additive, no-regen]" % ", ".join(dates)
    _git("commit", "-m", msg, check=True)
    for attempt in (1, 2):
        _git("pull", "--rebase", "--autostash", "origin", "main")
        if _git("push", "origin", "main") == 0:
            log("  pushed on attempt %d" % attempt)
            return True
        log("  push rejected (attempt %d) -- rebasing and retrying" % attempt)
    log("  PUSH FAILED after retry -- commit is local; next cycle will retry")
    return False


def main():
    ap = argparse.ArgumentParser(description="Self-healing root->docs/data bake reconciler.")
    ap.add_argument("--check", action="store_true", help="dry-run; exit 1 if anything is stranded")
    ap.add_argument("--push", action="store_true", help="safe commit+push if anything changed")
    ap.add_argument("--update-newer", action="store_true", help="also re-bake dates whose root file is newer than the baked copy")
    ap.add_argument("--min-age-sec", type=int, default=DEFAULT_MIN_AGE, help="root file must be this stable (s) before baking")
    args = ap.parse_args()

    to_bake, skipped = find_candidates(args.update_newer, args.min_age_sec)
    for d, why in skipped:
        log("SKIP %s (%s)" % (d, why))

    if args.check:
        if to_bake:
            for d, why in to_bake:
                log("STRANDED %s (%s)" % (d, why))
            log("CHECK: %d slate(s) need baking" % len(to_bake))
            return 1
        log("CHECK: docs/data is in sync with root -- nothing stranded")
        return 0

    if not to_bake:
        log("nothing to bake; docs/data in sync with root")
        # still reconcile the manifest in case it drifted from docs/data
        if rebuild_manifest() and args.push:
            safe_push([], [])
        return 0

    # serialize with run_local_slate + publish_local (shared mutex)
    try:
        import job_lock
        if not job_lock.acquire(wait=30):
            log("job_lock held by another job -- skipping this cycle (it will bake/publish)")
            return 0
        atexit.register(job_lock.release)
    except Exception as e:
        log("job_lock unavailable (%r) -- proceeding without cross-job mutex" % e)

    dates = [d for d, _ in to_bake]
    log("baking %d slate(s): %s" % (len(dates), ", ".join(dates)))
    written = bake(dates)
    rebuild_manifest()

    if args.push:
        ok = safe_push(dates, written)
        log("DONE (push=%s)" % ("ok" if ok else "FAILED"))
        return 0 if ok else 2
    log("DONE (local bake only; run with --push to publish)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
