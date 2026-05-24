#!/usr/bin/env python3
"""
_patch_quant_terminal.py
========================
4-phase "Quant Terminal" redesign for docs/index.html.

PHASES (all applied in one shot):
  1. Hide the bracketed date-strip; rely on existing <input type="date">.
  2. Drop the REASONS column from the slate table (expander row still
     surfaces full reasoning).
  3. Strip the bullpen outlook narrative paragraphs down to compact
     status / pitch-count / most-used-arm cards.
  4. Monospace body font + > prompt on Ask-the-Slate input.

Per locked memory:
  - feedback_edit_tool_pivot — str.replace only, no Edit tool.
  - feedback_bat_crlf — driver .bat must be CRLF (handled by caller).

Run from D:\\mlb_edge\\mlb_edge after curl-refreshing docs/index.html.
"""
from __future__ import annotations

import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "docs" / "index.html"


def must_replace(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence of anchor, found {n}")
        sys.exit(2)
    out = src.replace(old, new, 1)
    if out == src:
        print(f"[FAIL] {label}: replacement was a no-op")
        sys.exit(2)
    print(f"[ok]   {label}")
    return out


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    n0 = len(src)
    print(f"=== _patch_quant_terminal.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- Phase 1: hide bracketed date strip ----------
    src = must_replace(
        src,
        '<div class="date-strip" id="dateStrip">',
        '<div class="date-strip" id="dateStrip" style="display:none;">',
        "P1: hide .date-strip",
    )

    # ---------- Phase 2: drop REASONS column ----------
    # 2a. Remove <th>Reasons</th> from thead.
    src = must_replace(
        src,
        '        ${haveAnyResult ? "<th>Result</th>" : ""}\n'
        '        <th>Reasons</th>\n'
        '      </tr></thead><tbody>`;',
        '        ${haveAnyResult ? "<th>Result</th>" : ""}\n'
        '      </tr></thead><tbody>`;',
        "P2a: drop <th>Reasons</th>",
    )

    # 2b. Remove the <td class="why"> reasons cell from each row.
    src = must_replace(
        src,
        '      ${claudeCell}\n'
        '      ${resultCell}\n'
        '      <td class="why">${reasons || "<span class=\'muted\'>—</span>"}</td>\n'
        '    </tr>\n'
        '    <tr class="details-row" id="details-${i}">',
        '      ${claudeCell}\n'
        '      ${resultCell}\n'
        '    </tr>\n'
        '    <tr class="details-row" id="details-${i}">',
        "P2b: drop <td class=why> cell",
    )

    # 2c. Adjust colspan (was 14/13, now 13/12 since we dropped one column).
    src = must_replace(
        src,
        'const colspan = haveAnyResult ? 14 : 13;',
        'const colspan = haveAnyResult ? 13 : 12;',
        "P2c: colspan -1",
    )

    # ---------- Phase 3: compact bullpen cards ----------
    # Rewrite _bullpenTeamNarrative end-to-end. Anchored on the full
    # current function body so we don't accidentally clobber a future
    # rewrite that diverges.
    old_bp = (
        'function _bullpenTeamNarrative(teamBlock, teamLabel) {\n'
        '  if (!teamBlock) {\n'
        '    return `<span class="muted">${teamLabel}: no bullpen data available.</span>`;\n'
        '  }\n'
        '  if (teamBlock.unavailable) {\n'
        '    return `<span class="muted">${teamLabel}: ${teamBlock.reason || "no recent appearances"}.</span>`;\n'
        '  }\n'
        '  const s = teamBlock.team_summary || {};\n'
        '  const tier = s.ceiling_tier || "NORMAL";\n'
        '  const tierColor = _bullpenTierColor(tier);\n'
        '  const relievers = teamBlock.top_relievers || [];\n'
        '\n'
        '  // Compose a multi-sentence paragraph\n'
        '  let sentences = [];\n'
        '  sentences.push(\n'
        '    `<strong>${teamLabel}\'s bullpen is currently <span style="color:${tierColor};">${tier}</span></strong> ` +\n'
        '    `(${s.top3_pitch_total_72h || 0} pitches across the top-3 high-leverage arms in the last 72 hours, ` +\n'
        '    `${s.n_relievers_tracked || 0} relievers tracked).`\n'
        '  );\n'
        '\n'
        '  // Fatigue alarms\n'
        '  const alarms = [];\n'
        '  if (s.n_relievers_three_consecutive > 0) {\n'
        '    alarms.push(`<span class="flag-red">${s.n_relievers_three_consecutive} reliever(s) on three consecutive days — effectively unavailable tonight</span>`);\n'
        '  }\n'
        '  if (s.n_relievers_back_to_back > 0) {\n'
        '    alarms.push(`${s.n_relievers_back_to_back} reliever(s) on back-to-back days — usable but limited`);\n'
        '  }\n'
        '  if (alarms.length) {\n'
        '    sentences.push(`<em>${alarms.join("; ")}.</em>`);\n'
        '  }\n'
        '\n'
        '  // Average rest interpretation\n'
        '  if (typeof s.avg_rest_days === "number") {\n'
        '    if (s.avg_rest_days < 1.5) {\n'
        '      sentences.push(`Average rest across the top-${s.n_relievers_tracked} sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — short. Late-leverage choices are constrained.`);\n'
        '    } else if (s.avg_rest_days >= 3.0) {\n'
        '      sentences.push(`Average rest sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — fresh. Manager has full menu of late-leverage options.`);\n'
        '    } else {\n'
        '      sentences.push(`Average rest sits at <strong>${s.avg_rest_days.toFixed(1)} days</strong> — workable but not deep.`);\n'
        '    }\n'
        '  }\n'
        '\n'
        '  // Top reliever summary line\n'
        '  if (relievers.length) {\n'
        '    const top = relievers[0];\n'
        '    const topName = top.name || `pitcher #${top.pitcher_id}`;\n'
        '    sentences.push(\n'
        '      `Most-used arm: <strong>${topName}</strong> ` +\n'
        '      `(${top.pitches_72h}p in 72h, last LI avg ${top.avg_leverage_last_3 || 0}) ` +\n'
        '      _bullpenFlagBadge(top.fatigue_flag) + `.`\n'
        '    );\n'
        '  }\n'
        '\n'
        '  return sentences.join(" ");\n'
        '}\n'
    )
    new_bp = (
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
        '}\n'
    )
    src = must_replace(src, old_bp, new_bp, "P3: compact _bullpenTeamNarrative")

    # ---------- Phase 4: monospace body + > prompt search ----------
    # 4a. Body font → monospace stack ("Quant terminal" identity).
    src = must_replace(
        src,
        '  body {\n'
        '    margin: 0; padding: 0;\n'
        '    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;\n'
        '    background: var(--bg); color: var(--text);\n'
        '    line-height: 1.5;\n'
        '  }',
        '  body {\n'
        '    margin: 0; padding: 0;\n'
        '    font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;\n'
        '    background: var(--bg); color: var(--text);\n'
        '    line-height: 1.5;\n'
        '    font-variant-ligatures: none;\n'
        '  }',
        "P4a: body font-family → monospace",
    )

    # 4b. Ask-the-Slate input → borderless with leading `>` prompt.
    src = must_replace(
        src,
        '    <div style="display:flex;gap:0.5rem;margin-bottom:0.75rem;">\n'
        '      <input type="text" id="queryInput"\n'
        '             placeholder="Ask a question about the loaded slate…"\n'
        '             style="flex:1;background:var(--bg);color:var(--text);\n'
        '                    border:1px solid var(--border);border-radius:4px;\n'
        '                    padding:0.5rem 0.75rem;font-size:0.95rem;" />\n'
        '      <button id="askBtn">Ask</button>\n'
        '    </div>',
        '    <div style="display:flex;gap:0.5rem;margin-bottom:0.75rem;align-items:center;border-bottom:1px solid var(--accent);padding:0.25rem 0;">\n'
        '      <span style="color:var(--accent);font-weight:700;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:1.05rem;padding-left:0.4rem;">&gt;</span>\n'
        '      <input type="text" id="queryInput"\n'
        '             placeholder="ask the slate..."\n'
        '             style="flex:1;background:transparent;color:var(--text);\n'
        '                    border:none;outline:none;\n'
        '                    padding:0.45rem 0.25rem;font-size:0.95rem;\n'
        '                    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;" />\n'
        '      <button id="askBtn">Ask</button>\n'
        '    </div>',
        "P4b: > prompt on Ask-the-Slate",
    )

    # ---------- write ----------
    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
