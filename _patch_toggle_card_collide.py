#!/usr/bin/env python3
"""
_patch_toggle_card_collide.py
=============================
Fix: clicking the Simple/Advanced mode toggle (inside the slate's <h2>)
triggers the togglable-cards delegation handler because the toggle's
ancestor matches "h2 child of .card". The handler then collapses the
whole slate card (display:none on all subsequent siblings), so the user
sees the slate disappear instead of the Advanced columns appearing.

Fix: bail out of togglable-cards when the click target is an interactive
element (button / a / input / select / textarea / label). Card-collapse
on h2 click is still preserved for actual header clicks.
"""
from __future__ import annotations
import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "docs" / "index.html"


def must_replace(src, old, new, label=""):
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main():
    src = INDEX.read_text(encoding="utf-8")
    n0 = len(src)
    print(f"input: {INDEX} ({n0} bytes)")

    old = (
        '  const t = e.target.closest("h2, h3");\n'
        '  if (!t) return;\n'
        '  const parent = t.parentElement;\n'
        '  if (!parent || !parent.classList.contains("card")) return;\n'
    )
    new = (
        '  const t = e.target.closest("h2, h3");\n'
        '  if (!t) return;\n'
        '  // Bail when the click target is an interactive control inside\n'
        '  // the header — e.g. the Simple/Advanced mode toggle in the slate\n'
        '  // header, the help glossary button, etc. Otherwise clicking them\n'
        '  // collapses the whole card (2026-05-25 user report).\n'
        '  if (e.target.closest("button, a, input, select, textarea, label")) {\n'
        '    return;\n'
        '  }\n'
        '  const parent = t.parentElement;\n'
        '  if (!parent || !parent.classList.contains("card")) return;\n'
    )
    src = must_replace(src, old, new, "togglable-cards bail on interactive")
    INDEX.write_text(src, encoding="utf-8")
    print(f"output: {INDEX} ({len(src)} bytes, delta {len(src)-n0:+d})")


if __name__ == "__main__":
    main()
