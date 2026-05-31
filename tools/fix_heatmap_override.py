#!/usr/bin/env python3
"""
fix_heatmap_override.py
-----------------------
Heatmap colors (gp-c1..c5 / gp-h1..h5) never actually showed inside the game
expander. Root cause (confirmed live via getComputedStyle):

    .details-row td { background: var(--bg); }   /* specificity 0,1,1 */
    .gp-h3        { background: rgba(248,81,73,0.33); }  /* specificity 0,1,0 */

`.details-row td` is more specific, so it overrode every heat class -> cells
rendered the flat dark cell background instead of the blue/red tint. This
affects ALL heatmaps in the expander (bullpen K% matrix, OPS tables, SP rows).

Fix: mark the 10 heat-color backgrounds `!important` so they win over the
container rule (verified live: cells go from rgb(14,17,23) to the rgba tints).
gp-z (transparent / neutral) is left as-is. CSS-only; no JS change.

Idempotent. Run from the repo root.
"""
import sys

IDX = "docs/index.html"

OLD = (
    "  .gp-c5{background:rgba(56,110,230,0.55);} .gp-c4{background:rgba(56,110,230,0.42);}\n"
    "  .gp-c3{background:rgba(56,110,230,0.30);} .gp-c2{background:rgba(56,110,230,0.20);}\n"
    "  .gp-c1{background:rgba(56,110,230,0.11);} .gp-z{background:transparent;}\n"
    "  .gp-h1{background:rgba(248,81,73,0.12);}  .gp-h2{background:rgba(248,81,73,0.22);}\n"
    "  .gp-h3{background:rgba(248,81,73,0.33);}  .gp-h4{background:rgba(248,81,73,0.46);}\n"
    "  .gp-h5{background:rgba(248,81,73,0.58);}"
)
NEW = (
    "  .gp-c5{background:rgba(56,110,230,0.55) !important;} .gp-c4{background:rgba(56,110,230,0.42) !important;}\n"
    "  .gp-c3{background:rgba(56,110,230,0.30) !important;} .gp-c2{background:rgba(56,110,230,0.20) !important;}\n"
    "  .gp-c1{background:rgba(56,110,230,0.11) !important;} .gp-z{background:transparent;}\n"
    "  .gp-h1{background:rgba(248,81,73,0.12) !important;}  .gp-h2{background:rgba(248,81,73,0.22) !important;}\n"
    "  .gp-h3{background:rgba(248,81,73,0.33) !important;}  .gp-h4{background:rgba(248,81,73,0.46) !important;}\n"
    "  .gp-h5{background:rgba(248,81,73,0.58) !important;}"
)
MARK = "rgba(248,81,73,0.58) !important"


def main():
    with open(IDX, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    if MARK in work:
        print("  skip (already applied)")
        return
    if work.count(OLD) != 1:
        print(f"  ERROR anchor count={work.count(OLD)} (need 1)")
        sys.exit(1)
    work = work.replace(OLD, NEW, 1)
    n_imp = NEW.count("!important")
    with open(IDX, "w", encoding="utf-8", newline="") as f:
        f.write(work.replace("\n", nl))
    print(f"  applied: heat classes now !important ({n_imp} rules)")


if __name__ == "__main__":
    main()
