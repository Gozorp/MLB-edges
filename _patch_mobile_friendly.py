#!/usr/bin/env python3
"""
_patch_mobile_friendly.py
=========================
Mobile-friendly responsive layer for the dashboard. Two breakpoints:
  - @media (max-width: 720px)  → tablet & large phone (iPhone Plus, etc.)
  - @media (max-width: 480px)  → small phone (iPhone Mini, SE)

What changes on mobile:
  - Hero: shrinks to ~70vh, stats stack vertically, headline font scales,
    scroll indicator gets out of the way.
  - Header: smaller title/padding, help button stays tappable (44px).
  - Cards: less padding (1.5rem → 1rem), tighter margins.
  - Intro card: 2-col grid → single column.
  - Date picker row: arrows + button wrap properly, buttons larger.
  - Slate table: overflow-x: auto with sticky first column hint;
    smaller cell padding + font; default-Simple-on-first-mobile-visit
    handled at JS level (existing localStorage logic, just sets simple
    on first visit so mobile users start with the dense table hidden).
  - Lineup-edge / Bullpen-edge / Bullpen-outlook 2-col grids stack.
  - Accordion headers get larger tap area.
  - Mode toggle buttons + accordion headers ≥ 44px tappable.
  - Disable hover transforms on touch (no sticky :hover states).
  - Help slide-over panel: already mobile-sized via min(440px, 92vw).

Per locked memory: bash + Python str.replace, CRLF on .bat.
"""
from __future__ import annotations
import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "docs" / "index.html"


