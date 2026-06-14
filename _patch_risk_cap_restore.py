# -*- coding: utf-8 -*-
"""_patch_risk_cap_restore.py — RESTORE MAX_DAILY_RISK_UNITS 10.0 -> 15.0 (post-travel).
The reverse of the SFO->Japan travel tighten. DRY BY DEFAULT: prints what it would do and
writes nothing unless called with --apply (so an accidental run never changes the live cap).
Idempotent: if already 15.0, it's a no-op. Run from repo root."""
import sys, re

P = "mlb_edge/config.py"
APPLY = "--apply" in sys.argv
OLD = "MAX_DAILY_RISK_UNITS: float = 10.0"
NEW = "MAX_DAILY_RISK_UNITS: float = 15.0"

src = open(P, encoding="utf-8").read()

if NEW in src:
    print("[noop] already at 15.0 — nothing to do")
    sys.exit(0)
if OLD not in src:
    print("[FAIL] could not find %r in %s (was it edited? check the line manually)" % (OLD, P))
    sys.exit(1)
if src.count(OLD) != 1:
    print("[FAIL] expected exactly 1 occurrence, found %d — aborting" % src.count(OLD))
    sys.exit(1)

if not APPLY:
    print("[dry-run] WOULD change:  %s  ->  %s" % (OLD, NEW))
    print("[dry-run] re-run with --apply to write (the PUSH_RISK_CAP_RESTORE.bat does this).")
    sys.exit(0)

open(P, "w", encoding="utf-8").write(src.replace(OLD, NEW))
print("[applied] MAX_DAILY_RISK_UNITS 10.0 -> 15.0")
