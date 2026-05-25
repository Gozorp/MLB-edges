#!/usr/bin/env python3
"""
_patch_newbie_ux.py
===================
Newbie-friendly UX scaffold layered on top of Quant Terminal aesthetic.

Three pieces, all gated by localStorage so power users only see them once:
  1. Intro card (#mlb-intro-card) — auto-appears on first visit with a
     plain-English explanation: what the dashboard does, how to read a
     row, what the grades mean, where to find help later. Dismissable
     with X; sets localStorage.mlb_edge_intro_seen = "1".
  2. Help button (?) in header — opens a slide-over panel with the full
     column glossary + how-to. Always available, never auto-opens.
  3. Simple / Advanced mode toggle near the slate header — Simple hides
     F5, Fair, Edge, Pred, Tier, Claude (keeps Matchup, Pick, Full, O/U,
     Grade, Result). Mode persisted in localStorage.mlb_edge_slate_mode.
     First-time visitors default to Simple; returning users keep their
     last choice (defaults to Advanced if no record).

Plus: title= tooltips on every <th> in the slate table so hovering on a
column header explains it in plain English. Preserves the existing
Quant Terminal monospace + neon palette per locked memory.

Per locked memory: bash + Python str.replace; CRLF on .bat.
"""
from __future__ import annotations

import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "docs" / "index.html"


