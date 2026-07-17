#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_lock.py -- single cross-job mutex so the scheduled jobs (3-hourly slate
bake, nightly chain, self-learn/brain/postgame publishes) never run their
write phases concurrently. The 00:00 collision between mlb_edge_slate and
mlb_edge_refit produced torn picks_*_diag.csv files (the .corrupt* remnants
at the repo root) because two predict.py runs rewrote the same CSV at once.

Mirrors the age-gated pattern in sweep_git_locks.py: a lock older than
STALE_AGE is a crash remnant and is taken over, so a killed job can never
wedge the pipeline. os.O_CREAT|os.O_EXCL makes creation atomic on Windows.

Usage:
    import job_lock
    if not job_lock.acquire():      # waited, still busy -> caller exits 0
        sys.exit(0)
    try:
        ...
    finally:
        job_lock.release()
"""
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = os.path.join(ROOT, "logs", "mlb_edge_job.lock")
STALE_AGE = 60 * 60   # a slate bake finishes well under an hour; older = crash remnant
WAIT_SECS = 10 * 60   # how long to wait for the other job before giving up
POLL_SECS = 15


def acquire(wait=WAIT_SECS, stale=STALE_AGE):
    """Create the lockfile, waiting up to `wait` seconds for a holder to
    finish. Steals a lock older than `stale` seconds. Returns True on
    success, False if the lock is still held at the deadline (callers log
    and exit 0 -- skipping a cycle is always safer than racing)."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as fh:
                fh.write("pid=%d time=%s\n"
                         % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK_PATH)
                if age >= stale:
                    print("job_lock: taking over stale lock (%.0fs old)" % age)
                    os.remove(LOCK_PATH)
                    continue
            except OSError:
                continue   # lock vanished between checks -> retry immediately
            if time.time() >= deadline:
                return False
            time.sleep(POLL_SECS)


def release():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass
