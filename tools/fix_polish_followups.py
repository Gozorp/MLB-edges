#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_polish_followups.py
-----------------------
Two cosmetic follow-ups spotted on the live page after the polish + Theoretical
chances landed:

  1. The Theoretical-chances card showed the home team as "BAL (G4 of 4)" --
     the doubleheader/series suffix leaked into the abbreviation because the raw
     matchup was split on "@". Strip the trailing "(...)" from both side labels.
  2. The "Ask the Slate" section heading stayed at ~38px: the usability pass's
     `main h2` tightening lost a specificity battle (a nested card rule beat it).
     Add !important so the intended densification actually applies.

3 idempotent edits to docs/index.html. Run from repo root.
"""
import sys

F = "docs/index.html"
EDITS = [
    # (old, new, sentinel)
    (r'${_tp[1]||"HOME"}', r'${(_tp[1]||"HOME").replace(/\s*\(.*$/,"")}',
     r'(_tp[1]||"HOME").replace'),
    (r'${_tp[0]||"AWAY"}', r'${(_tp[0]||"AWAY").replace(/\s*\(.*$/,"")}',
     r'(_tp[0]||"AWAY").replace'),
    ('main h2{font-size:1.05rem;letter-spacing:.01em;margin:1rem 0 .5rem;}',
     'main h2{font-size:1.05rem!important;letter-spacing:.01em;margin:1rem 0 .5rem;}',
     'main h2{font-size:1.05rem!important;'),
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
            print(f"  ERROR anchor count={n} (need 1): {old[:40]}")
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
