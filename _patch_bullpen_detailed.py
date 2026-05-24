#!/usr/bin/env python3
"""
_patch_bullpen_detailed.py
==========================
Pump up the bullpen surfaces to Probable-Starters level of detail.

Surface A: top-level Bullpen Outlook card (`_bullpenTeamNarrative`).
  Currently compact (status tag + pitch count + top arm). Replaced with a
  detailed panel that includes:
    - Status badge + team-summary metrics
    - Multi-sentence narrative prose (state interpretation, fatigue alarms,
      rest interpretation, most-used arm callout)
    - The per-reliever fatigue table (rest / consec / P-72h / LI / flag),
      pulled inline so it lives next to the prose instead of in a separate
      section.

Surface B: in-expander bullpen panel (`fmtBullpen` inside
  `_formatGamePreviewUpcoming`).
  Currently a 3-row li list with role label + ERA / WHIP / SV / HLD / IP
  + a static role blurb. Enhanced to include:
    - K/9 stat in the stats line (was missing)
    - Per-reliever impact narrative below each row, mirroring the
      `_pitcherImpact` style used for SPs: role context, K-rate read
      (punch-out arm / contact-prone), ERA read (elite / fringe), and
      fatigue context from bullpen_meta when available.

New helpers:
  - `_bullpenTeamProse(teamSummary, relievers)` — multi-sentence prose
    summary for the team-level bullpen state.
  - `_bullpenFatigueRowByName(teamBlock)` — name -> row index over
    `top_relievers` so the in-expander panel can attach fatigue context
    to the matching roster entry.
  - `_relImpactNarrative(rel, fatigueRow, roleLabel, oppName)` —
    per-reliever narrative, _pitcherImpact-style.

This deliberately overrides the "compact bullpen cards" line from
feedback_quant_terminal_identity. The memory will be updated separately
to reflect the new direction (user requested 2026-05-24).

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
    print("=== _patch_bullpen_detailed.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. Replace _bullpenTeamNarrative with detailed panel ----------
    new_narrative = (
        'function _bullpenTeamProse(s, relievers) {\n'
        '  // Multi-sentence prose summary built from team_summary metrics.\n'
        '  // Mirrors the structure of _pitcherImpact: state -> alarms ->\n'
        '  // rest -> top arm.\n'
        '  const parts = [];\n'
        '  if (s.n_relievers_three_consecutive > 0) {\n'
        '    const n = s.n_relievers_three_consecutive;\n'
        '    parts.push(`<strong class="flag-red">${n} reliever${n>1?"s":""} on three consecutive days</strong> — effectively unavailable tonight; manager works with a shortened menu.`);\n'
        '  } else if (s.n_relievers_back_to_back > 0) {\n'
        '    const n = s.n_relievers_back_to_back;\n'
        '    parts.push(`<span style="color:var(--yellow);">${n} reliever${n>1?"s":""} on back-to-back days</span> — usable but managed, likely capped at a single inning.`);\n'
        '  }\n'
        '  if (typeof s.avg_rest_days === "number") {\n'
        '    if (s.avg_rest_days < 1.5) {\n'
        '      parts.push(`Average rest across the top-${s.n_relievers_tracked} sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — short. Late-leverage choices are constrained.`);\n'
        '    } else if (s.avg_rest_days >= 3.0) {\n'
        '      parts.push(`Average rest sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — fresh. Manager has the full menu of late-leverage options.`);\n'
        '    } else {\n'
        '      parts.push(`Average rest sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — workable but not deep.`);\n'
        '    }\n'
        '  }\n'
        '  if (relievers && relievers.length) {\n'
        '    const top = relievers[0];\n'
        '    const topName = top.name || `pitcher #${top.pitcher_id}`;\n'
        '    parts.push(\n'
        '      `Most-used arm: <strong>${topName}</strong> ` +\n'
        '      `(${top.pitches_72h}p in 72h, last LI avg ${(top.avg_leverage_last_3||0).toFixed(2)}) ` +\n'
        '      _bullpenFlagBadge(top.fatigue_flag) + "."\n'
        '    );\n'
        '  }\n'
        '  return parts.join(" ");\n'
        '}\n'
        '\n'
        'function _bullpenTeamNarrative(teamBlock, teamLabel) {\n'
        '  // Detailed panel (2026-05-24): status badge + team-summary metrics\n'
        '  // + multi-sentence prose + per-reliever fatigue table inline.\n'
        '  // Replaces the prior compact one-liner; user asked for Probable-\n'
        '  // Starters-level depth on this surface.\n'
        '  if (!teamBlock) {\n'
        '    return `<div class="muted" style="font-size:0.82rem;">no bullpen data</div>`;\n'
        '  }\n'
        '  if (teamBlock.unavailable) {\n'
        '    return `<div class="muted" style="font-size:0.82rem;">${teamBlock.reason || "no recent appearances"}</div>`;\n'
        '  }\n'
        '  const s = teamBlock.team_summary || {};\n'
        '  const tier = s.ceiling_tier || "NORMAL";\n'
        '  const tierColor = _bullpenTierColor(tier);\n'
        '  const relievers = teamBlock.top_relievers || [];\n'
        '  const pitchCount = s.top3_pitch_total_72h || 0;\n'
        '  const restStr = (typeof s.avg_rest_days === "number")\n'
        '    ? `${s.avg_rest_days.toFixed(1)}d`\n'
        '    : "n/a";\n'
        '\n'
        '  // Header: status badge + key metrics in a single mono line\n'
        '  let html = `<div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.35rem;">`\n'
        '           +   `<span style="display:inline-block;padding:0.1rem 0.45rem;border-radius:3px;border:1px solid ${tierColor};color:${tierColor};font-weight:700;font-size:0.74rem;letter-spacing:0.06em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">${tier}</span>`\n'
        '           +   `<span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:0.78rem;color:var(--text);">${pitchCount}p / 72h · ${s.n_relievers_tracked || 0} tracked · avg rest ${restStr}</span>`\n'
        '           + `</div>`;\n'
        '\n'
        '  // Multi-sentence narrative paragraph\n'
        '  const prose = _bullpenTeamProse(s, relievers);\n'
        '  if (prose) {\n'
        '    html += `<div style="font-size:0.83rem;color:var(--text);margin-bottom:0.4rem;line-height:1.5;">${prose}</div>`;\n'
        '  }\n'
        '\n'
        '  // Per-reliever fatigue table inline (was previously hidden behind a\n'
        '  // separate panel that only the expander showed)\n'
        '  html += _bullpenFatigueTable(teamBlock, teamLabel);\n'
        '  return html;\n'
        '}\n'
    )

    src = must_replace(
        src,
        'function _bullpenTeamNarrative(teamBlock, teamLabel) {\n'
        '  // Quant-terminal compact card: status tag + pitch count + top arm.\n'
        '  // Walls of prose moved to the expandable game row; this stays scannable.\n'
        '  if (!teamBlock) {\n'
        '    return `<div class="muted" style="font-size:0.82rem;">no bullpen data</div>`;\n'
        '  }\n'
        '  if (teamBlock.unavailable) {\n'
        '    return `<div class="muted" style="font-size:0.82rem;">${teamBlock.reason || "no recent appearances"}</div>`;\n'
        '  }\n'
        '  const s = teamBlock.team_summary || {};\n'
        '  const tier = s.ceiling_tier || "NORMAL";\n'
        '  const tierColor = _bullpenTierColor(tier);\n'
        '  const relievers = teamBlock.top_relievers || [];\n'
        '  const pitchCount = s.top3_pitch_total_72h || 0;\n'
        '  const top = relievers[0];\n'
        '  let topLine = "";\n'
        '  if (top) {\n'
        '    const topName = top.name || `#${top.pitcher_id}`;\n'
        '    topLine = `<div style="font-size:0.78rem;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`\n'
        '            + `Top: <span style="color:var(--text);">${topName}</span> (${top.pitches_72h}p) `\n'
        '            + _bullpenFlagBadge(top.fatigue_flag)\n'
        '            + `</div>`;\n'
        '  }\n'
        '  const alarms = [];\n'
        '  if (s.n_relievers_three_consecutive > 0) {\n'
        '    alarms.push(`<span class="flag-red" style="font-size:0.74rem;">${s.n_relievers_three_consecutive} on 3-day · UNAVAILABLE</span>`);\n'
        '  } else if (s.n_relievers_back_to_back > 0) {\n'
        '    alarms.push(`<span style="color:var(--yellow);font-size:0.74rem;">${s.n_relievers_back_to_back} on B2B</span>`);\n'
        '  }\n'
        '  const alarmLine = alarms.length\n'
        '    ? `<div style="margin-top:0.15rem;">${alarms.join(" · ")}</div>`\n'
        '    : "";\n'
        '  return `<div style="display:flex;flex-direction:column;gap:0.2rem;">`\n'
        '       + `<div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">`\n'
        '       +   `<span style="display:inline-block;padding:0.1rem 0.45rem;border-radius:3px;border:1px solid ${tierColor};color:${tierColor};font-weight:700;font-size:0.74rem;letter-spacing:0.06em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">${tier}</span>`\n'
        '       +   `<span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:0.82rem;color:var(--text);">${pitchCount}p / 72h</span>`\n'
        '       + `</div>`\n'
        '       + topLine\n'
        '       + alarmLine\n'
        '       + `</div>`;\n'
        '}\n',
        new_narrative,
        "1: replace _bullpenTeamNarrative with detailed panel",
    )

    # ---------- 2. Enhance in-expander fmtBullpen ----------
    # Add K/9 stat + per-reliever impact narrative. Also hook in the fatigue
    # row by name so the narrative can surface FRESH/B2B/OVERWORKED context
    # from the bullpen_meta sidecar.
    new_fmt_bullpen = (
        '  // Build a lookup of fatigue rows keyed by reliever name so the\n'
        '  // per-arm narrative can pull in the FRESH/B2B/OVERWORKED context\n'
        '  // alongside the roster ERA/WHIP/K9 line.\n'
        '  const _bpFatigueLookup = (function() {\n'
        '    try {\n'
        '      const _m = `${preview.awayAbbr} @ ${preview.homeAbbr}`;\n'
        '      const _bp = _bullpenMetaForMatchup(_m);\n'
        '      const out = { away: {}, home: {} };\n'
        '      const _idx = (block) => {\n'
        '        const o = {};\n'
        '        if (block && Array.isArray(block.top_relievers)) {\n'
        '          for (const r of block.top_relievers) {\n'
        '            if (r && r.name) o[r.name.toLowerCase()] = r;\n'
        '          }\n'
        '        }\n'
        '        return o;\n'
        '      };\n'
        '      if (_bp) {\n'
        '        out.away = _idx(_bp.away);\n'
        '        out.home = _idx(_bp.home);\n'
        '      }\n'
        '      return out;\n'
        '    } catch (_) { return { away: {}, home: {} }; }\n'
        '  })();\n'
        '\n'
        '  function _relImpactNarrative(rel, fatigueRow, roleLabel, oppName) {\n'
        '    const lines = [];\n'
        '    const role = String(roleLabel || "").toLowerCase();\n'
        '    if (role.includes("closer")) lines.push("owns the 9th when his team leads — the inning that decides a one-run line");\n'
        '    else if (role.includes("setup")) lines.push("7th–8th-inning bridge to the closer; a clean lead lands here first");\n'
        '    else lines.push("middle-leverage / matchup work, often used on the platoon edge");\n'
        '    const k9 = parseFloat(rel.k9 || 0);\n'
        '    if (isFinite(k9) && k9 >= 10.5) lines.push(`<strong>punch-out arm</strong> (${k9.toFixed(1)} K/9) — neutralizes ${oppName || "the opposing lineup"}\'s contact threats`);\n'
        '    else if (isFinite(k9) && k9 < 7.5) lines.push(`contact-prone (${k9.toFixed(1)} K/9) — relies on weak contact and quick outs`);\n'
        '    const era = parseFloat(rel.era || 0);\n'
        '    if (isFinite(era) && era > 0 && era < 2.5) lines.push(`elite ratio (${era.toFixed(2)} ERA)`);\n'
        '    else if (isFinite(era) && era > 4.5) lines.push(`fringe results lately (${era.toFixed(2)} ERA)`);\n'
        '    if (fatigueRow) {\n'
        '      const flag = String(fatigueRow.fatigue_flag || "").toUpperCase();\n'
        '      if (flag === "B2B2B" || flag === "OVERWORKED") {\n'
        '        lines.push(`<span class="flag-red">on ${fatigueRow.consecutive_days || 3}-day, effectively unavailable tonight</span>`);\n'
        '      } else if (flag === "B2B") {\n'
        '        lines.push(`<span style="color:var(--yellow);">on B2B (${fatigueRow.pitches_72h || 0}p / 72h)</span> — usable but managed`);\n'
        '      } else if (typeof fatigueRow.rest_days === "number" && fatigueRow.rest_days >= 3) {\n'
        '        lines.push(`well-rested (${fatigueRow.rest_days}d off)`);\n'
        '      }\n'
        '    }\n'
        '    return lines.join(". ") + ".";\n'
        '  }\n'
        '\n'
        '  const fmtBullpen = (rels, side, oppName) => {\n'
        '    if (!rels || !rels.length) return `<li class="muted">Roster fetch returned no qualified relievers.</li>`;\n'
        '    const labels = ["Closer (proxy)", "Setup", "Third lever"];\n'
        '    const fatigueIdx = (side === "away") ? _bpFatigueLookup.away : _bpFatigueLookup.home;\n'
        '    let html = "";\n'
        '    for (let i = 0; i < Math.min(rels.length, 3); i++) {\n'
        '      const r = rels[i];\n'
        '      const fat = fatigueIdx[(r.name || "").toLowerCase()] || null;\n'
        '      const impact = _relImpactNarrative(r, fat, labels[i], oppName);\n'
        '      html += `<li style="margin-bottom:0.5rem;">`\n'
        '           +   `<div><strong>${r.name}</strong> <span class="muted">(${labels[i]})</span> — ${r.era} ERA, ${r.whip} WHIP, ${r.sv} SV, ${r.hld} HLD, ${r.ip} IP, ${r.k9 || "-"} K/9</div>`\n'
        '           +   `<div style="font-size:0.83rem;color:var(--muted);margin-top:0.2rem;line-height:1.45;">${impact}</div>`\n'
        '           + `</li>`;\n'
        '    }\n'
        '    return html;\n'
        '  };\n'
        '\n'
    )

    # Replace just the fmtBullpen block (plus insert the helper above it)
    src = must_replace(
        src,
        '  const fmtBullpen = (rels) => {\n'
        '    if (!rels || !rels.length) return `<li class="muted">Roster fetch returned no qualified relievers.</li>`;\n'
        '    const labels = ["Closer (proxy)", "Setup", "Third lever"];\n'
        '    const blurbs = [\n'
        '      "Owns the 9th when his team leads; if the line is one-run, he is the inning that decides the game.",\n'
        '      "The 7th–8th-inning bridge; getting a clean lead to the closer starts here.",\n'
        '      "Used for high-leverage middle work or matchup work."\n'
        '    ];\n'
        '    let html = "";\n'
        '    for (let i = 0; i < Math.min(rels.length, 3); i++) {\n'
        '      const r = rels[i];\n'
        '      html += `<li><strong>${r.name}</strong> (${labels[i]}) — ${r.era} ERA, ${r.whip} WHIP, ${r.sv} SV, ${r.hld} HLD, ${r.ip} IP. ${blurbs[i]}</li>`;\n'
        '    }\n'
        '    return html;\n'
        '  };\n'
        '\n',
        new_fmt_bullpen,
        "2: enhance fmtBullpen with K/9 + per-reliever narrative",
    )

    # ---------- 3. Update fmtBullpen call sites to pass side + oppName ----------
    # Call sites for the bullpen section in _formatGamePreviewUpcoming.
    src = must_replace(
        src,
        '        <h5>${preview.awayName} bullpen — three highest-leverage arms</h5>\n'
        '        <ul>${fmtBullpen(preview.awayBullpen)}</ul>\n'
        '        <h5>${preview.homeName} bullpen — three highest-leverage arms</h5>\n'
        '        <ul>${fmtBullpen(preview.homeBullpen)}</ul>',
        '        <h5>${preview.awayName} bullpen — three highest-leverage arms</h5>\n'
        '        <ul>${fmtBullpen(preview.awayBullpen, "away", preview.homeName)}</ul>\n'
        '        <h5>${preview.homeName} bullpen — three highest-leverage arms</h5>\n'
        '        <ul>${fmtBullpen(preview.homeBullpen, "home", preview.awayName)}</ul>',
        "3: pass side + oppName to fmtBullpen call sites",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
