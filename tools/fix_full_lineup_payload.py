#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_full_lineup_payload.py
--------------------------
The batter payload (home/away_top_5_batters_json) only carried the top 5 hitters
because build_team_top_5_payload fetched n=5. Bump to n=9 so the full batting
order is included -> the Statcast Profile and platoon tables render all 9.

Surgical: only the call inside build_team_top_5_payload changes. get_top_n_lineup's
default (n=5) is left alone, so the BvP path (bvp_brain, which passes its own n)
is unaffected. The column name stays *_top_5_* (cosmetic); all consumers loop
over the array, so 9 entries just render 9 rows.

1 idempotent edit to mlb_edge/platoon_splits.py. Run from repo root.
"""
import sys

F = "mlb_edge/platoon_splits.py"
OLD = "    lineup = get_top_n_lineup(game_pk, team_side, n=5)"
NEW = "    lineup = get_top_n_lineup(game_pk, team_side, n=9)  # full batting order (was top-5)"
SENTINEL = "n=9)  # full batting order"


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    if SENTINEL in work:
        print(f"  skip (already applied): {SENTINEL}")
        return
    n = work.count(OLD)
    if n != 1:
        print(f"  ERROR anchor count={n} (need 1)")
        sys.exit(1)
    work = work.replace(OLD, NEW, 1)
    with open(F, "w", encoding="utf-8", newline="") as fh:
        fh.write(work.replace("\n", nl))
    print("  applied: top-5 -> full 9-batter lineup")


if __name__ == "__main__":
    main()
