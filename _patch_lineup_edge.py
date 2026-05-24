#!/usr/bin/env python3
"""
_patch_lineup_edge.py
=====================
Adds a "Lineup edge" card to the per-game expander, above the projected
lineup cards. The card shows:

  1. Composite winner banner (xwOBA + wRC+, sourced from
     player_aware_signal JSON). "ATL clear edge (+18): xwOBA 0.332 ·
     wRC+ 112 vs xwOBA 0.305 · wRC+ 94".
  2. Per-batter K-vulnerability lists, sorted high-to-low, for each
     side vs the opposing SP. Uses a Log5 / odds-ratio combine:
       p_K = clamp((batter_K_rate * pitcher_K_rate) / league_K_rate)
     league_K_rate ≈ 0.225; batter_K_rate = SO / PA;
     pitcher_K_rate ≈ k9 / 38 (PA per 9 IP).

Also widens _fetchTeamRoster to capture batter strikeouts (`so`), and
propagates `so` through idToBat / _enrich so the lineup objects carry it.

Per locked memory:
  - feedback_edit_tool_pivot — str.replace only.
  - feedback_bat_crlf — driver .bat must be CRLF (caller handles).
  - feedback_quant_terminal_identity — keep monospace / compact / card-style.

Run from D:\\mlb_edge\\mlb_edge after curl-refreshing docs/index.html.
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
    print("=== _patch_lineup_edge.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. Capture batter SO in _fetchTeamRoster ----------
    src = must_replace(
        src,
        '            batters.push({\n'
        '              name: p.fullName, pos, ops: st.ops, ops_f: ops,\n'
        '              avg: st.avg, hr: st.homeRuns, pa, rbi: st.rbi\n'
        '            });',
        '            batters.push({\n'
        '              name: p.fullName, pos, ops: st.ops, ops_f: ops,\n'
        '              avg: st.avg, hr: st.homeRuns, pa, rbi: st.rbi,\n'
        '              so: parseInt(st.strikeOuts || 0)\n'
        '            });',
        "1: capture batter SO in roster fetch",
    )

    # ---------- 2. Propagate `so` through idToBat ----------
    src = must_replace(
        src,
        '      return {\n'
        '        id, name: byId[id].name, pos: byId[id].pos,\n'
        '        ops: m ? m.ops : null,\n'
        '        avg: m ? m.avg : null,\n'
        '        hr:  m ? m.hr  : 0,\n'
        '        rbi: m ? m.rbi : 0,\n'
        '        pa:  m ? m.pa  : 0,\n'
        '      };',
        '      return {\n'
        '        id, name: byId[id].name, pos: byId[id].pos,\n'
        '        ops: m ? m.ops : null,\n'
        '        avg: m ? m.avg : null,\n'
        '        hr:  m ? m.hr  : 0,\n'
        '        rbi: m ? m.rbi : 0,\n'
        '        pa:  m ? m.pa  : 0,\n'
        '        so:  m ? m.so  : 0,\n'
        '      };',
        "2: propagate SO through idToBat",
    )

    # ---------- 3. Propagate `so` through _enrich (projected lineup) ----------
    src = must_replace(
        src,
        '    return {\n'
        '      id: p.id, name: p.name, pos: p.pos,\n'
        '      ops: m ? m.ops : null,\n'
        '      avg: m ? m.avg : null,\n'
        '      hr:  m ? m.hr  : 0,\n'
        '      rbi: m ? m.rbi : 0,\n'
        '      pa:  m ? m.pa  : 0,\n'
        '    };',
        '    return {\n'
        '      id: p.id, name: p.name, pos: p.pos,\n'
        '      ops: m ? m.ops : null,\n'
        '      avg: m ? m.avg : null,\n'
        '      hr:  m ? m.hr  : 0,\n'
        '      rbi: m ? m.rbi : 0,\n'
        '      pa:  m ? m.pa  : 0,\n'
        '      so:  m ? m.so  : 0,\n'
        '    };',
        "3: propagate SO through _enrich",
    )

    # ---------- 4. Insert renderLineupEdge + helpers BEFORE _hrProbability ----------
    helpers = (
        '// =====================================================================\n'
        '// Lineup edge card — composite lineup score + per-batter K-vulnerability\n'
        '// list vs the opposing SP. Renders above the "projected lineup" cards in\n'
        '// the per-game expander. Composite sourced from player_aware_signal JSON\n'
        '// (lineup_wrcplus_h/a + lineup_xwoba_h/a). K-prob uses Log5:\n'
        '//   p_K = clamp((batter_K_rate * pitcher_K_rate) / league_K_rate)\n'
        '// where batter_K_rate = SO / PA, pitcher_K_rate ≈ k9 / 38, league ≈ 0.225.\n'
        '// =====================================================================\n'
        'const _LG_K_RATE = 0.225;  // league avg K% per PA, 2024-2026 era\n'
        'const _PA_PER_9  = 38.0;   // approx PAs per 9 innings\n'
        '\n'
        'function _batterKProb(batter, opposingSP) {\n'
        '  if (!batter || !opposingSP) return null;\n'
        '  const pa = parseInt(batter.pa || 0);\n'
        '  const so = parseInt(batter.so || 0);\n'
        '  if (pa < 30) return null;\n'
        '  const bK = so / pa;\n'
        '  const k9 = parseFloat(opposingSP.k9 || 0);\n'
        '  if (!isFinite(k9) || k9 <= 0) return null;\n'
        '  const pK = k9 / _PA_PER_9;\n'
        '  if (!isFinite(pK) || pK <= 0) return null;\n'
        '  let p = (bK * pK) / _LG_K_RATE;\n'
        '  if (!isFinite(p)) return null;\n'
        '  return Math.max(0.03, Math.min(0.6, p));\n'
        '}\n'
        '\n'
        'function _lineupCompositeFromPA(pa, side) {\n'
        '  // side = "h" or "a"\n'
        '  if (!pa) return null;\n'
        '  const wrc = pa["lineup_wrcplus_" + side];\n'
        '  const xw  = pa["lineup_xwoba_" + side];\n'
        '  if (wrc == null && xw == null) return null;\n'
        '  let score = 0;\n'
        '  const parts = [];\n'
        '  if (wrc != null) {\n'
        '    score += (wrc - 100);\n'
        '    parts.push(`wRC+ ${Math.round(wrc)}`);\n'
        '  }\n'
        '  if (xw != null) {\n'
        '    score += (xw - 0.310) * 1000;\n'
        '    parts.push(`xwOBA ${xw.toFixed(3)}`);\n'
        '  }\n'
        '  return { score, parts };\n'
        '}\n'
        '\n'
        'function _lookupTotalsForPreview(preview) {\n'
        '  if (!preview || !window.__totalsByMatchup) return null;\n'
        '  const a = preview.awayAbbr || "";\n'
        '  const h = preview.homeAbbr || "";\n'
        '  if (!a || !h) return null;\n'
        '  return window.__totalsByMatchup[`${a} @ ${h}`] || null;\n'
        '}\n'
        '\n'
        'function _parsePlayerAware(totalsRow) {\n'
        '  if (!totalsRow || !totalsRow.player_aware_signal) return null;\n'
        '  try { return JSON.parse(totalsRow.player_aware_signal); } catch (_) { return null; }\n'
        '}\n'
        '\n'
        'function renderLineupEdge(preview) {\n'
        '  if (!preview) return "";\n'
        '  const totalsRow = _lookupTotalsForPreview(preview);\n'
        '  const pa = _parsePlayerAware(totalsRow);\n'
        '  const awayComp = _lineupCompositeFromPA(pa, "a");\n'
        '  const homeComp = _lineupCompositeFromPA(pa, "h");\n'
        '\n'
        '  // --- Composite winner banner ---\n'
        '  let banner = "";\n'
        '  if (awayComp && homeComp) {\n'
        '    const diff = Math.abs(awayComp.score - homeComp.score);\n'
        '    const awayWins = awayComp.score >= homeComp.score;\n'
        '    const winName = awayWins ? preview.awayName : preview.homeName;\n'
        '    const winAbbr = awayWins ? preview.awayAbbr : preview.homeAbbr;\n'
        '    const edgeWord = diff < 5 ? "marginal edge" : diff < 12 ? "edge" : "clear edge";\n'
        '    banner = `<div style="background:rgba(63,185,80,0.08);border-left:3px solid var(--green);padding:0.55rem 0.85rem;margin-bottom:0.6rem;border-radius:4px;">`\n'
        '           + `<div style="font-weight:700;color:var(--green);font-size:0.88rem;letter-spacing:0.05em;text-transform:uppercase;">`\n'
        '           +   `${winAbbr || winName} ${edgeWord} (+${diff.toFixed(0)})`\n'
        '           + `</div>`\n'
        '           + `<div style="font-size:0.78rem;color:var(--muted);margin-top:0.3rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`\n'
        '           +   `${preview.awayAbbr || preview.awayName}: ${awayComp.parts.join(" · ")}<br>`\n'
        '           +   `${preview.homeAbbr || preview.homeName}: ${homeComp.parts.join(" · ")}`\n'
        '           + `</div></div>`;\n'
        '  } else if (awayComp || homeComp) {\n'
        '    const only = awayComp || homeComp;\n'
        '    const which = awayComp ? preview.awayAbbr : preview.homeAbbr;\n'
        '    banner = `<div class="muted" style="font-size:0.82rem;margin-bottom:0.5rem;">`\n'
        '           + `Partial composite (${which} only): ${only.parts.join(" · ")}`\n'
        '           + `</div>`;\n'
        '  } else {\n'
        '    banner = `<div class="muted" style="font-size:0.82rem;margin-bottom:0.5rem;">`\n'
        '           + `Composite lineup metrics not baked for this matchup (player_aware_signal missing).`\n'
        '           + `</div>`;\n'
        '  }\n'
        '\n'
        '  // --- Per-batter K-vulnerability lists ---\n'
        '  const kList = (lineup, opposingSP, teamName, teamAbbr) => {\n'
        '    if (!opposingSP) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: opposing SP not yet known.</div>`;\n'
        '    }\n'
        '    if (!lineup || !lineup.length) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: lineup card not yet posted.</div>`;\n'
        '    }\n'
        '    const rows = [];\n'
        '    for (const b of lineup) {\n'
        '      const pK = _batterKProb(b, opposingSP);\n'
        '      if (pK == null) continue;\n'
        '      rows.push({ name: b.name, pos: b.pos, pa: b.pa, prob: pK });\n'
        '    }\n'
        '    if (!rows.length) {\n'
        '      return `<div class="muted" style="font-size:0.82rem;">${teamName}: not enough batter PA for K-projection (need 30+ PA / hitter).</div>`;\n'
        '    }\n'
        '    rows.sort((a, b) => b.prob - a.prob);\n'
        '    const meanK = rows.reduce((s, r) => s + r.prob, 0) / rows.length;\n'
        '    const contactPct = (1 - meanK) * 100;\n'
        '    const k9Str = parseFloat(opposingSP.k9 || 0).toFixed(1);\n'
        '    let html = `<div>`\n'
        '             + `<div style="display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.25rem;">`\n'
        '             +   `<strong style="font-size:0.88rem;">${teamAbbr || teamName} lineup</strong>`\n'
        '             +   `<span class="muted" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:0.76rem;">`\n'
        '             +     `contact ${contactPct.toFixed(0)}% · vs ${opposingSP.name} (${k9Str} K/9)`\n'
        '             +   `</span>`\n'
        '             + `</div>`\n'
        '             + `<ol style="margin:0.2rem 0 0 1.4rem;padding:0;font-size:0.8rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`;\n'
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
        '       + `<h5>Lineup edge <span class="muted" style="font-size:0.78rem;font-weight:normal;">— composite advantage + per-batter K vs opposing SP</span></h5>`\n'
        '       + banner\n'
        '       + `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0.9rem;">`\n'
        '       +   kList(preview.awayLineup, preview.homePitcher, preview.awayName, preview.awayAbbr)\n'
        '       +   kList(preview.homeLineup, preview.awayPitcher, preview.homeName, preview.homeAbbr)\n'
        '       + `</div></div>`;\n'
        '}\n'
        '\n'
    )

    src = must_replace(
        src,
        '// Single-game HR probability for a batter facing today\'s opposing SP.\n',
        helpers + '// Single-game HR probability for a batter facing today\'s opposing SP.\n',
        "4: insert renderLineupEdge + helpers",
    )

    # ---------- 5. Wire renderLineupEdge into _formatGamePreviewUpcoming ----------
    # Insert the new card RIGHT BEFORE the away-team lineup card (which is the
    # 3rd preview-card in the grid). This puts "Lineup edge" above both team
    # lineup blocks, spanning full grid width.
    src = must_replace(
        src,
        '      <div class="preview-card">\n'
        '        <h5>${preview.awayName} top hitters (by 2026 OPS)</h5>',
        '      ${renderLineupEdge(preview)}\n'
        '      <div class="preview-card">\n'
        '        <h5>${preview.awayName} top hitters (by 2026 OPS)</h5>',
        "5: wire renderLineupEdge into preview grid",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
