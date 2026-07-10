#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/publish_guard.py — no-regression publish protection (2026-07-10).
=========================================================================
Born from the 7/10 incident: the cloud bake lost the ability to resolve
probable starters (statsapi team-code renames) and silently OVERWROTE a
fully-scored published slate with a 7-games-TBD one. This guard makes that
class of failure impossible to publish quietly:

For every docs/data/picks_*_diag.csv that differs from origin/main, compare
scored-game counts. If the new file is a MASS DEGRADATION of what's already
public, restore the published version (git checkout origin/main -- <file>)
and log loudly — the better board stays up. Also protects manifest.json's
newest date from disappearing.

Rules (per same-dated diag):
  BLOCK if new has 0 data rows while published has >= 1
  BLOCK if scored games (home_sp_name present) drop by >= THRESH (default 3)
        (a single legit SP scratch drops 1; the failure mode drops 6-7)
  BLOCK if total games shrink by > 2
  manifest.json: BLOCK if the published newest date vanishes from the new list

Behavior:
  - BLOCK = restore the origin version of that file; other files publish fine.
  - PUBLISH_ALLOW_REGRESSION=1 env = warn but do not restore (manual override).
  - Guard code errors NEVER abort a publish (fail-open with a warning) —
    availability first; detection is enforced only on clean comparisons.
  - Exit code 0 always (advisory-restorer); --strict exits 2 if anything blocked.

Usage:
  python tools/publish_guard.py             # auto-detect changed diags + manifest
  python tools/publish_guard.py --selftest  # synthetic unit checks, no git
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
csv.field_size_limit(10 ** 7)

THRESH = int(os.environ.get("PUBLISH_GUARD_THRESHOLD", "3"))
ALLOW = os.environ.get("PUBLISH_ALLOW_REGRESSION", "") == "1"


def _git(*a):
    return subprocess.run(["git"] + list(a), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def diag_metrics(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    scored = sum(1 for r in rows if (r.get("home_sp_name") or "").strip())
    pending = sum(1 for r in rows if "PENDING" in (r.get("tier") or ""))
    return {"rows": len(rows), "scored": scored, "pending": pending}


def check_diag(path):
    """Returns (blocked: bool, reason: str)."""
    show = _git("show", "origin/main:%s" % path.replace(os.sep, "/"))
    if show.returncode != 0:
        return False, "new file (not on origin) — allowed"
    old = diag_metrics(show.stdout)
    with open(path, encoding="utf-8", errors="replace") as fh:
        new = diag_metrics(fh.read())
    if new["rows"] == 0 and old["rows"] >= 1:
        return True, "new file has 0 games vs %d published" % old["rows"]
    if old["scored"] - new["scored"] >= THRESH:
        return True, ("scored games would drop %d -> %d (>= %d = mass regression; "
                      "a legit scratch drops 1)" % (old["scored"], new["scored"], THRESH))
    if old["rows"] - new["rows"] > 2:
        return True, "game count would shrink %d -> %d" % (old["rows"], new["rows"])
    return False, ("ok (rows %d->%d, scored %d->%d, pending %d->%d)"
                   % (old["rows"], new["rows"], old["scored"], new["scored"],
                      old["pending"], new["pending"]))


def check_manifest(path="docs/data/manifest.json"):
    show = _git("show", "origin/main:%s" % path)
    if show.returncode != 0 or not os.path.exists(path):
        return False, "n/a"
    try:
        old = json.loads(show.stdout).get("dates") or []
        new = json.load(open(path, encoding="utf-8")).get("dates") or []
    except Exception as e:
        return False, "unparseable (%r) — allowed" % (e,)
    if old and old[0] not in new:
        return True, "published newest date %s missing from new manifest" % old[0]
    return False, "ok (newest %s preserved, %d -> %d dates)" % (
        old[0] if old else "-", len(old), len(new))


def restore(path):
    r = _git("checkout", "origin/main", "--", path)
    return r.returncode == 0


def main():
    if "--selftest" in sys.argv:
        good = "matchup,home_sp_name,tier\nA @ B,Ace,GOLD\nC @ D,Deuce,SKIP\nE @ F,Trey,GOLD\nG @ H,Quad,SKIP\n"
        bad = "matchup,home_sp_name,tier\nA @ B,,PENDING_SP_DATA\nC @ D,,PENDING_SP_DATA\nE @ F,,PENDING_SP_DATA\nG @ H,Quad,SKIP\n"
        g, b = diag_metrics(good), diag_metrics(bad)
        assert g["scored"] == 4 and b["scored"] == 1 and b["pending"] == 3
        assert g["scored"] - b["scored"] >= THRESH, "threshold should catch the mass drop"
        assert g["scored"] - (g["scored"] - 1) < THRESH, "single scratch should pass"
        print("selftest OK: mass regression caught, single scratch allowed (threshold=%d)" % THRESH)
        return 0

    strict = "--strict" in sys.argv
    blocked_any = False

    diff = _git("diff", "--name-only", "origin/main")
    changed = [l.strip() for l in (diff.stdout or "").splitlines() if l.strip()]
    diags = [f for f in changed
             if f.startswith("docs/data/picks_") and f.endswith("_diag.csv")
             and os.path.exists(f)]

    for path in diags:
        try:
            blocked, reason = check_diag(path)
        except Exception as e:
            print("[publish_guard] WARN: check failed for %s (%r) — allowing (fail-open)"
                  % (path, e))
            continue
        if blocked and ALLOW:
            print("[publish_guard] OVERRIDE (PUBLISH_ALLOW_REGRESSION=1): %s — %s"
                  % (path, reason))
        elif blocked:
            ok = restore(path)
            blocked_any = True
            print("[publish_guard] BLOCKED regression in %s — %s -> %s"
                  % (path, reason, "restored published version" if ok else
                     "RESTORE FAILED (manual attention needed)"))
            print("::warning::publish_guard blocked a slate regression in %s (%s)"
                  % (path, reason))
        else:
            print("[publish_guard] %s: %s" % (path, reason))

    if "docs/data/manifest.json" in changed:
        try:
            blocked, reason = check_manifest()
            if blocked and ALLOW:
                print("[publish_guard] OVERRIDE manifest: %s" % reason)
            elif blocked:
                ok = restore("docs/data/manifest.json")
                blocked_any = True
                print("[publish_guard] BLOCKED manifest regression — %s -> %s"
                      % (reason, "restored" if ok else "RESTORE FAILED"))
                print("::warning::publish_guard blocked a manifest regression (%s)" % reason)
            else:
                print("[publish_guard] manifest: %s" % reason)
        except Exception as e:
            print("[publish_guard] WARN: manifest check failed (%r) — allowing" % (e,))

    if not diags and "docs/data/manifest.json" not in changed:
        print("[publish_guard] nothing slate-shaped changed vs origin — clean")
    return 2 if (strict and blocked_any) else 0


if __name__ == "__main__":
    sys.exit(main())
