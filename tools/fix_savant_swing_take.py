#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_savant_swing_take.py
------------------------
Savant renamed the swing-take leaderboard's player-type parameter: `type=batter`
now returns an empty header-only CSV (134 bytes), while `playerType=batter`
returns the full 43,642-byte leaderboard (verified live on the local box). This
swaps the one param so the swing-take harvest works again -> the F3
swing_take_gap conviction signal gets fresh data instead of stale cache.

1 idempotent edit to mlb_edge/savant_scraper.py. Run from repo root.
"""
import ast
import sys

F = "mlb_edge/savant_scraper.py"
OLD = "min=q&type=batter&csv=true"
NEW = "min=q&playerType=batter&csv=true"
SENTINEL = "min=q&playerType=batter&csv=true"

with open(F, "r", encoding="utf-8") as fh:
    s = fh.read()
if SENTINEL in s:
    print("  skip (already applied)")
    sys.exit(0)
n = s.count(OLD)
if n != 1:
    print("  ERROR anchor count=%d (need 1)" % n)
    sys.exit(1)
s = s.replace(OLD, NEW, 1)
ast.parse(s)  # gate: still valid Python
with open(F, "w", encoding="utf-8") as fh:
    fh.write(s)
print("  applied: swing-take type=batter -> playerType=batter")
