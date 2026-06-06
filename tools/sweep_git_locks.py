#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sweep_git_locks.py -- remove STALE git lock files so a crashed git op can't
silently freeze the local publish pipeline (a left-behind .git/index.lock makes
every later reset/commit fail, and the public dashboard goes stale).

AGE-GATED (>= MAX_AGE seconds): real git operations finish in well under a
minute, so a lock older than 5 minutes is a crash remnant, never a live op.
This guarantees the sweep can NEVER clobber a lock held by a concurrent,
legitimate git process. Safe to run anytime; designed to run at the very top of
jobs/job_nightly_chain.bat (before any git-touching step) so each scheduled run
self-recovers from a prior crash.

Exit 0 always (never blocks the chain).
"""
import os
import sys
import time
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT = os.path.join(ROOT, ".git")
MAX_AGE = 300  # seconds

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    if not os.path.isdir(GIT):
        print("sweep: no .git dir at %s" % ROOT)
        return 0
    now = time.time()
    cands = set()
    # known hot spots + a shallow scan of the usual lock locations
    for rel in ("index.lock", "shallow.lock", "config.lock", "HEAD.lock",
                "packed-refs.lock", os.path.join("objects", "maintenance.lock")):
        cands.add(os.path.join(GIT, rel))
    for pat in ("*.lock", os.path.join("objects", "*.lock"),
                os.path.join("refs", "heads", "*.lock"),
                os.path.join("refs", "remotes", "origin", "*.lock"),
                os.path.join("logs", "*.lock")):
        cands.update(glob.glob(os.path.join(GIT, pat)))
    removed, kept = [], []
    for p in sorted(cands):
        try:
            if not os.path.exists(p):
                continue
            age = now - os.path.getmtime(p)
            if age >= MAX_AGE:
                os.remove(p)
                removed.append((os.path.relpath(p, ROOT).replace("\\", "/"), int(age)))
            else:
                kept.append((os.path.relpath(p, ROOT).replace("\\", "/"), int(age)))
        except OSError as e:
            print("sweep: could not remove %s: %s" % (p, e))
    for rp, age in removed:
        print("sweep: REMOVED stale git lock %s (%ds old)" % (rp, age))
    for rp, age in kept:
        print("sweep: kept FRESH lock %s (%ds old < %ds) -- live op, not touched" % (rp, age, MAX_AGE))
    if not removed and not kept:
        print("sweep: no git locks present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