def must_replace(src: str, old: str, new: str, label: str = "") -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label or '(no label)'}: expected 1 occurrence, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    n0 = len(src)
    print("=== _patch_newbie_ux.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. CSS additions ----------
    css = (
        '  /* ===== Newbie UX scaffold (2026-05-24) ===== */\n'
        '  /* Intro card — auto-shown on first visit, dismissable */\n'
        '  #mlb-intro-card {\n'
        '    background: linear-gradient(135deg, rgba(63,185,80,0.05), rgba(88,166,255,0.05));\n'
        '    border: 1px solid var(--accent);\n'
        '    border-radius: 8px; padding: 1.2rem 1.4rem;\n'
        '    margin-bottom: 1.5rem; position: relative;\n'
        '  }\n'
        '  #mlb-intro-card.hidden { display: none; }\n'
        '  #mlb-intro-card h2 { margin-top: 0; color: var(--green); font-size: 1.05rem; }\n'
        '  #mlb-intro-card .intro-close {\n'
        '    position: absolute; top: 0.6rem; right: 0.9rem;\n'
        '    background: transparent; border: none; color: var(--muted);\n'
        '    font-size: 1.2rem; cursor: pointer; line-height: 1;\n'
        '    font-family: inherit;\n'
        '  }\n'
        '  #mlb-intro-card .intro-close:hover { color: var(--text); }\n'
        '  #mlb-intro-card .intro-grid {\n'
        '    display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem 1.4rem;\n'
        '    margin-top: 0.8rem; font-size: 0.88rem;\n'
        '  }\n'
        '  @media (max-width: 720px) {\n'
        '    #mlb-intro-card .intro-grid { grid-template-columns: 1fr; }\n'
        '  }\n'
        '  #mlb-intro-card .intro-step strong { color: var(--accent); }\n'
        '  #mlb-intro-card .intro-step .num {\n'
        '    display: inline-block; min-width: 1.4rem; color: var(--green);\n'
        '    font-weight: 700; font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '  }\n'
        '  /* Help button (?) in header */\n'
        '  #help-btn {\n'
        '    background: transparent; border: 1px solid var(--border);\n'
        '    color: var(--muted); width: 1.8rem; height: 1.8rem;\n'
        '    border-radius: 50%; cursor: pointer; font-family: inherit;\n'
        '    font-size: 0.9rem; font-weight: 700; padding: 0;\n'
        '    display: inline-flex; align-items: center; justify-content: center;\n'
        '    margin-left: 0.5rem; transition: all 0.15s;\n'
        '  }\n'
        '  #help-btn:hover { border-color: var(--accent); color: var(--accent); }\n'
        '  /* Slide-over help panel */\n'
        '  #help-panel-backdrop {\n'
        '    position: fixed; inset: 0; background: rgba(0,0,0,0.5);\n'
        '    z-index: 10000; display: none; backdrop-filter: blur(2px);\n'
        '  }\n'
        '  #help-panel-backdrop.open { display: block; }\n'
        '  #help-panel {\n'
        '    position: fixed; top: 0; right: 0; bottom: 0;\n'
        '    width: min(440px, 92vw); background: var(--bg-elev);\n'
        '    border-left: 1px solid var(--border); padding: 1.5rem 1.6rem;\n'
        '    overflow-y: auto; z-index: 10001;\n'
        '    transform: translateX(100%); transition: transform 0.22s ease-out;\n'
        '    box-shadow: -8px 0 24px rgba(0,0,0,0.4);\n'
        '  }\n'
        '  #help-panel.open { transform: translateX(0); }\n'
        '  #help-panel .help-close {\n'
        '    background: transparent; border: 1px solid var(--border);\n'
        '    color: var(--muted); padding: 0.25rem 0.55rem; cursor: pointer;\n'
        '    border-radius: 4px; font-family: inherit; font-size: 0.78rem;\n'
        '    float: right;\n'
        '  }\n'
        '  #help-panel h2 { margin-top: 0; color: var(--accent); font-size: 1.05rem; }\n'
        '  #help-panel h3 {\n'
        '    color: var(--text); font-size: 0.92rem; margin: 1.2rem 0 0.4rem 0;\n'
        '    text-transform: uppercase; letter-spacing: 0.05em;\n'
        '    border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.3rem;\n'
        '  }\n'
        '  #help-panel .glossary-row {\n'
        '    display: grid; grid-template-columns: 5.5rem 1fr;\n'
        '    gap: 0.5rem; padding: 0.4rem 0; font-size: 0.86rem;\n'
        '    border-bottom: 1px dashed rgba(255,255,255,0.06);\n'
        '  }\n'
        '  #help-panel .glossary-row .term {\n'
        '    color: var(--accent); font-weight: 700;\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '  }\n'
        '  #help-panel .glossary-row .def { color: var(--muted); line-height: 1.5; }\n'
        '  #help-panel .grade-row { display: flex; gap: 0.5rem; margin: 0.3rem 0; align-items: center; }\n'
        '  /* Simple / Advanced mode toggle near slate header */\n'
        '  .mode-toggle {\n'
        '    display: inline-flex; gap: 0; margin-left: 0.8rem;\n'
        '    border: 1px solid var(--border); border-radius: 4px;\n'
        '    overflow: hidden; vertical-align: middle;\n'
        '  }\n'
        '  .mode-toggle button {\n'
        '    background: transparent; border: none; color: var(--muted);\n'
        '    padding: 0.25rem 0.7rem; cursor: pointer;\n'
        '    font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    font-size: 0.74rem; letter-spacing: 0.04em;\n'
        '  }\n'
        '  .mode-toggle button.active {\n'
        '    background: var(--accent); color: var(--bg); font-weight: 700;\n'
        '  }\n'
        '  .mode-toggle button:not(.active):hover { color: var(--text); }\n'
        '  /* Simple mode column hides — driven by body.simple-mode */\n'
        '  body.simple-mode table th.col-f5,\n'
        '  body.simple-mode table td.col-f5,\n'
        '  body.simple-mode table th.col-fair,\n'
        '  body.simple-mode table td.col-fair,\n'
        '  body.simple-mode table th.col-edge,\n'
        '  body.simple-mode table td.col-edge,\n'
        '  body.simple-mode table th.col-pred,\n'
        '  body.simple-mode table td.col-pred,\n'
        '  body.simple-mode table th.col-tier,\n'
        '  body.simple-mode table td.col-tier,\n'
        '  body.simple-mode table th.col-claude,\n'
        '  body.simple-mode table td.col-claude { display: none; }\n'
        '  /* Help-tooltip cursor on column headers */\n'
        '  th[title] { cursor: help; }\n'
        '\n'
    )

    src = must_replace(
        src,
        '  /* Deep-analysis collapsible sub-sections (2026-05-24) */\n',
        css + '  /* Deep-analysis collapsible sub-sections (2026-05-24) */\n',
        )
    print("[ok]   1: CSS injected")

    # ---------- 2. Header: add ? help button ----------
    src = must_replace(
        src,
        '<header>\n'
        '  <h1>mlb_edge</h1>\n'
        '  <div class="meta">Daily MLB slate predictions with multi-rule grading</div>\n'
        '  <span id="visitPill" class="visit-pill muted-dim" title="Unique visit counter — one bump per browser">\n'
        '    <span class="visit-dot"></span>\n'
        '    <span id="visitText">… loading visits</span>\n'
        '  </span>\n'
        '</header>',
        '<header>\n'
        '  <h1>mlb_edge</h1>\n'
        '  <div class="meta">Daily MLB slate predictions with multi-rule grading</div>\n'
        '  <span id="visitPill" class="visit-pill muted-dim" title="Unique visit counter — one bump per browser">\n'
        '    <span class="visit-dot"></span>\n'
        '    <span id="visitText">… loading visits</span>\n'
        '  </span>\n'
        '  <button id="help-btn" title="Help &amp; glossary — what does each column mean?" aria-label="Open help panel">?</button>\n'
        '</header>',
    )
    print("[ok]   2: help button added to header")

    # ---------- 3. Intro card markup right after <main> opens ----------
    intro_markup = (
        '<main>\n'
        '  <div class="card" id="mlb-intro-card" style="display:none;">\n'
        '    <button class="intro-close" id="intro-close-btn" title="Got it, dismiss" aria-label="Dismiss intro">×</button>\n'
        '    <h2>First time here? Here is what this dashboard does.</h2>\n'
        '    <p style="margin:0.3rem 0 0.6rem 0;font-size:0.92rem;color:var(--muted);line-height:1.55;">\n'
        '      mlb_edge predicts every MLB game on today\'s slate and grades how confident the model is in each pick. It uses a rule-based system trained on historical results, not a crystal ball — read it as <em>where the model thinks the market is wrong</em>, not as guaranteed winners.\n'
        '    </p>\n'
        '    <div class="intro-grid">\n'
        '      <div class="intro-step"><span class="num">1.</span> <strong>Pick the date</strong> with the date picker above. Today\'s slate auto-refreshes during games.</div>\n'
        '      <div class="intro-step"><span class="num">2.</span> <strong>Each row is one game.</strong> The <code>Pick</code> column is the team the model favors, with its win probability.</div>\n'
        '      <div class="intro-step"><span class="num">3.</span> <strong>Grade tells you conviction:</strong> A = strong, B = moderate, C/D = skip. Click any row for the full reasoning.</div>\n'
        '      <div class="intro-step"><span class="num">4.</span> <strong>Need more detail?</strong> Toggle <code>Advanced</code> mode below to show all columns. Click the <strong>?</strong> button in the header any time for the glossary.</div>\n'
        '    </div>\n'
        '  </div>\n'
    )
    src = must_replace(src, '<main>\n', intro_markup)
    print("[ok]   3: intro card markup inserted")

    # ---------- 4. Help slide-over panel (right before </body>) ----------
    help_panel = (
        '<div id="help-panel-backdrop"></div>\n'
        '<aside id="help-panel" aria-label="Help and glossary" aria-hidden="true">\n'
        '  <button class="help-close" id="help-close-btn">close ✕</button>\n'
        '  <h2>Dashboard help</h2>\n'
        '  <p class="muted" style="font-size:0.88rem;line-height:1.55;margin:0.4rem 0 0.6rem 0;">\n'
        '    Quick reference for new visitors. The model predicts every MLB game on the slate and grades its confidence in each pick. Click any slate row for the full reasoning behind a pick.\n'
        '  </p>\n'
        '\n'
        '  <h3>How to use it</h3>\n'
        '  <ol style="margin:0.4rem 0 0.8rem 1.2rem;padding:0;font-size:0.86rem;line-height:1.55;">\n'
        '    <li>Pick a date in the date picker. Today auto-refreshes; past dates are archived.</li>\n'
        '    <li>Scan the <strong>Grade</strong> column. A and A− are the model\'s most confident calls. C/D = skip.</li>\n'
        '    <li>Click a row to open the deep-analysis panel: pitching matchup, lineup edge, bullpen state, counter-signals, what would change the call.</li>\n'
        '    <li>Use <strong>Simple</strong> mode (default for new users) to hide expert columns. Toggle to <strong>Advanced</strong> when you want the full data.</li>\n'
        '    <li>Ask-the-Slate (the <code>&gt; </code> prompt below the picker) lets you search the slate in plain English: "best pick", "show A grades", "parlay legs", or a team name.</li>\n'
        '  </ol>\n'
        '\n'
        '  <h3>What each column means</h3>\n'
        '  <div class="glossary-row"><div class="term">Matchup</div><div class="def">Away team @ Home team. A "(G2 of 3)" tag means it\'s game 2 of a 3-game series.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Pick</div><div class="def">Which team the model favors to win, plus the model\'s win probability for that team.</div></div>\n'
        '  <div class="glossary-row"><div class="term">F5</div><div class="def">First-5-innings win probability. Reflects the starting pitchers facing each other before bullpens take over.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Full</div><div class="def">Full-game win probability. The model\'s headline number — combines F5 + bullpen quality + late-leverage signals.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Fair</div><div class="def">Market-implied "fair" win probability, with the sportsbook\'s vig (juice) removed. Think of this as the market\'s honest opinion.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Edge</div><div class="def">Gap between Model and Fair, in percentage points. Positive = model thinks market is underrating the pick. &gt;5pp is meaningful.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Pred</div><div class="def">Model\'s projected final score (away–home), in runs.</div></div>\n'
        '  <div class="glossary-row"><div class="term">O/U</div><div class="def">Over/Under recommendation: side (OVER/UNDER), the Vegas total line, and the edge in runs. Empty = no market signal.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Tier</div><div class="def">Pre-grade conviction level: PLATINUM &gt; GOLD &gt; SILVER &gt; BRONZE &gt; SKIP. Assigned before the rule layer runs.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Grade</div><div class="def">Final grade after the 8-rule grader applies caps and adjustments. <strong>A/A−</strong> = strong; <strong>B/B+/B−</strong> = moderate; <strong>C</strong> = skip (cap fired); <strong>D</strong> = avoid.</div></div>\n'
        '  <div class="glossary-row"><div class="term">Claude</div><div class="def">Claude\'s executive review of the model\'s call: <strong>CONFIRM</strong> (agrees), <strong>DOWNGRADE</strong> (less confident), <strong>OVERRIDE</strong> (disagrees with direction).</div></div>\n'
        '  <div class="glossary-row"><div class="term">Result</div><div class="def">Post-game outcome: ✓ WIN / ✗ LOSS / LIVE (with score) / PRE-GAME / POSTPONED.</div></div>\n'
        '\n'
        '  <h3>Grade legend</h3>\n'
        '  <div class="grade-row"><span class="grade grade-A">A</span> <span style="color:var(--muted);font-size:0.85rem;">Strong conviction — multiple confirming layers.</span></div>\n'
        '  <div class="grade-row"><span class="grade grade-A-">A−</span> <span style="color:var(--muted);font-size:0.85rem;">Strong, just below A.</span></div>\n'
        '  <div class="grade-row"><span class="grade grade-Bp">B+</span> <span style="color:var(--muted);font-size:0.85rem;">Moderate signal.</span></div>\n'
        '  <div class="grade-row"><span class="grade grade-B">B</span> <span style="color:var(--muted);font-size:0.85rem;">Marginal edge.</span></div>\n'
        '  <div class="grade-row"><span class="grade grade-C">C</span> <span style="color:var(--muted);font-size:0.85rem;">Skip — a cap rule fired.</span></div>\n'
        '  <div class="grade-row"><span class="grade grade-D">D</span> <span style="color:var(--muted);font-size:0.85rem;">Avoid — direction disagreement or stress warnings.</span></div>\n'
        '\n'
        '  <h3>Anything else?</h3>\n'
        '  <p class="muted" style="font-size:0.85rem;line-height:1.5;">\n'
        '    This is a <strong>research dashboard</strong>, not betting advice. The grades reflect the model\'s confidence in its own picks; results vary, and historical accuracy is no guarantee of future games. Treat A-grade picks as worth examining, not as locks.\n'
        '  </p>\n'
        '</aside>\n'
        '\n'
        '</body>'
    )
    src = must_replace(src, '\n</body>', '\n' + help_panel)
    print("[ok]   4: help panel inserted")

    # ---------- 5. Tooltips on <th> headers + col-* classes ----------
    old_thead = (
        '      <thead><tr>\n'
        '        <th></th>\n'
        '        <th>Matchup</th><th>Pick</th><th>F5</th><th>Full</th>\n'
        '        <th>Fair</th><th>Edge</th><th>Pred</th>\n'
        '        <th title="Over/Under prediction: side · Vegas total · edge in runs. From picks_totals_<date>.csv">O/U</th>\n'
        '        <th>Tier</th><th>Grade</th>\n'
        '        <th title="Claude Brain (executive review): CONFIRM / DOWNGRADE / OVERRIDE">Claude</th>\n'
        '        ${haveAnyResult ? "<th>Result</th>" : ""}\n'
        '      </tr></thead><tbody>`;\n'
    )
    new_thead = (
        '      <thead><tr>\n'
        '        <th></th>\n'
        '        <th title="Away team @ Home team. (G2 of 3) = game 2 of a 3-game series.">Matchup</th>\n'
        '        <th title="Team the model favors to win, with its win probability for that team.">Pick</th>\n'
        '        <th class="col-f5" title="First-5-innings win probability. Reflects starting pitchers vs each other before bullpens.">F5</th>\n'
        '        <th title="Full-game win probability — the model\'s headline number combining SP + bullpen + late-leverage signals.">Full</th>\n'
        '        <th class="col-fair" title="Market-implied fair probability (sportsbook vig removed). The market\'s honest opinion.">Fair</th>\n'
        '        <th class="col-edge" title="Model − Fair, in percentage points. Positive = model thinks market is underrating the pick.">Edge</th>\n'
        '        <th class="col-pred" title="Model\'s projected final score (away–home), in runs.">Pred</th>\n'
        '        <th title="Over/Under: side (OVER/UNDER), Vegas total line, and edge in runs. Empty = no market signal.">O/U</th>\n'
        '        <th class="col-tier" title="Pre-grade conviction level: PLATINUM > GOLD > SILVER > BRONZE > SKIP.">Tier</th>\n'
        '        <th title="Final grade after the 8-rule grader: A/A− = strong, B family = moderate, C = skip, D = avoid.">Grade</th>\n'
        '        <th class="col-claude" title="Claude\'s executive review of the model\'s call: CONFIRM / DOWNGRADE / OVERRIDE.">Claude</th>\n'
        '        ${haveAnyResult ? \'<th title="Post-game outcome: WIN / LOSS / LIVE / PRE-GAME / POSTPONED.">Result</th>\' : ""}\n'
        '      </tr></thead><tbody>`;\n'
    )
    src = must_replace(src, old_thead, new_thead)
    print("[ok]   5: thead tooltips + col-* classes")

    # ---------- 6. Add col-* classes to the cell row ----------
    # Find the row's <td>${...} cells for F5, Fair, Edge, Pred, Tier, Claude
    # and tag them with the same classes so they hide together.
    src = must_replace(
        src,
        '      <td>${favTeamProb(matchupKey, r.f5_prob)}</td>\n'
        '      <td>${favTeamProb(matchupKey, r.full_prob)}</td>\n'
        '      <td>${fairFmt}</td>\n'
        '      <td>${edgeFmt}</td>\n'
        '      <td class="pred-score">${_formatPredScore(r)}</td>\n'
        '      ${ouCell}\n'
        '      <td>${tierBadge(r)}</td>\n'
        '      <td><span class="grade ${gradeClass(grade)}">${grade || "—"}</span></td>\n'
        '      ${claudeCell}\n',
        '      <td class="col-f5">${favTeamProb(matchupKey, r.f5_prob)}</td>\n'
        '      <td>${favTeamProb(matchupKey, r.full_prob)}</td>\n'
        '      <td class="col-fair">${fairFmt}</td>\n'
        '      <td class="col-edge">${edgeFmt}</td>\n'
        '      <td class="pred-score col-pred">${_formatPredScore(r)}</td>\n'
        '      ${ouCell}\n'
        '      <td class="col-tier">${tierBadge(r)}</td>\n'
        '      <td><span class="grade ${gradeClass(grade)}">${grade || "—"}</span></td>\n'
        '      ${claudeCell.replace(\'<td>\', \'<td class="col-claude">\').replace(\'<td class=\\\'\', \'<td class="col-claude \').replace(\'<td class="\', \'<td class="col-claude \')}\n',
        )
    print("[ok]   6: row cell col-* classes")

    # ---------- 7. Add Simple/Advanced mode toggle near slate header ----------
    src = must_replace(
        src,
        '    <h2>Slate (${rows.length} games) <span class="muted" style="font-size:0.8rem;font-weight:normal;">— click any row for in-depth reasoning</span></h2>\n',
        '    <h2 style="display:flex;align-items:center;flex-wrap:wrap;gap:0.4rem;">'
        'Slate (${rows.length} games) '
        '<span class="muted" style="font-size:0.8rem;font-weight:normal;">— click any row for in-depth reasoning</span>'
        '<span class="mode-toggle" role="tablist" aria-label="Slate detail level">'
        '<button id="mode-simple-btn" type="button" data-mode="simple">SIMPLE</button>'
        '<button id="mode-advanced-btn" type="button" data-mode="advanced">ADVANCED</button>'
        '</span>'
        '</h2>\n',
        )
    print("[ok]   7: simple/advanced toggle in slate header")

    # ---------- 8. JS: glossary + intro + help-panel + mode-toggle ----------
    js_block = (
        '\n'
        '// ============================================================\n'
        '// Newbie UX scaffold (2026-05-24)\n'
        '//   - Auto-show intro card on first visit (localStorage gate)\n'
        '//   - Open/close help slide-over panel\n'
        '//   - Simple / Advanced mode toggle + persistence\n'
        '// ============================================================\n'
        '(function() {\n'
        '  const LS_INTRO = "mlb_edge_intro_seen";\n'
        '  const LS_MODE = "mlb_edge_slate_mode";\n'
        '\n'
        '  function setMode(mode, persist) {\n'
        '    const m = mode === "simple" ? "simple" : "advanced";\n'
        '    document.body.classList.toggle("simple-mode", m === "simple");\n'
        '    const btnS = document.getElementById("mode-simple-btn");\n'
        '    const btnA = document.getElementById("mode-advanced-btn");\n'
        '    if (btnS) btnS.classList.toggle("active", m === "simple");\n'
        '    if (btnA) btnA.classList.toggle("active", m === "advanced");\n'
        '    if (persist) {\n'
        '      try { localStorage.setItem(LS_MODE, m); } catch (_) {}\n'
        '    }\n'
        '  }\n'
        '\n'
        '  function openHelp() {\n'
        '    const bd = document.getElementById("help-panel-backdrop");\n'
        '    const p  = document.getElementById("help-panel");\n'
        '    if (bd) bd.classList.add("open");\n'
        '    if (p)  { p.classList.add("open"); p.setAttribute("aria-hidden", "false"); }\n'
        '  }\n'
        '  function closeHelp() {\n'
        '    const bd = document.getElementById("help-panel-backdrop");\n'
        '    const p  = document.getElementById("help-panel");\n'
        '    if (bd) bd.classList.remove("open");\n'
        '    if (p)  { p.classList.remove("open"); p.setAttribute("aria-hidden", "true"); }\n'
        '  }\n'
        '\n'
        '  function init() {\n'
        '    // Intro card: show iff first visit. Defaults Simple mode on first visit.\n'
        '    let isFirst = false;\n'
        '    try {\n'
        '      isFirst = !localStorage.getItem(LS_INTRO);\n'
        '    } catch (_) { isFirst = true; }\n'
        '    const intro = document.getElementById("mlb-intro-card");\n'
        '    if (intro && isFirst) {\n'
        '      intro.style.display = "";\n'
        '    }\n'
        '    const closeBtn = document.getElementById("intro-close-btn");\n'
        '    if (closeBtn) {\n'
        '      closeBtn.addEventListener("click", function() {\n'
        '        if (intro) intro.style.display = "none";\n'
        '        try { localStorage.setItem(LS_INTRO, "1"); } catch (_) {}\n'
        '      });\n'
        '    }\n'
        '\n'
        '    // Mode toggle: first-visit default Simple; otherwise persisted choice (default Advanced).\n'
        '    let mode = "advanced";\n'
        '    try {\n'
        '      const saved = localStorage.getItem(LS_MODE);\n'
        '      if (saved === "simple" || saved === "advanced") mode = saved;\n'
        '      else if (isFirst) mode = "simple";\n'
        '    } catch (_) {\n'
        '      if (isFirst) mode = "simple";\n'
        '    }\n'
        '    setMode(mode, false);\n'
        '    document.addEventListener("click", function(e) {\n'
        '      const btn = e.target.closest("[data-mode]");\n'
        '      if (btn) {\n'
        '        setMode(btn.getAttribute("data-mode"), true);\n'
        '        e.stopPropagation();\n'
        '      }\n'
        '    });\n'
        '\n'
        '    // Help button + panel\n'
        '    const helpBtn = document.getElementById("help-btn");\n'
        '    if (helpBtn) helpBtn.addEventListener("click", openHelp);\n'
        '    const helpClose = document.getElementById("help-close-btn");\n'
        '    if (helpClose) helpClose.addEventListener("click", closeHelp);\n'
        '    const bd = document.getElementById("help-panel-backdrop");\n'
        '    if (bd) bd.addEventListener("click", closeHelp);\n'
        '    document.addEventListener("keydown", function(e) {\n'
        '      if (e.key === "Escape") closeHelp();\n'
        '    });\n'
        '  }\n'
        '\n'
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", init);\n'
        '  } else {\n'
        '    init();\n'
        '  }\n'
        '})();\n'
    )

    # Anchor on the close brace of the togglable-cards delegation handler
    # (added 2026-05-23) so our new script sits right after it inside the
    # existing inline <script> block.
    src = must_replace(
        src,
        '    sib = sib.nextElementSibling;\n'
        '  }\n'
        '});\n'
        '\n'
        '</script>',
        '    sib = sib.nextElementSibling;\n'
        '  }\n'
        '});\n'
        + js_block
        + '\n</script>',
    )
    print("[ok]   8: newbie-UX JS block")

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
    sys.exit(main())
    sys.exit(main())
      '</script>',
        '    sib = sib.nextElementSibling;\n'
        '  }\n'
        '});\n'
        + js_block
        + '\n</script>',
    )
    print("[ok]   8: newbie-UX JS block")

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
