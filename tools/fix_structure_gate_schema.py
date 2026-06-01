#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_structure_gate_schema.py
----------------------------
The slate structure-gate only commits when SP/lineup NAMES change, so when a
code change adds fields to the batter payload (e.g. the per-hitter Statcast
columns) but names/SPs are unchanged, the gate skips and the new data never
publishes. Teach the gate to ALSO publish when the batter-payload SCHEMA (the
set of keys per hitter) changes vs HEAD. It self-quiesces: once HEAD carries
the new schema, NEW==OLD and behaviour returns to name-only.

3 idempotent edits to tools/slate_structure_gate.py. Run from repo root.
"""
import sys

F = "tools/slate_structure_gate.py"
EDITS = [
    # 1. schema helper before _games_of
    ("def _games_of(text):",
     'def _batter_schema(raw):\n'
     '    """Sorted tuple of the union of keys across a team\'s batter JSON,\n'
     '    or None when empty/unparseable (so a "[]" flap never falsely fires)."""\n'
     '    try:\n'
     '        bats = json.loads(raw or "[]") or []\n'
     '        if not bats:\n'
     '            return None\n'
     '        keys = set()\n'
     '        for b in bats:\n'
     '            keys |= set(b.keys())\n'
     '        return tuple(sorted(keys))\n'
     '    except Exception:\n'
     '        return None\n'
     '\n'
     '\n'
     'def _games_of(text):',
     "def _batter_schema(raw):"),

    # 2. capture schema per game
    ('            "hl":  _lineup_names(r.get("home_top_5_batters_json")),\n'
     '        }',
     '            "hl":  _lineup_names(r.get("home_top_5_batters_json")),\n'
     '            "schema": (_batter_schema(r.get("home_top_5_batters_json"))\n'
     '                       or _batter_schema(r.get("away_top_5_batters_json"))),\n'
     '        }',
     '"schema": (_batter_schema'),

    # 3. add schema-change to the per-game decision
    ('        if sp_changed or lineup_changed:\n'
     '            return True, f"{m}: " + ("SP changed" if sp_changed else "lineup changed")',
     '        # Schema change: the batter payload gained/lost fields (e.g. a new\n'
     '        # per-hitter metric). Publish once so the data lands even when\n'
     '        # SP/lineups are unchanged; self-quiesces once HEAD catches up.\n'
     '        schema_changed = (ng.get("schema") and og.get("schema")\n'
     '                          and ng["schema"] != og["schema"])\n'
     '        if sp_changed or lineup_changed or schema_changed:\n'
     '            return True, f"{m}: " + ("SP changed" if sp_changed\n'
     '                                     else "lineup changed" if lineup_changed\n'
     '                                     else "batter schema changed")',
     'schema_changed = (ng.get("schema")'),
]


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = skipped = 0
    for old, new, sentinel in EDITS:
        if sentinel in work:
            print(f"  skip (already applied): {sentinel[:40]}")
            skipped += 1
            continue
        n = work.count(old)
        if n != 1:
            print(f"  ERROR anchor count={n} (need 1): {sentinel[:40]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  applied: {sentinel[:40]}")
    if applied:
        with open(F, "w", encoding="utf-8", newline="") as fh:
            fh.write(work.replace("\n", nl))
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
