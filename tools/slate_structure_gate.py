#!/usr/bin/env python3
"""
slate_structure_gate.py
-----------------------
Commit-gate for the daily-slate bake. The diag CSV churns every bake from
non-deterministic Monte-Carlo / live-odds output (win-prob jitters ~1pp on
identical inputs, rippling into edge/kelly/grade), so committing every bake is
pure noise. This gate commits ONLY when the actual slate STRUCTURE changes —
i.e. a starting pitcher or a lineup is newly announced or edited.

Compares the freshly-baked docs/data/picks_*_diag.csv (working tree) to HEAD.
Structure per game = {away_sp_name, home_sp_name, away lineup names (ordered),
home lineup names (ordered)}. Empty/blank values are carried forward from HEAD
(treated as "no new info") so the lineup JSON flapping to "[]" — a known data-
source hiccup — does NOT count as a change.

Exit codes (consumed by the workflow):
    0  -> structure changed (or new diag, or any error): COMMIT
    1  -> structure identical to HEAD: SKIP the commit

FAIL-SAFE: any unexpected error exits 0 (commit) so a bug here can never
silently freeze the slate. Only a confident "unchanged" skips.

Run from the repo root.
"""
import csv
import glob
import io
import json
import os
import subprocess
import sys


def _lineup_names(raw):
    try:
        bats = json.loads(raw or "[]") or []
        return [(b.get("name") or "").strip()
                for b in sorted(bats, key=lambda b: int(b.get("order", 0) or 0))]
    except Exception:
        return []


def _batter_schema(raw):
    """Sorted tuple of the union of keys across a team's batter JSON,
    or None when empty/unparseable (so a "[]" flap never falsely fires)."""
    try:
        bats = json.loads(raw or "[]") or []
        if not bats:
            return None
        keys = set()
        for b in bats:
            keys |= set(b.keys())
        return tuple(sorted(keys))
    except Exception:
        return None


def _games_of(text):
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        m = (r.get("matchup") or "").strip()
        if not m:
            continue
        out[m] = {
            "asp": (r.get("away_sp_name") or "").strip(),
            "hsp": (r.get("home_sp_name") or "").strip(),
            "al":  _lineup_names(r.get("away_top_5_batters_json")),
            "hl":  _lineup_names(r.get("home_top_5_batters_json")),
            "schema": (_batter_schema(r.get("home_top_5_batters_json"))
                       or _batter_schema(r.get("away_top_5_batters_json"))),
        }
    return out


def compare(new_text, old_text):
    """(changed: bool, detail: str). Empty new values carry forward from old."""
    if old_text is None:
        return True, "new diag (no HEAD version)"
    new_g, old_g = _games_of(new_text), _games_of(old_text)
    for m, ng in new_g.items():
        og = old_g.get(m)
        if og is None:
            return True, f"new game on the slate: {m}"
        # SP: carry a blank new value forward from HEAD (rare blank-flap); any
        # real name change (TBD->name, name->name') counts.
        sp_changed = ((ng["asp"] or og["asp"]) != og["asp"]
                      or (ng["hsp"] or og["hsp"]) != og["hsp"])
        # Lineup: fire when the NEW lineup is non-empty and differs from HEAD.
        # This catches a brand-new posting (HEAD empty -> populated) -- the case
        # the old both-non-empty rule missed when the SP did not co-confirm --
        # while still ignoring the batter JSON flap to "[]" (NEW empty -> falsy,
        # no fire). HEAD is only ever empty when a lineup was never published
        # (the gate never commits populated -> empty), so empty -> populated is
        # always a real posting, never a flap.
        lineup_changed = ((ng["al"] and ng["al"] != og["al"])
                          or (ng["hl"] and ng["hl"] != og["hl"]))
        # Schema change: the batter payload gained/lost fields (e.g. a new
        # per-hitter metric). Publish once so the data lands even when
        # SP/lineups are unchanged; self-quiesces once HEAD catches up.
        schema_changed = (ng.get("schema") and og.get("schema")
                          and ng["schema"] != og["schema"])
        if sp_changed or lineup_changed or schema_changed:
            return True, f"{m}: " + ("SP changed" if sp_changed
                                     else "lineup changed" if lineup_changed
                                     else "batter schema changed")
    return False, "all SP/lineups identical to HEAD"


def _head_version(path):
    try:
        return subprocess.check_output(["git", "show", f"HEAD:{path}"],
                                       stderr=subprocess.DEVNULL).decode("utf-8")
    except subprocess.CalledProcessError:
        return None


def decide():
    diags = sorted(glob.glob("docs/data/picks_*_diag.csv"))
    if not diags:
        return True, "no baked diag found"          # commit (safe)
    for path in diags:
        new_text = open(path, encoding="utf-8").read()
        changed, why = compare(new_text, _head_version(path))
        if changed:
            return True, f"{os.path.basename(path)} -> {why}"
    return False, "all baked diags: SP/lineups identical to HEAD"


def main():
    try:
        commit, why = decide()
    except Exception as e:                            # fail-safe: commit on any error
        print(f"[structure_gate] error: {e} -> committing (fail-safe)")
        sys.exit(0)
    if commit:
        print(f"STRUCTURE_CHANGED -> commit | {why}")
        sys.exit(0)
    print(f"STRUCTURE_UNCHANGED -> skip churn commit | {why}")
    sys.exit(1)


if __name__ == "__main__":
    main()
