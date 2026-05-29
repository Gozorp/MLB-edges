#!/usr/bin/env python3
"""Heatmap the bullpen xwOBA-allowed gap in the OU deep-analysis (_deepNarrativeOU).

This is the one place the dashboard renders a bullpen xwOBA value. The diag only
carries a single signed `hl_bullpen_xwoba_gap` (home vs away), so we color it from
the HOME reference, matching the ERA/WHIP/xwOBA "low = good = hot" logic:
  gap < 0  -> home bullpen has the LOWER xwOBA-allowed (better) -> HOT (red)
  gap > 0  -> home bullpen worse                               -> COLD (blue)
The existing "(home better/worse)" label keeps the direction explicit, and the
_dt() tooltip on the value is preserved.

Depends on add_preview_upgrades.py (provides _gpHeatDir + the .gp-stat chip CSS);
aborts if that hasn't been pushed. Idempotent + backup. Caller node --checks.
"""
import sys
from pathlib import Path

P = Path("docs/index.html")

OLD = r'''Bullpen gap (home vs away xwOBA-allowed): ${_dt(_fmtNum(hlBp,4), "hl_bullpen_xwoba_gap CSV")} <span class="muted">(${sign}).</span>'''
NEW = r'''Bullpen gap (home vs away xwOBA-allowed): <span class="gp-stat ${_gpHeatDir(hlBp,-0.04,0,0.04,true)}">${_dt(_fmtNum(hlBp,4), "hl_bullpen_xwoba_gap CSV")}</span> <span class="muted">(${sign}).</span>'''


def main():
    src = P.read_text(encoding="utf-8")
    if "_gpHeatDir" not in src or "gp-stat{" not in src:
        print("ERROR: preview upgrades not found. Run PUSH_PREVIEW_UPGRADES.bat "
              "first (and let it push), then re-run this.")
        return 2
    if "_gpHeatDir(hlBp" in src:
        print("bullpen xwOBA heat already applied; nothing to do.")
        return 0
    n = src.count(OLD)
    if n != 1:
        raise SystemExit("ANCHOR ERROR: bullpen-gap line found %d times (expected 1)" % n)
    P.with_suffix(".html.bak3").write_text(src, encoding="utf-8")
    src = src.replace(OLD, NEW, 1)
    P.write_text(src, encoding="utf-8", newline="\n")
    print("Heatmapped bullpen xwOBA gap in _deepNarrativeOU (backup -> index.html.bak3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
