#!/usr/bin/env python3
"""
_patch_series_dh_regex.py
=========================
Fix: matchResult was conflating doubleheader `(G2)` with series-indicator
`(G2 of 3)`, causing slate rows annotated with the series indicator to
resolve to the wrong gamePk.

Concrete failure (2026-05-24):
  Slate row "DET @ BAL (G2 of 3)" (series, NOT a doubleheader)
    -> regex /\\(G([12])(?:\\s+of\\s+\\d+)?\\)/i parsed gameNumber=2
    -> lookup results["DET@BAL@G2"] = gamePk 824840 (Scheduled, 22:05Z)
    -> chip rendered as "PRE-GAME"
  Correct behavior:
    -> regex should NOT fire on "(G2 of 3)"
    -> fall back to bare key results["DET@BAL"] = gamePk 824839 (Final 3-5)
    -> chip rendered as "MISS" / "LOSS"

Two annotations exist in the matchup string pipeline:
  - _dedupDoubleheaders appends "(G2)" / "(G3)" for true doubleheaders.
  - _addSeriesSuffix appends "(G2 of 3)" / "(G3 of 3)" for series games.

Only the bare-suffix form is the doubleheader signal; the "of N" form
is just series numbering and must NOT trigger DH-keyed lookup.

Fix: tighten the regex to /\\(G([12])\\)/ — only match plain (GN).

Also fix the comment to reflect the actual annotation source
(_dedupDoubleheaders, not _addSeriesSuffix).
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
    print("=== _patch_series_dh_regex.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    src = must_replace(
        src,
        '  // Doubleheader disambiguation (2026-05-23): if the matchup string has\n'
        '  // "(G1)" or "(G2)" — appended at render time via _addSeriesSuffix —\n'
        '  // look up the specific game first before falling back to bare key.\n'
        '  let gameNumber = null;\n'
        '  const gMatch = matchup.match(/\\(G([12])(?:\\s+of\\s+\\d+)?\\)/i);\n'
        '  if (gMatch) gameNumber = parseInt(gMatch[1], 10);',
        '  // Doubleheader disambiguation. Two distinct annotations can appear\n'
        '  // in the matchup string:\n'
        '  //   "(G2)"      <- _dedupDoubleheaders, true doubleheader G2/G3\n'
        '  //   "(G2 of 3)" <- _addSeriesSuffix, series-game indicator (NOT DH)\n'
        '  // Only the BARE form is the doubleheader signal. Regex must be\n'
        '  // strict to "(GN)" so a series row like "DET @ BAL (G2 of 3)"\n'
        '  // doesn\'t get misrouted to the doubleheader G2 key.\n'
        '  // (2026-05-24: prior regex matched both forms and routed series-G2\n'
        '  //  rows to a future/wrong gamePk.)\n'
        '  let gameNumber = null;\n'
        '  const gMatch = matchup.match(/\\(G([12])\\)/);\n'
        '  if (gMatch) gameNumber = parseInt(gMatch[1], 10);',
        "tighten matchResult regex to bare (GN) only",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
