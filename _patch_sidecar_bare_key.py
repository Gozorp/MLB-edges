#!/usr/bin/env python3
"""
_patch_sidecar_bare_key.py
==========================
Fix: O/U pill, Claude pill, and parlay-grade lookups in renderSlate were
silently failing for every game whose matchup string carried a series
suffix `(G2 of 3)` or doubleheader suffix `(G2)`.

Root cause: renderSlate sets
    matchupKey = r.matchup.trim()
which after `_addSeriesSuffix` is e.g. "PIT @ TOR (G3 of 3)". The
sidecar maps are keyed by the BARE `AWAY @ HOME` form ("PIT @ TOR"),
so every lookup returned undefined:
    window.__totalsByMatchup[matchupKey]                -> undefined  -> O/U cell blank
    gradeMap[matchupKey]                                -> undefined  -> parlay grade missing
    window.__claudePicks.by_matchup[matchupKey]         -> undefined  -> Claude pill empty

Concrete failure observed 2026-05-24: 15 rows, totals map had 5 entries
(all bare keys), but ZERO O/U pills rendered because the suffixed
lookup never hit. The `_lookupTotalsForPreview` helper used elsewhere
correctly builds the key from `awayAbbr` + `homeAbbr` (no suffix) which
is why the Lineup Edge card composite still worked — only the slate
table row-level lookups were broken.

Fix: introduce `bareMatchupKey` (strip trailing parenthetical) inside
the renderSlate loop, and try the bare key first with the suffixed key
as a fallback. Keep `matchupKey` (with suffix) for display purposes.

Per locked memory: bash + Python str.replace, no Edit tool.
"""
from __future__ import annotations

import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "docs" / "index.html"


def must_replace(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    out = src.replace(old, new, 1)
    if out == src:
        print(f"[FAIL] {label}: no-op replace")
        sys.exit(2)
    print(f"[ok]   {label}")
    return out


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    n0 = len(src)
    print("=== _patch_sidecar_bare_key.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. Derive bareMatchupKey + fix gradeMap lookup ----------
    src = must_replace(
        src,
        '    const r = rows[i];\n'
        '    const matchupKey = (r.matchup || "").trim();\n'
        '    const fromParlay = (gradeMap && gradeMap[matchupKey]) || {};',
        '    const r = rows[i];\n'
        '    const matchupKey = (r.matchup || "").trim();\n'
        '    // bareMatchupKey strips any "(G2 of 3)" / "(G2)" suffix that\n'
        '    // _addSeriesSuffix or _dedupDoubleheaders appends; sidecar maps\n'
        '    // (gradeMap, claudePicks, totalsByMatchup) are keyed by the bare\n'
        '    // "AWAY @ HOME" form, so the suffixed key never hits. Bare first,\n'
        '    // suffixed fallback in case some sidecar ever uses the full string.\n'
        '    const bareMatchupKey = matchupKey.replace(/\\s*\\([^)]*\\)\\s*$/, "").trim();\n'
        '    const fromParlay = (gradeMap && (gradeMap[bareMatchupKey] || gradeMap[matchupKey])) || {};',
        "1: introduce bareMatchupKey + gradeMap bare-key lookup",
    )

    # ---------- 2. Fix Claude pill lookup ----------
    src = must_replace(
        src,
        '    const cb = (window.__claudePicks && window.__claudePicks.by_matchup)\n'
        '      ? window.__claudePicks.by_matchup[matchupKey] : null;',
        '    const cb = (window.__claudePicks && window.__claudePicks.by_matchup)\n'
        '      ? (window.__claudePicks.by_matchup[bareMatchupKey] || window.__claudePicks.by_matchup[matchupKey]) : null;',
        "2: Claude pill bare-key lookup",
    )

    # ---------- 3. Fix O/U totals lookup ----------
    src = must_replace(
        src,
        '    const tot = (window.__totalsByMatchup) ? window.__totalsByMatchup[matchupKey] : null;',
        '    const tot = (window.__totalsByMatchup) ? (window.__totalsByMatchup[bareMatchupKey] || window.__totalsByMatchup[matchupKey]) : null;',
        "3: O/U totals bare-key lookup",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
