#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_structure_gate_lineup_posting.py
------------------------------------
The structure gate only counted a lineup as "changed" when BOTH the HEAD and the
freshly-baked version were non-empty. That means the FIRST lineup posting of the
day (HEAD empty -> populated) never trips it -- and because the probable starter
is known days ahead, the lineup posting almost never arrives with a co-occurring
SP change. Result: a slate first baked with empty lineups (the normal morning
bake) never publishes its lineups all day, so BOTH the platoon table and the new
hitter Statcast table stay blank.

Fix: fire when the NEW lineup is non-empty and differs from HEAD. This catches a
real first posting (empty -> populated) while still ignoring the batter-JSON
flap to "[]" (NEW empty -> falsy -> no fire). HEAD can only be empty when a
lineup was never published (the gate never commits populated -> empty), so
empty -> populated is always a real posting, never a flap.

1 idempotent edit to tools/slate_structure_gate.py. Run from repo root.
"""
import sys

F = "tools/slate_structure_gate.py"

OLD = (
    "        # Lineup: a change only when BOTH versions have a real (non-empty) lineup\n"
    "        # AND they differ. This ignores the batter JSON flapping to \"[]\" in\n"
    "        # either direction (a known fetch hiccup); the cost is not firing on a\n"
    "        # brand-new posting that arrives with no SP change (rare -- the SP almost\n"
    "        # always co-confirms, and that commit carries the lineup with it).\n"
    '        lineup_changed = ((ng["al"] and og["al"] and ng["al"] != og["al"])\n'
    '                          or (ng["hl"] and og["hl"] and ng["hl"] != og["hl"]))'
)

NEW = (
    "        # Lineup: fire when the NEW lineup is non-empty and differs from HEAD.\n"
    "        # This catches a brand-new posting (HEAD empty -> populated) -- the case\n"
    "        # the old both-non-empty rule missed when the SP did not co-confirm --\n"
    "        # while still ignoring the batter JSON flap to \"[]\" (NEW empty -> falsy,\n"
    "        # no fire). HEAD is only ever empty when a lineup was never published\n"
    "        # (the gate never commits populated -> empty), so empty -> populated is\n"
    "        # always a real posting, never a flap.\n"
    '        lineup_changed = ((ng["al"] and ng["al"] != og["al"])\n'
    '                          or (ng["hl"] and ng["hl"] != og["hl"]))'
)

SENTINEL = '((ng["al"] and ng["al"] != og["al"])'


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    if SENTINEL in work:
        print(f"  skip (already applied): {SENTINEL[:40]}")
        return
    n = work.count(OLD)
    if n != 1:
        print(f"  ERROR anchor count={n} (need 1)")
        sys.exit(1)
    work = work.replace(OLD, NEW, 1)
    with open(F, "w", encoding="utf-8", newline="") as fh:
        fh.write(work.replace("\n", nl))
    print("  applied: lineup empty->populated now fires")


if __name__ == "__main__":
    main()
