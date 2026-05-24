#!/usr/bin/env python3
"""
_patch_bullpen_edge.py
======================
Adds a "Bullpen edge" preview-card to the per-game expander. Sits below
the "Lineup edge" card and above the projected-lineup cards. Per side:
  - K-prob list (per-batter) vs the opposing closer / top high-leverage
    arm, reusing the same Log5 _batterKProb helper that the Lineup-Edge
    SP version uses.
  - One-line strain note ("FRESH", "B2B - usable but limited",
    "OVERWORKED - effectively unavailable") sourced from the bullpen_meta
    sidecar's top_relievers list, matched by name. Surfaces the
    manager-decision context: a tired closer may be skipped tonight.

Reuses:
  - _batterKProb (from Lineup-Edge sprint)        — Log5 K-prob
  - _bullpenMetaForMatchup, _bullpenFlagBadge     — existing bullpen helpers
  - preview.awayBullpen / preview.homeBullpen     — top-3 arms from roster fetch

Per locked memory: bash + Python str.replace, CRLF on .bat.
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
    print("=== _patch_bullpen_edge.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. Insert renderBullpenEdge + helpers right BEFORE renderLineupEdge ----------
    helpers = (
        '// =====================================================================\n'
        '// Bullpen edge card — when the SP gives way to the closer/top arm,\n'
        '// who in the lineup is most vulnerable to a strikeout? Same Log5\n'
        '// math as the SP version, swapping in the opposing team\'s top\n'
        '// high-leverage reliever. Adds a strain note pulled from bullpen_meta\n'
        '// so the user can see when the obvious leverage arm is on B2B / 3-day\n'
        '// and the manager will likely reach for someone else.\n'
        '// =====================================================================\n'
        'function _bullpenStrainNote(teamBlock, closerName) {\n'
        '  if (!teamBlock || !closerName || !Array.isArray(teamBlock.top_relievers)) {\n'
        '    return null;\n'
        '  }\n'
        '  // Name match — bullpen_meta names sometimes carry "Jr." / accents\n'
        '  // that the roster fetch strips; do a loose contains-test in either\n'
        '  // direction so we don\'t silently miss the closer.\n'
        '  const lc = String(closerName).toLowerCase();\n'
        '  const rel = teamBlock.top_relievers.find(r => {\n'
        '    const rn = String(r.name || "").toLowerCase();\n'
        '    return rn && (rn === lc || rn.includes(lc) || lc.includes(rn));\n'
        '  });\n'
        '  if (!rel) return null;\n'
        '  const flag = String(rel.fatigue_flag || "").toUpperCase();\n'
        '  if (flag === "FRESH" || flag === "NORMAL" || flag === "") return null;\n'
        '  if (flag === "B2B") {\n'
        '    return `<span style="color:var(--yellow);">${closerName} on B2B — usable but limited; setup man may take the 9th</span>`;\n'
        '  }\n'
        '  if (flag === "B2B2B" || flag === "OVERWORKED") {\n'
        '    return `<span class="flag-red">${closerName} ${flag === "B2B2B" ? "on 3-day" : "OVERWORKED"} — effectively unavailable tonight</span>`;\n'
        '  }\n'
        '  return null;\n'
        '}\n'
        '\n'
        'function _topBullpenArm(bullpen) {\n'
        '  // preview.{away|home}Bullpen is already sorted by save+hold weight in\n'
        '  // _fetchTeamRoster, so element [0] is the closer proxy.\n'
        '  if (!Array.isArray(bullpen) || bullpen.length === 0) return null;\n'
        '  return bullpen[0];\n'
        '}\n'
        '\n'
        'function renderBullpenEdge(preview) {\n'
        '  if (!preview) return "";\n'
        '  // Look up bullpen_meta for the strain notes (best-effort; if the\n'
        '  // sidecar isn\'t baked yet we simply omit the strain line).\n'
        '  const meta = (function() {\n'
        '    try {\n'
        '      const m = `${preview.awayAbbr || preview.awayName} @ ${preview.homeAbbr || preview.homeName}`;\n'
        '      return _bullpenMetaForMatchup(m) || {};\n'
        '    } catch (_) { return {}; }\n'
        '  })();\n'
        '  const awayCloser = _topBullpenArm(preview.awayBullpen);\n'
        '  const homeCloser = _topBullpenArm(preview.homeBullpen);\n'
        '\n'
        '  const kList = (lineup, opposingCloser, teamName, teamAbbr, oppTeamBlock) => {\n'
        '    if (!opposingCloser) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: opposing bullpen not yet hydrated.</div>`;\n'
        '    }\n'
        '    if (!lineup || !lineup.length) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: lineup card not yet posted.</div>`;\n'
        '    }\n'
        '    const rows = [];\n'
        '    for (const b of lineup) {\n'
        '      const pK = _batterKProb(b, opposingCloser);\n'
        '      if (pK == null) continue;\n'
        '      rows.push({ name: b.name, pos: b.pos, pa: b.pa, prob: pK });\n'
        '    }\n'
        '    if (!rows.length) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: not enough batter PA for K-projection.</div>`;\n'
        '    }\n'
        '    rows.sort((a, b) => b.prob - a.prob);\n'
        '    const meanK = rows.reduce((s, r) => s + r.prob, 0) / rows.length;\n'
        '    const contactPct = (1 - meanK) * 100;\n'
        '    const k9Str = parseFloat(opposingCloser.k9 || 0).toFixed(1);\n'
        '    const strain = _bullpenStrainNote(oppTeamBlock, opposingCloser.name);\n'
        '    let html = `<div>`\n'
        '             + `<div style="display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.25rem;">`\n'
        '             +   `<strong style="font-size:0.88rem;">${teamAbbr || teamName} lineup</strong>`\n'
        '             +   `<span class="muted" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:0.76rem;">`\n'
        '             +     `contact ${contactPct.toFixed(0)}% · vs ${opposingCloser.name} (${k9Str} K/9)`\n'
        '             +   `</span>`\n'
        '             + `</div>`;\n'
        '    if (strain) {\n'
        '      html += `<div style="font-size:0.76rem;margin:0.2rem 0 0.3rem 0;font-style:italic;">${strain}</div>`;\n'
        '    }\n'
        '    html += `<ol style="margin:0.2rem 0 0 1.4rem;padding:0;font-size:0.8rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`;\n'
        '    for (const r of rows) {\n'
        '      const pct = (r.prob * 100).toFixed(0);\n'
        '      const color = r.prob >= 0.32 ? "var(--red)"\n'
        '                  : r.prob >= 0.25 ? "var(--yellow)"\n'
        '                  : "var(--muted)";\n'
        '      html += `<li style="margin:0.1rem 0;">`\n'
        '            +   `<span style="display:inline-block;min-width:3.2rem;color:${color};font-weight:700;">${pct}% K</span>`\n'
        '            +   `<span class="muted"> · </span>`\n'
        '            +   `<span style="color:var(--text);">${r.name}</span>`\n'
        '            +   `<span class="muted"> (${r.pos || "-"})</span>`\n'
        '            + `</li>`;\n'
        '    }\n'
        '    html += `</ol></div>`;\n'
        '    return html;\n'
        '  };\n'
        '\n'
        '  return `<div class="preview-card" style="grid-column: 1 / -1;">`\n'
        '       + `<h5>Bullpen edge <span class="muted" style="font-size:0.78rem;font-weight:normal;">— per-batter K vs the opposing top-leverage arm + manager-decision context</span></h5>`\n'
        '       + `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0.9rem;">`\n'
        '       +   kList(preview.awayLineup, homeCloser, preview.awayName, preview.awayAbbr, meta.home)\n'
        '       +   kList(preview.homeLineup, awayCloser, preview.homeName, preview.homeAbbr, meta.away)\n'
        '       + `</div></div>`;\n'
        '}\n'
        '\n'
    )

    src = must_replace(
        src,
        '// =====================================================================\n'
        '// Lineup edge card — composite lineup score + per-batter K-vulnerability\n',
        helpers
        + '// =====================================================================\n'
        '// Lineup edge card — composite lineup score + per-batter K-vulnerability\n',
        "1: insert renderBullpenEdge + helpers before renderLineupEdge",
    )

    # ---------- 2. Wire renderBullpenEdge into the preview grid ----------
    # Insert right AFTER renderLineupEdge so the Bullpen Edge card sits
    # between Lineup Edge and the per-team lineup cards.
    src = must_replace(
        src,
        '      ${renderLineupEdge(preview)}\n'
        '      <div class="preview-card">\n'
        '        <h5>${preview.awayName} top hitters (by 2026 OPS)</h5>',
        '      ${renderLineupEdge(preview)}\n'
        '      ${renderBullpenEdge(preview)}\n'
        '      <div class="preview-card">\n'
        '        <h5>${preview.awayName} top hitters (by 2026 OPS)</h5>',
        "2: wire renderBullpenEdge into preview grid",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
