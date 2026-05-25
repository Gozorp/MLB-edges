#!/usr/bin/env python3
"""
_patch_hero_section.py
======================
Phase 1 of the Apple-style overhaul: add a full-viewport hero section
above the existing dashboard. Animated count-up stats for today's pick
count, A-grade count, and live tracker indicator. Gradient background
with subtle parallax. Scroll indicator. Smooth-scroll throughout.

The existing dashboard (date picker, slate table, expanders, etc.)
stays intact below the hero — this is additive, not destructive.

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

    # ---------- 1. CSS for hero + animations ----------
    css = (
        '  /* ===== Apple-style hero section (2026-05-25) ===== */\n'
        '  html { scroll-behavior: smooth; }\n'
        '\n'
        '  #mlb-hero {\n'
        '    position: relative; min-height: 92vh;\n'
        '    display: flex; flex-direction: column; justify-content: center;\n'
        '    align-items: center; text-align: center;\n'
        '    padding: 4rem 2rem 5rem 2rem; overflow: hidden;\n'
        '    background: radial-gradient(ellipse at top left, rgba(63,185,80,0.10), transparent 55%),\n'
        '                radial-gradient(ellipse at bottom right, rgba(88,166,255,0.10), transparent 55%),\n'
        '                linear-gradient(180deg, var(--bg) 0%, var(--bg-elev) 100%);\n'
        '    border-bottom: 1px solid var(--border);\n'
        '  }\n'
        '  #mlb-hero::before {\n'
        '    content: ""; position: absolute; inset: 0;\n'
        '    background: radial-gradient(circle at 30% 20%, rgba(63,185,80,0.05) 0%, transparent 25%),\n'
        '                radial-gradient(circle at 70% 80%, rgba(88,166,255,0.05) 0%, transparent 25%);\n'
        '    animation: heroPulse 14s ease-in-out infinite;\n'
        '    pointer-events: none;\n'
        '  }\n'
        '  @keyframes heroPulse {\n'
        '    0%, 100% { opacity: 1; transform: scale(1); }\n'
        '    50% { opacity: 0.6; transform: scale(1.05); }\n'
        '  }\n'
        '  .hero-eyebrow {\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    color: var(--green); font-size: 0.78rem;\n'
        '    text-transform: uppercase; letter-spacing: 0.3em;\n'
        '    margin-bottom: 1.2rem; opacity: 0;\n'
        '    animation: heroFadeUp 0.7s ease-out 0.05s forwards;\n'
        '  }\n'
        '  .hero-headline {\n'
        '    font-size: clamp(2.5rem, 6vw, 5rem); font-weight: 700;\n'
        '    line-height: 1.05; letter-spacing: -0.02em;\n'
        '    margin: 0 0 1rem 0; opacity: 0;\n'
        '    animation: heroFadeUp 0.8s ease-out 0.2s forwards;\n'
        '    background: linear-gradient(135deg, var(--text) 0%, var(--accent) 100%);\n'
        '    -webkit-background-clip: text; background-clip: text;\n'
        '    -webkit-text-fill-color: transparent;\n'
        '    max-width: 14ch;\n'
        '  }\n'
        '  .hero-tagline {\n'
        '    font-size: clamp(1rem, 1.8vw, 1.3rem); color: var(--muted);\n'
        '    font-weight: 400; margin: 0 0 2.5rem 0;\n'
        '    max-width: 50ch; line-height: 1.5; opacity: 0;\n'
        '    animation: heroFadeUp 0.8s ease-out 0.35s forwards;\n'
        '  }\n'
        '  .hero-stats {\n'
        '    display: flex; gap: 2.5rem; flex-wrap: wrap;\n'
        '    justify-content: center; margin: 1rem 0 2rem 0;\n'
        '    opacity: 0; animation: heroFadeUp 0.9s ease-out 0.5s forwards;\n'
        '  }\n'
        '  .hero-stat {\n'
        '    display: flex; flex-direction: column; align-items: center;\n'
        '    min-width: 6rem;\n'
        '  }\n'
        '  .hero-stat-value {\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    font-size: clamp(2rem, 4.5vw, 3.4rem); font-weight: 700;\n'
        '    line-height: 1; color: var(--text);\n'
        '    font-variant-numeric: tabular-nums;\n'
        '  }\n'
        '  .hero-stat-value.green { color: var(--green); }\n'
        '  .hero-stat-value.accent { color: var(--accent); }\n'
        '  .hero-stat-label {\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    font-size: 0.7rem; color: var(--muted);\n'
        '    text-transform: uppercase; letter-spacing: 0.18em;\n'
        '    margin-top: 0.5rem;\n'
        '  }\n'
        '  .hero-cta {\n'
        '    display: inline-flex; align-items: center; gap: 0.5rem;\n'
        '    background: var(--accent); color: var(--bg);\n'
        '    border: none; padding: 0.85rem 1.8rem;\n'
        '    border-radius: 999px; font-size: 0.95rem; font-weight: 600;\n'
        '    cursor: pointer; text-decoration: none;\n'
        '    transition: transform 0.18s ease, box-shadow 0.18s ease;\n'
        '    opacity: 0; animation: heroFadeUp 0.8s ease-out 0.7s forwards;\n'
        '  }\n'
        '  .hero-cta:hover {\n'
        '    transform: translateY(-2px);\n'
        '    box-shadow: 0 8px 24px rgba(88,166,255,0.3);\n'
        '  }\n'
        '  .hero-scroll-indicator {\n'
        '    position: absolute; bottom: 2rem; left: 50%;\n'
        '    transform: translateX(-50%); color: var(--muted);\n'
        '    font-size: 0.78rem; font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    text-transform: uppercase; letter-spacing: 0.2em;\n'
        '    display: flex; flex-direction: column; align-items: center;\n'
        '    gap: 0.5rem; opacity: 0;\n'
        '    animation: heroFadeUp 1s ease-out 1s forwards, heroFloat 2.5s ease-in-out 1.5s infinite;\n'
        '  }\n'
        '  .hero-scroll-indicator .arrow {\n'
        '    width: 1.5rem; height: 1.5rem;\n'
        '    border-right: 2px solid var(--muted);\n'
        '    border-bottom: 2px solid var(--muted);\n'
        '    transform: rotate(45deg);\n'
        '  }\n'
        '  @keyframes heroFadeUp {\n'
        '    from { opacity: 0; transform: translateY(20px); }\n'
        '    to   { opacity: 1; transform: translateY(0); }\n'
        '  }\n'
        '  @keyframes heroFloat {\n'
        '    0%, 100% { transform: translate(-50%, 0); }\n'
        '    50%      { transform: translate(-50%, 10px); }\n'
        '  }\n'
        '\n'
        '  /* IntersectionObserver fade-in for cards as they enter viewport */\n'
        '  .reveal-on-scroll {\n'
        '    opacity: 0; transform: translateY(24px);\n'
        '    transition: opacity 0.7s ease-out, transform 0.7s ease-out;\n'
        '  }\n'
        '  .reveal-on-scroll.revealed { opacity: 1; transform: translateY(0); }\n'
        '\n'
        '  @media (max-width: 720px) {\n'
        '    #mlb-hero { min-height: 80vh; padding: 3rem 1.2rem; }\n'
        '    .hero-stats { gap: 1.5rem; }\n'
        '  }\n'
        '\n'
    )

    src = must_replace(
        src,
        '  /* ===== Newbie UX scaffold (2026-05-24) ===== */\n',
        css + '  /* ===== Newbie UX scaffold (2026-05-24) ===== */\n',
        "1: hero CSS",
    )
    print("[ok]   1: hero CSS injected")

    # ---------- 2. Hero markup BETWEEN <header> and <main> ----------
    hero = (
        '\n<section id="mlb-hero" aria-label="mlb_edge hero">\n'
        '  <div class="hero-eyebrow">Daily MLB Slate · The Quant Terminal</div>\n'
        '  <h2 class="hero-headline">Find the model\'s edge.</h2>\n'
        '  <p class="hero-tagline">\n'
        '    Multi-rule grading of every game on today\'s slate. Pre-game picks locked at first pitch, live win-prob trajectories during play, and a deep-analysis panel under every row.\n'
        '  </p>\n'
        '  <div class="hero-stats">\n'
        '    <div class="hero-stat">\n'
        '      <div class="hero-stat-value" id="hero-stat-games" data-target="0">0</div>\n'
        '      <div class="hero-stat-label">Games today</div>\n'
        '    </div>\n'
        '    <div class="hero-stat">\n'
        '      <div class="hero-stat-value green" id="hero-stat-a-grades" data-target="0">0</div>\n'
        '      <div class="hero-stat-label">A · A− picks</div>\n'
        '    </div>\n'
        '    <div class="hero-stat">\n'
        '      <div class="hero-stat-value accent" id="hero-stat-live" data-target="0">0</div>\n'
        '      <div class="hero-stat-label">Live now</div>\n'
        '    </div>\n'
        '  </div>\n'
        '  <a href="#main-slate-anchor" class="hero-cta">View today\'s slate ↓</a>\n'
        '  <div class="hero-scroll-indicator">\n'
        '    <span>Scroll</span>\n'
        '    <div class="arrow"></div>\n'
        '  </div>\n'
        '</section>\n'
        '\n<main id="main-slate-anchor">\n'
    )

    src = must_replace(src, '\n<main>\n', hero, "2: hero markup")
    print("[ok]   2: hero markup inserted")

    # ---------- 3. JS: counter animation + IntersectionObserver ----------
    js = (
        '\n'
        '// =============================================================\n'
        '// Apple-style hero animations (2026-05-25)\n'
        '//   - Count-up animation for the 3 hero stats once the slate\n'
        '//     loads (games count, A-grade count, live-game count).\n'
        '//   - IntersectionObserver adds .revealed to .reveal-on-scroll\n'
        '//     elements when they enter the viewport.\n'
        '// =============================================================\n'
        '(function() {\n'
        '  function animateCounter(el, target, duration) {\n'
        '    if (!el) return;\n'
        '    const start = parseFloat(el.textContent) || 0;\n'
        '    const dur = duration || 900;\n'
        '    const startTime = performance.now();\n'
        '    function step(now) {\n'
        '      const t = Math.min(1, (now - startTime) / dur);\n'
        '      // ease-out cubic\n'
        '      const eased = 1 - Math.pow(1 - t, 3);\n'
        '      const val = Math.round(start + (target - start) * eased);\n'
        '      el.textContent = val;\n'
        '      if (t < 1) requestAnimationFrame(step);\n'
        '    }\n'
        '    requestAnimationFrame(step);\n'
        '  }\n'
        '\n'
        '  function updateHeroStats() {\n'
        '    const slate = window.__slate || {};\n'
        '    const rows = slate.rows || [];\n'
        '    const results = slate.results || {};\n'
        '    const games = rows.length;\n'
        '    const aGrades = rows.filter(r => {\n'
        '      const g = (r.grade || "").trim();\n'
        '      return g === "A" || g === "A-" || g === "A−";\n'
        '    }).length;\n'
        '    let liveCount = 0;\n'
        '    for (const k of Object.keys(results)) {\n'
        '      const r = results[k];\n'
        '      const s = String(r.statusText || "").toLowerCase();\n'
        '      if (/in progress|manager challenge|delayed/.test(s)) {\n'
        '        liveCount++;\n'
        '      }\n'
        '    }\n'
        '    // Each unique game has up to 4 keys in the results map (away@home,\n'
        '    // home@away, and DH variants). Divide by 4 as a rough dedupe.\n'
        '    liveCount = Math.round(liveCount / 4);\n'
        '    const sg = document.getElementById("hero-stat-games");\n'
        '    const sa = document.getElementById("hero-stat-a-grades");\n'
        '    const sl = document.getElementById("hero-stat-live");\n'
        '    if (sg) { sg.setAttribute("data-target", games); animateCounter(sg, games); }\n'
        '    if (sa) { sa.setAttribute("data-target", aGrades); animateCounter(sa, aGrades); }\n'
        '    if (sl) { sl.setAttribute("data-target", liveCount); animateCounter(sl, liveCount); }\n'
        '  }\n'
        '\n'
        '  function installRevealObserver() {\n'
        '    if (typeof IntersectionObserver !== "function") return;\n'
        '    const cards = document.querySelectorAll(".card, .preview-card");\n'
        '    cards.forEach(c => c.classList.add("reveal-on-scroll"));\n'
        '    const obs = new IntersectionObserver(function(entries) {\n'
        '      entries.forEach(e => {\n'
        '        if (e.isIntersecting) {\n'
        '          e.target.classList.add("revealed");\n'
        '          obs.unobserve(e.target);\n'
        '        }\n'
        '      });\n'
        '    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });\n'
        '    cards.forEach(c => obs.observe(c));\n'
        '  }\n'
        '\n'
        '  function init() {\n'
        '    installRevealObserver();\n'
        '    // Update stats once the slate has loaded. Try a few times since\n'
        '    // loadSlate is async.\n'
        '    let tries = 0;\n'
        '    const t = setInterval(function() {\n'
        '      tries++;\n'
        '      const slate = window.__slate || {};\n'
        '      if ((slate.rows && slate.rows.length) || tries > 30) {\n'
        '        updateHeroStats();\n'
        '        clearInterval(t);\n'
        '        // Re-install observer on any newly-rendered .card elements.\n'
        '        installRevealObserver();\n'
        '      }\n'
        '    }, 400);\n'
        '\n'
        '    // Re-update on every slate render (date change, refresh).\n'
        '    const slateEl = document.getElementById("slate");\n'
        '    if (slateEl && typeof MutationObserver === "function") {\n'
        '      const mo = new MutationObserver(function() {\n'
        '        updateHeroStats();\n'
        '        installRevealObserver();\n'
        '      });\n'
        '      mo.observe(slateEl, { childList: true });\n'
        '    }\n'
        '  }\n'
        '\n'
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", init);\n'
        '  } else {\n'
        '    init();\n'
        '  }\n'
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
        "3: hero JS",
    )
    print("[ok]   3: hero JS injected")

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