def must_replace(src: str, old: str, new: str, label: str = "") -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    n0 = len(src)
    print(f"input: {INDEX} ({n0} bytes)")

    css = (
        '  /* ===== Mobile-friendly responsive layer (2026-05-25) ===== */\n'
        '  /* Touch hint: scroll table horizontally to see all columns. */\n'
        '  .table-scroll-hint {\n'
        '    display: none; font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    color: var(--muted); font-size: 0.74rem; margin-top: 0.5rem;\n'
        '    text-align: right; letter-spacing: 0.08em;\n'
        '  }\n'
        '\n'
        '  @media (max-width: 720px) {\n'
        '    /* Layout: tighter padding throughout. */\n'
        '    header { padding: 1rem 1rem; gap: 0.6rem; }\n'
        '    header h1 { font-size: 1.15rem; }\n'
        '    header .meta { font-size: 0.78rem; }\n'
        '    main { padding: 1rem 1rem 2rem 1rem; }\n'
        '    .card { padding: 1.1rem; margin-bottom: 1rem; border-radius: 8px; }\n'
        '\n'
        '    /* Hero: shorter + stacked stats. */\n'
        '    #mlb-hero { min-height: 78vh; padding: 2.5rem 1rem 4rem 1rem; }\n'
        '    .hero-eyebrow { font-size: 0.68rem; letter-spacing: 0.22em; }\n'
        '    .hero-headline { max-width: 100%; }\n'
        '    .hero-tagline { font-size: 0.95rem; padding: 0 0.5rem; }\n'
        '    .hero-stats { gap: 1.2rem; margin: 0.5rem 0 1.5rem 0; }\n'
        '    .hero-stat-value { font-size: 2.4rem; }\n'
        '    .hero-stat-label { font-size: 0.62rem; }\n'
        '    .hero-cta { padding: 0.9rem 1.4rem; font-size: 0.92rem; }\n'
        '    .hero-scroll-indicator { bottom: 1rem; font-size: 0.68rem; }\n'
        '    .hero-scroll-indicator .arrow { width: 1.1rem; height: 1.1rem; }\n'
        '\n'
        '    /* Intro card: single column. */\n'
        '    #mlb-intro-card { padding: 1rem 1.1rem; }\n'
        '    #mlb-intro-card h2 { font-size: 1rem; padding-right: 1.5rem; }\n'
        '    #mlb-intro-card .intro-grid { grid-template-columns: 1fr; gap: 0.7rem; }\n'
        '    #mlb-intro-card .intro-step { font-size: 0.86rem; }\n'
        '\n'
        '    /* Ask the Slate accordion. */\n'
        '    #ask-the-slate-section { margin: 1.5rem 0 1rem 0; }\n'
        '    #ask-the-slate-section > h2 { font-size: 1.5rem; }\n'
        '    .accordion-header {\n'
        '      padding: 1.1rem 1rem; font-size: 1rem;\n'
        '      min-height: 44px; /* Apple-recommended tap target */\n'
        '    }\n'
        '    .accordion-body-inner { padding: 0 1rem 1.1rem 1rem; }\n'
        '\n'
        '    /* Date picker row: wrap cleanly + larger buttons. */\n'
        '    .picker { gap: 0.4rem; flex-wrap: wrap; }\n'
        '    .picker input, .picker button {\n'
        '      min-height: 40px; padding: 0.5rem 0.8rem;\n'
        '      font-size: 0.88rem;\n'
        '    }\n'
        '    .picker-arrow { width: 40px; }\n'
        '\n'
        '    /* Mode toggle: bigger tap targets. */\n'
        '    .mode-toggle button { padding: 0.5rem 0.8rem; font-size: 0.72rem; }\n'
        '\n'
        '    /* Help button: keep ≥ 44px tap area. */\n'
        '    #help-btn { width: 44px; height: 44px; font-size: 1.05rem; }\n'
        '\n'
        '    /* Visit pill: smaller. */\n'
        '    #visitPill { font-size: 0.74rem; }\n'
        '\n'
        '    /* Slate table: horizontally scrollable + tighter cells. */\n'
        '    .card table {\n'
        '      display: block; overflow-x: auto; -webkit-overflow-scrolling: touch;\n'
        '      font-size: 0.78rem;\n'
        '      /* Avoid sub-pixel scrollbar flash. */\n'
        '      scrollbar-width: thin;\n'
        '    }\n'
        '    .card th, .card td { padding: 0.4rem 0.35rem; }\n'
        '    .card th { font-size: 0.7rem; }\n'
        '    .table-scroll-hint { display: block; }\n'
        '\n'
        '    /* Per-game expander: drop fixed 2-col grids to single column. */\n'
        '    .preview-grid { grid-template-columns: 1fr !important; gap: 0.8rem; }\n'
        '    .preview-card { padding: 0.9rem; }\n'
        '    .preview-card h5 { font-size: 0.9rem; }\n'
        '    /* The inner Lineup-Edge / Bullpen-Edge per-side panels also stack. */\n'
        '    .preview-card > div[style*="grid-template-columns:1fr 1fr"],\n'
        '    .preview-card > div[style*="grid-template-columns: 1fr 1fr"] {\n'
        '      display: block !important;\n'
        '    }\n'
        '    .preview-card > div[style*="grid-template-columns:1fr 1fr"] > div,\n'
        '    .preview-card > div[style*="grid-template-columns: 1fr 1fr"] > div {\n'
        '      margin-bottom: 0.8rem;\n'
        '    }\n'
        '\n'
        '    /* Bullpen Outlook 2-col team grids stack on mobile. */\n'
        '    #bullpen-outlook > div > div[style*="grid-template-columns:1fr 1fr"],\n'
        '    #bullpen-outlook > div > div[style*="grid-template-columns: 1fr 1fr"] {\n'
        '      display: block !important;\n'
        '    }\n'
        '    #bullpen-outlook > div > div[style*="grid-template-columns:1fr 1fr"] > div,\n'
        '    #bullpen-outlook > div > div[style*="grid-template-columns: 1fr 1fr"] > div {\n'
        '      margin-bottom: 0.8rem;\n'
        '    }\n'
        '\n'
        '    /* Deep-analysis sub-sections. */\n'
        '    .deep-section > .deep-h5 { font-size: 0.78rem; padding: 0.4rem 0; }\n'
        '    .deep-section > .deep-body { font-size: 0.84rem; }\n'
        '\n'
        '    /* Failure analysis side-by-side stacks. */\n'
        '    .failure-grid { grid-template-columns: 1fr !important; }\n'
        '\n'
        '    /* Disable hover-driven transforms on touch (avoid sticky state). */\n'
        '    @media (hover: none) {\n'
        '      .hero-cta:hover { transform: none; box-shadow: none; }\n'
        '      .accordion-header:hover { background: transparent; }\n'
        '      .row-clickable:hover { background: transparent; }\n'
        '    }\n'
        '  }\n'
        '\n'
        '  @media (max-width: 480px) {\n'
        '    /* Small phone refinements. */\n'
        '    header { padding: 0.85rem 0.9rem; }\n'
        '    header h1 { font-size: 1.05rem; }\n'
        '    header .meta { display: none; } /* save vertical space */\n'
        '    main { padding: 0.85rem 0.85rem 2rem 0.85rem; }\n'
        '    .card { padding: 0.9rem; }\n'
        '    .hero-headline { font-size: 2.2rem; }\n'
        '    .hero-tagline { font-size: 0.86rem; }\n'
        '    .hero-stat-value { font-size: 2rem; }\n'
        '    .accordion-header { padding: 1rem 0.85rem; font-size: 0.95rem; }\n'
        '    .accordion-body-inner { padding: 0 0.85rem 1rem 0.85rem; }\n'
        '    .card th, .card td { padding: 0.35rem 0.3rem; }\n'
        '    .card table { font-size: 0.74rem; }\n'
        '  }\n'
        '\n'
    )

    # Inject just before the closing </style> tag. The dashboard has many
    # </style> tags inside embedded JS strings, so we need a unique anchor.
    # The first real </style> closes the main <style> block in <head>.
    # Use the closing of the </style> right before "</head>" as the anchor.
    src = must_replace(
        src,
        '</style>\n'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js',
        css + '</style>\n'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js',
        "1: mobile CSS before </style>+chart.js",
    )
    print("[ok]   1: mobile CSS injected")

    # Add the scroll-hint markup inside the slate render function.
    # Anchor on the `</table></div>";` close in renderSlate.
    src = must_replace(
        src,
        '  html += "</tbody></table></div>";\n',
        '  html += "</tbody></table>"\n'
        '       + "<div class=\\"table-scroll-hint\\">← swipe →</div>"\n'
        '       + "</div>";\n',
        "2: scroll hint markup",
    )
    print("[ok]   2: scroll-hint markup added")

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
