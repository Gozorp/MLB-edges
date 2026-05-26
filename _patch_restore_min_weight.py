#!/usr/bin/env python3
"""
_patch_restore_min_weight.py
=============================
HOTFIX. Commit 3 of the legacy-blowout teardown (8283396) removed
MIN_RELATIVE_WEIGHT from the import block in auto_weight_update.py,
but left the reference at line 314 (`floor = MIN_RELATIVE_WEIGHT * base`).
Causes NameError on every awu.run() since the teardown landed.

The error is silently absorbed by predict.bat's "-- continuing"
graceful fallback (it logs "FAILED (name 'MIN_RELATIVE_WEIGHT' is not
defined) -- continuing"), so the daily slate keeps generating picks
fine. But the learning loop has been skipping every iteration since
8283396. weights_state.json hasn't updated since.

Same failure mode as the original 2026-05-23 persistence bug (silent
amnesia), different cause. Caught immediately this time by running
predict.bat — without that manual run we wouldn't have noticed for
days, since the only on-disk signal is the audit log not advancing.

Fix: define MIN_RELATIVE_WEIGHT = 0.25 inline near the other learn-rate
constants (NEW_CEILING_MULT, STRESS_MASK_FACTOR, WARMUP_THRESHOLD).
0.25 is the value from the original recursive_weight_update.py and
is the documented 25% floor in [[selflearn-safeguards]].

Per locked memory: bash + Python str.replace; no Edit tool.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TARGET = REPO / "mlb_edge" / "auto_weight_update.py"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# Anchor: the line right after the existing learn-rate constants.
# WARMUP_THRESHOLD is the last one; insert MIN_RELATIVE_WEIGHT after.
must_replace(
    TARGET,
    'NEW_CEILING_MULT: float = 1.5\n'
    'STRESS_MASK_FACTOR: float = 0.3\n'
    'WARMUP_THRESHOLD: int = 30\n',
    'NEW_CEILING_MULT: float = 1.5\n'
    'STRESS_MASK_FACTOR: float = 0.3\n'
    'WARMUP_THRESHOLD: int = 30\n'
    '\n'
    '# Floor multiplier on the per-feature gradient update. A feature\n'
    '# can shrink to at most MIN_RELATIVE_WEIGHT * baseline; never below.\n'
    '# Inherited from the legacy recursive_weight_update.py (value 0.25);\n'
    '# restored 2026-05-26 after the Commit 3 teardown (8283396) removed\n'
    '# the import but left the reference at floor=MIN_RELATIVE_WEIGHT*base.\n'
    'MIN_RELATIVE_WEIGHT: float = 0.25\n',
    "1/1: restore MIN_RELATIVE_WEIGHT constant near other learn-rate consts",
)


# ---------------------------------------------------------------------------
# Final gate
# ---------------------------------------------------------------------------
src = TARGET.read_text(encoding="utf-8")
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"[FAIL] ast.parse after patch: {e}")
    sys.exit(3)
print("[ok]   ast.parse clean")
print("[done] hotfix applied")
