#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_hr_props_note.py
--------------------
The Home Run Props sub-section (inside Top Probable Outcomes) shows a fallback
note when no batter carries baked season HR/PA -- which in practice means the
slate's lineups simply aren't posted yet. The old wording ("...baked into the
diag (run a slate refresh)") wrongly implied a bake/deploy problem, when the
real condition is just "lineups not posted." Reword it to match reality so it
stops reading like the feature is missing.

Display-only: one text string in docs/index.html. No data/model/logic change.
1 idempotent edit. Run from repo root.
"""
import sys

F = "docs/index.html"
OLD = "Home Run Props populate once per-batter season HR/PA is baked into the diag (run a slate refresh)."
NEW = "Home Run Props populate once starting lineups are posted (~2h before first pitch)."
SENTINEL = "starting lineups are posted (~2h before first pitch)"


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
    print("  applied: HR-props note reworded (bake -> lineups-posted)")


if __name__ == "__main__":
    main()
