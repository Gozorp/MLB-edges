#!/usr/bin/env python3
"""
_patch_accordion.py
===================
New 'Ask the Slate' section that wraps the existing #top-outcomes,
#bullpen-outlook, and #slate divs in an Apple-style accordion menu.
All three items are collapsed by default; clicking any header expands
its body with a smooth max-height transition and rotates the chevron.

The inner div IDs (#top-outcomes / #bullpen-outlook / #slate) are
preserved so the existing rendering pipeline still injects HTML into
them — we just wrap them in accordion bodies.

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

    # ---------- 1. CSS for accordion ----------
    css = (
        '  /* ===== Ask the Slate accordion (2026-05-25) ===== */\n'
        '  #ask-the-slate-section {\n'
        '    margin: 2.5rem 0 1.5rem 0;\n'
        '  }\n'
        '  #ask-the-slate-section > h2 {\n'
        '    font-size: clamp(1.6rem, 3vw, 2.4rem);\n'
        '    font-weight: 700; margin: 0 0 1.2rem 0;\n'
        '    letter-spacing: -0.015em;\n'
        '    background: linear-gradient(135deg, var(--text) 0%, var(--accent) 100%);\n'
        '    -webkit-background-clip: text; background-clip: text;\n'
        '    -webkit-text-fill-color: transparent;\n'
        '  }\n'
        '  .accordion {\n'
        '    border: 1px solid var(--border); border-radius: 12px;\n'
        '    overflow: hidden; background: var(--bg-elev);\n'
        '  }\n'
        '  .accordion-item {\n'
        '    border-bottom: 1px solid var(--border);\n'
        '  }\n'
        '  .accordion-item:last-child { border-bottom: none; }\n'
        '  .accordion-header {\n'
        '    display: flex; align-items: center; justify-content: space-between;\n'
        '    width: 100%; background: transparent; border: none;\n'
        '    padding: 1.2rem 1.5rem; cursor: pointer;\n'
        '    color: var(--text); font-family: inherit;\n'
        '    font-size: 1.1rem; font-weight: 600;\n'
        '    text-align: left; transition: background 0.18s ease;\n'
        '  }\n'
        '  .accordion-header:hover { background: rgba(88,166,255,0.05); }\n'
        '  .accordion-header .chev {\n'
        '    color: var(--muted); font-size: 1.4rem;\n'
        '    transition: transform 0.32s cubic-bezier(0.4, 0, 0.2, 1);\n'
        '    display: inline-block; line-height: 1;\n'
        '  }\n'
        '  .accordion-item.open .accordion-header .chev {\n'
        '    transform: rotate(180deg); color: var(--accent);\n'
        '  }\n'
        '  .accordion-body {\n'
        '    max-height: 0; overflow: hidden;\n'
        '    transition: max-height 0.42s cubic-bezier(0.4, 0, 0.2, 1);\n'
        '  }\n'
        '  .accordion-item.open .accordion-body {\n'
        '    max-height: 12000px; /* large enough for any rendered content */\n'
        '  }\n'
        '  .accordion-body-inner {\n'
        '    padding: 0 1.5rem 1.5rem 1.5rem;\n'
        '  }\n'
        '  /* The inner cards rendered into #top-outcomes/#bullpen-outlook/#slate\n'
        '     already have their own .card styling - reset their margins so they\n'
        '     don\'t double-pad inside the accordion body. */\n'
        '  .accordion-body-inner > .card {\n'
        '    margin-bottom: 0;\n'
        '    background: transparent; border: none; padding: 0.5rem 0;\n'
        '  }\n'
        '  .accordion-eyebrow {\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    color: var(--muted); font-size: 0.72rem;\n'
        '    letter-spacing: 0.18em; text-transform: uppercase;\n'
        '    margin-bottom: 0.6rem; display: block;\n'
        '  }\n'
        '\n'
    )

    src = must_replace(
        src,
        '  /* ===== Apple-style hero section (2026-05-25) ===== */\n',
        css + '  /* ===== Apple-style hero section (2026-05-25) ===== */\n',
        "1: accordion CSS",
    )
    print("[ok]   1: accordion CSS injected")

    # ---------- 2. Replace the existing div trio with accordion wrapper ----------
    old_trio = (
        '  <div id="top-outcomes"></div>\n'
        '  <div id="bullpen-outlook"></div>\n'
        '  <div id="slate"></div>\n'
        '  <div id="parlay"></div>\n'
    )
    new_trio = (
        '  <section id="ask-the-slate-section">\n'
        '    <span class="accordion-eyebrow">Browse the slate</span>\n'
        '    <h2>Ask the Slate</h2>\n'
        '    <div class="accordion" role="tablist">\n'
        '      <div class="accordion-item" data-acc="top-outcomes">\n'
        '        <button class="accordion-header" type="button" aria-expanded="false" data-acc-toggle>\n'
        '          <span>Top Probable Outcomes</span>\n'
        '          <span class="chev" aria-hidden="true">▾</span>\n'
        '        </button>\n'
        '        <div class="accordion-body">\n'
        '          <div class="accordion-body-inner">\n'
        '            <div id="top-outcomes"></div>\n'
        '          </div>\n'
        '        </div>\n'
        '      </div>\n'
        '      <div class="accordion-item" data-acc="bullpen-outlook">\n'
        '        <button class="accordion-header" type="button" aria-expanded="false" data-acc-toggle>\n'
        '          <span>Bullpen Outlook</span>\n'
        '          <span class="chev" aria-hidden="true">▾</span>\n'
        '        </button>\n'
        '        <div class="accordion-body">\n'
        '          <div class="accordion-body-inner">\n'
        '            <div id="bullpen-outlook"></div>\n'
        '          </div>\n'
        '        </div>\n'
        '      </div>\n'
        '      <div class="accordion-item" data-acc="slate">\n'
        '        <button class="accordion-header" type="button" aria-expanded="false" data-acc-toggle>\n'
        '          <span>The Slate</span>\n'
        '          <span class="chev" aria-hidden="true">▾</span>\n'
        '        </button>\n'
        '        <div class="accordion-body">\n'
        '          <div class="accordion-body-inner">\n'
        '            <div id="slate"></div>\n'
        '          </div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </section>\n'
        '  <div id="parlay"></div>\n'
    )
    src = must_replace(src, old_trio, new_trio, "2: accordion wrapper")
    print("[ok]   2: accordion HTML wrapping injected")

    # ---------- 3. JS click handler ----------
    js = (
        '\n'
        '// =============================================================\n'
        '// "Ask the Slate" accordion (2026-05-25)\n'
        '//   Toggles .open on .accordion-item when its header is clicked.\n'
        '//   Each body uses max-height transitions; we set a large ceiling\n'
        '//   in CSS so any rendered content (slate table with 16+ rows)\n'
        '//   fits without measuring scrollHeight on every toggle.\n'
        '// =============================================================\n'
        '(function() {\n'
        '  document.addEventListener("click", function(e) {\n'
        '    const btn = e.target.closest("[data-acc-toggle]");\n'
        '    if (!btn) return;\n'
        '    const item = btn.closest(".accordion-item");\n'
        '    if (!item) return;\n'
        '    const willOpen = !item.classList.contains("open");\n'
        '    item.classList.toggle("open");\n'
        '    btn.setAttribute("aria-expanded", String(willOpen));\n'
        '    e.stopPropagation();\n'
        '  });\n'
        '})();\n'
    )

    src = must_replace(
        src,
        '    sib = sib.nextElementSibling;\n'
        '  }\n'
        '});\n',
        '    sib = sib.nextElementSibling;\n'
        '  }\n'
        '});\n'
        + js,
        "3: accordion JS",
    )
    print("[ok]   3: accordion JS injected")

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
