#!/usr/bin/env python3
"""
feature_usability_pass.py
-------------------------
Front-end usability + performance pass for docs/index.html. Measured causes of
the "laggy / choppy / AI-slop" feel (profiled live in the browser):
  - a 92vh full-screen marketing hero pushing the actual slate ~1700px down, so
    every visit starts with a scroll past a landing splash;
  - an INFINITE `heroPulse` background animation on that hero + an infinite
    floating scroll chevron -> perpetual repaints;
  - `.reveal-on-scroll` cards fade/slide in on every scroll -> scroll-coupled
    repaint that reads as jank;
  - Chart.js expanders animate a sluggish 400ms.

Two changes, both contained + reversible:
  1. Append a CSS override block at the end of the single <style> (collapses the
     hero into a compact stat strip so controls/slate are visible on load, kills
     the infinite animations, stops the scroll-fade, lightly densifies headings,
     and honors prefers-reduced-motion). The hero stat COUNTERS (#hero-stat-*)
     stay intact -- only the marketing chrome is hidden, so the JS that animates
     them is untouched. CSS-only => no HTML/JS structure change.
  2. Chart.js expander animation 400ms -> 120ms (snappier open).

(Deliberately NOT touching content-visibility: it can mis-size the lazily
created win-prob chart canvases and can't be integration-tested offline.)

Idempotent. Run from repo root.
"""
import sys

F = "docs/index.html"

CSS_BLOCK = """
/* === usability-pass-2026: data-first compact hero + calmer motion === */
#mlb-hero{min-height:0!important;padding:14px clamp(16px,4vw,32px)!important;flex-direction:row!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;overflow:visible!important;background:var(--bg)!important;}
#mlb-hero::before{display:none!important;animation:none!important;}
#mlb-hero .hero-eyebrow,#mlb-hero .hero-headline,#mlb-hero .hero-tagline,#mlb-hero .hero-cta,#mlb-hero .hero-scroll-indicator{display:none!important;}
#mlb-hero .hero-stats{margin:0!important;gap:clamp(1.2rem,4vw,2.2rem)!important;opacity:1!important;animation:none!important;justify-content:flex-start!important;flex-wrap:wrap!important;}
#mlb-hero .hero-stat{flex-direction:row!important;align-items:baseline!important;gap:.5rem!important;min-width:0!important;}
#mlb-hero .hero-stat-value{font-size:1.3rem!important;}
#mlb-hero .hero-stat-label{margin-top:0!important;font-size:.62rem!important;letter-spacing:.14em!important;}
.reveal-on-scroll{opacity:1!important;transform:none!important;}
main h2{font-size:1.05rem;letter-spacing:.01em;margin:1rem 0 .5rem;}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important;scroll-behavior:auto!important;}}
/* === end usability-pass-2026 === */
"""

SENTINEL = "usability-pass-2026"


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = []

    # --- Edit 1: append CSS override block before the single </style> ---
    if SENTINEL in work:
        print("  skip edit 1 (CSS block already present)")
    else:
        if work.count("</style>") != 1:
            print(f"  ERROR </style> count={work.count('</style>')} (need 1)")
            sys.exit(1)
        work = work.replace("</style>", CSS_BLOCK + "</style>", 1)
        applied.append("css-block")

    # --- Edit 2: chart expander animation 400ms -> 120ms ---
    OLD = "      animation: { duration: 400 },"
    NEW = "      animation: { duration: 120 },"
    if NEW in work and OLD not in work:
        print("  skip edit 2 (chart duration already 120)")
    elif work.count(OLD) == 1:
        work = work.replace(OLD, NEW, 1)
        applied.append("chart-anim")
    else:
        print(f"  ERROR chart anim anchor count={work.count(OLD)} (need 1)")
        sys.exit(1)

    if not applied:
        print("DONE (nothing to do, already applied)")
        return

    with open(F, "w", encoding="utf-8", newline="") as fh:
        fh.write(work.replace("\n", nl))
    print("  applied:", ", ".join(applied))


if __name__ == "__main__":
    main()
