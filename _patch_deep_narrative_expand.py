#!/usr/bin/env python3
"""
_patch_deep_narrative_expand.py
===============================
Two changes per user ask 2026-05-24:

1. Build out the Cloudflare Pages Functions for AI-augmented narrative
   (handled in functions/api/claude/ — separate files, NOT this script).

2. Expand the deterministic deep-analysis content (this script):
   - Inject CSS for collapsible deep-sections + dotted-underline tooltips.
   - Add `_deepSection(title, body, openByDefault)` + `_dt(value, source)`
     helpers that all three narrative builders use.
   - Restructure _deepNarrativeML / _deepNarrativeOU / _deepNarrativeK
     into named collapsible subsections with two NEW sections each:
       * Counter-signals — what argues AGAINST the pick
       * What would change my mind — tripwires that flip the call
   - Add an "Ask Claude about this" inline button INSIDE the deep panel
     that pre-fills the Ask-the-Slate input with a question scoped to
     this specific prop.
   - Add a click handler that collapses/expands sections.

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
    print("=== _patch_deep_narrative_expand.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. CSS additions for collapsible deep-sections + tooltips ----------
    css_addition = (
        '  /* Deep-analysis collapsible sub-sections (2026-05-24) */\n'
        '  .deep-section { margin: 0.6rem 0 0.4rem 0; }\n'
        '  .deep-section > .deep-h5 {\n'
        '    cursor: pointer; user-select: none;\n'
        '    display: flex; align-items: center; gap: 0.4rem;\n'
        '    font-size: 0.82rem; font-weight: 700;\n'
        '    color: var(--accent); text-transform: uppercase;\n'
        '    letter-spacing: 0.05em; margin: 0 0 0.3rem 0;\n'
        '    padding: 0.2rem 0; border-bottom: 1px solid rgba(255,255,255,0.06);\n'
        '  }\n'
        '  .deep-section > .deep-h5 .chev {\n'
        '    display: inline-block; width: 0.9em; color: var(--muted);\n'
        '    transition: transform 0.15s;\n'
        '  }\n'
        '  .deep-section.open > .deep-h5 .chev { transform: rotate(90deg); color: var(--accent); }\n'
        '  .deep-section > .deep-body { display: none; font-size: 0.88rem; line-height: 1.55; }\n'
        '  .deep-section.open > .deep-body { display: block; }\n'
        '  .deep-section.subtle > .deep-h5 { color: var(--muted); }\n'
        '  .deep-section.subtle.open > .deep-h5 { color: var(--text); }\n'
        '  .deep-section .counter-flag {\n'
        '    color: var(--yellow); font-weight: 600;\n'
        '  }\n'
        '  .deep-section .pivot-flag {\n'
        '    color: var(--accent); font-weight: 600;\n'
        '  }\n'
        '  /* Stat tooltip: dotted underline + cursor:help, title attr shows source */\n'
        '  .dt {\n'
        '    border-bottom: 1px dotted var(--muted);\n'
        '    cursor: help;\n'
        '  }\n'
        '  /* Inline "Ask Claude about this" button */\n'
        '  .deep-ask-claude {\n'
        '    background: rgba(88,166,255,0.08); border: 1px solid var(--accent);\n'
        '    color: var(--accent); padding: 0.25rem 0.7rem; border-radius: 4px;\n'
        '    font-size: 0.78rem; font-family: ui-monospace,SFMono-Regular,Menlo,monospace;\n'
        '    cursor: pointer; margin-top: 0.6rem;\n'
        '  }\n'
        '  .deep-ask-claude:hover { background: rgba(88,166,255,0.16); }\n'
        '\n'
    )

    src = must_replace(
        src,
        '  /* ----- Post-game result indicators ----- */\n',
        css_addition + '  /* ----- Post-game result indicators ----- */\n',
        "1: inject deep-section CSS",
    )

    # ---------- 2. Helpers right BEFORE _deepNarrativeML ----------
    helpers = (
        '// Deep-analysis section helper: renders a collapsible <section> with a\n'
        '// chevron header and a hidden-by-default body. Click toggles via the\n'
        '// event-delegation listener at the bottom of the file.\n'
        'function _deepSection(title, bodyHtml, openByDefault) {\n'
        '  if (!bodyHtml || !bodyHtml.trim()) return "";\n'
        '  const cls = openByDefault ? "deep-section open" : "deep-section";\n'
        '  return `<section class="${cls}" data-deep-section>`\n'
        '       +   `<div class="deep-h5"><span class="chev">▸</span><span>${title}</span></div>`\n'
        '       +   `<div class="deep-body">${bodyHtml}</div>`\n'
        '       + `</section>`;\n'
        '}\n'
        '\n'
        '// Stat tooltip — wraps a value with dotted underline + cursor:help and a\n'
        '// title= attribute that explains where the value came from. Hover to\n'
        '// see source attribution.\n'
        'function _dt(value, source) {\n'
        '  const safeSrc = String(source || "").replace(/"/g, "&quot;");\n'
        '  return `<span class="dt" title="${safeSrc}">${value}</span>`;\n'
        '}\n'
        '\n'
        '// Build the counter-signals section for an ML pick. Looks at the same\n'
        '// signals the lead narrative uses, but flips perspective to surface what\n'
        '// argues AGAINST the model\'s call. Returns "" if there is nothing\n'
        '// non-trivial to surface.\n'
        'function _deepCounterSignalsML(r, item) {\n'
        '  const out = [];\n'
        '  const f5 = parseFloat(r.f5_prob);\n'
        '  const full = parseFloat(r.full_prob);\n'
        '  if (isFinite(f5) && isFinite(full) && Math.abs(f5 - full) > 0.10) {\n'
        '    out.push(`Stages 1 and 2 disagree by <strong>${(Math.abs(f5-full)*100).toFixed(1)}pp</strong> — bullpen-driven model output is more fragile.`);\n'
        '  }\n'
        '  const fair = parseFloat(r.fair_prob);\n'
        '  if (isFinite(fair) && isFinite(item.prob) && (item.prob - fair) > 0.18) {\n'
        '    out.push(`Model-vs-market gap of <strong>${((item.prob-fair)*100).toFixed(1)}pp</strong> is in the top decile — markets rarely miss by this much; favor caution.`);\n'
        '  }\n'
        '  if (isFinite(item.prob) && item.prob > 0.80) {\n'
        '    out.push(`Model prob <strong>${(item.prob*100).toFixed(1)}%</strong> is past the 80% saturation point (HARD CAP 9 territory) — historically a regression zone.`);\n'
        '  }\n'
        '  const stress = (r.stress_warnings || "").trim();\n'
        '  if (stress) out.push(`Stress audit fired: <span class="muted">${stress}</span>.`);\n'
        '  const reasons = (r.grade_reasons || "").toLowerCase();\n'
        '  if (reasons.includes("small_sample") || reasons.includes("small sample")) {\n'
        '    out.push(`<strong>Small-sample cap fired</strong> — one or more signals based on &lt; threshold PA / IP / appearances.`);\n'
        '  }\n'
        '  if (reasons.includes("compound")) {\n'
        '    out.push(`<strong>Compound-cap fired</strong> — multiple weak signals stacked; treat as down-weighted.`);\n'
        '  }\n'
        '  if (!out.length) {\n'
        '    out.push(`<span class="muted">No material counter-signals — direction is clean across the layers.</span>`);\n'
        '  }\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        '// "What would change my mind" — tripwires that, if observed, would\n'
        '// materially shift the model\'s call. Quantitative where possible.\n'
        'function _deepPivotPointsML(r, item) {\n'
        '  const out = [];\n'
        '  if (isFinite(item.prob) && isFinite(item.edge_pp)) {\n'
        '    const breakeven = parseFloat(r.fair_prob);\n'
        '    if (isFinite(breakeven)) {\n'
        '      const tipDelta = (item.prob - breakeven) * 100;\n'
        '      out.push(`Market moves <strong class="pivot-flag">${tipDelta.toFixed(1)}pp toward the pick</strong> — edge collapses to zero; downgrade automatically.`);\n'
        '    }\n'
        '  }\n'
        '  const hlBp = parseFloat(r.hl_bullpen_xwoba_gap);\n'
        '  if (isFinite(hlBp)) {\n'
        '    out.push(`Bullpen xwOBA gap (currently <strong>${hlBp.toFixed(4)}</strong>) flips sign — re-evaluate; bullpen edge was a contributing layer.`);\n'
        '  }\n'
        '  const pqi = parseFloat(r.pqi_diff);\n'
        '  if (isFinite(pqi)) {\n'
        '    out.push(`PQI Δ (currently <strong>${pqi.toFixed(1)}</strong>) inverts — pitching-quality contribution flips against the pick.`);\n'
        '  }\n'
        '  out.push(`Late-scratch on the pick\'s SP — re-grade with PENDING_SP_DATA fallback.`);\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        '// Counter-signals for O/U: what argues against the OVER/UNDER side.\n'
        'function _deepCounterSignalsOU(r, item) {\n'
        '  const out = [];\n'
        '  const edge = Math.abs(item.edge_pp || 0);\n'
        '  if (edge < 2) {\n'
        '    out.push(`Edge <strong>${edge.toFixed(1)}pp</strong> sits inside typical model-to-market noise (~±2pp). Could be variance, not signal.`);\n'
        '  }\n'
        '  const homeK = parseFloat(r.home_sp_k_pct);\n'
        '  const awayK = parseFloat(r.away_sp_k_pct);\n'
        '  if (isFinite(homeK) && isFinite(awayK)) {\n'
        '    const combo = (homeK + awayK) / 2;\n'
        '    if (item.side === "OVER" && combo > 26) {\n'
        '      out.push(`OVER call but combined SP K% is <strong>${combo.toFixed(1)}%</strong> (high) — strikeouts suppress runs, fights the over.`);\n'
        '    } else if (item.side === "UNDER" && combo < 19) {\n'
        '      out.push(`UNDER call but combined SP K% is <strong>${combo.toFixed(1)}%</strong> (low) — more contact = more BIP = more runs, fights the under.`);\n'
        '    }\n'
        '  }\n'
        '  const umpK = parseFloat(r.ump_k_pct_delta);\n'
        '  if (isFinite(umpK)) {\n'
        '    if (item.side === "OVER" && umpK > 0.015) {\n'
        '      out.push(`OVER call but plate umpire is pitcher-friendly (K Δ <strong>+${umpK.toFixed(3)}</strong>).`);\n'
        '    } else if (item.side === "UNDER" && umpK < -0.015) {\n'
        '      out.push(`UNDER call but plate umpire is hitter-friendly (K Δ <strong>${umpK.toFixed(3)}</strong>).`);\n'
        '    }\n'
        '  }\n'
        '  if (!out.length) {\n'
        '    out.push(`<span class="muted">No material counter-signals on this side.</span>`);\n'
        '  }\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        'function _deepPivotPointsOU(r, item) {\n'
        '  const out = [];\n'
        '  if (isFinite(item.pred_runs) && isFinite(item.line)) {\n'
        '    const diff = item.pred_runs - item.line;\n'
        '    out.push(`Market moves the line <strong class="pivot-flag">${diff.toFixed(1)} runs</strong> toward the model\'s number — edge collapses; no play.`);\n'
        '  }\n'
        '  out.push(`Wind / weather shifts materially (Statcast doesn\'t bake same-day wind) — re-check the venue before locking in.`);\n'
        '  out.push(`Late-scratch on either SP — re-evaluate; SP K% drives a meaningful chunk of the total.`);\n'
        '  out.push(`Lineup posted differs materially from projected (rest day for a 1.000-OPS bat, e.g.) — wRC+ aggregation shifts.`);\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        'function _deepCounterSignalsK(r, item) {\n'
        '  const out = [];\n'
        '  const kPct = item.k_pct;\n'
        '  const expK = item.expected_K;\n'
        '  const umpK = parseFloat(r.ump_k_pct_delta);\n'
        '  if (kPct < 23 && expK > 6.0) {\n'
        '    out.push(`Low K% (<strong>${kPct.toFixed(1)}%</strong>) but expected K still <strong>${expK.toFixed(1)}</strong> — leans on PAs, not K-rate. Fragile to early hook.`);\n'
        '  }\n'
        '  if (isFinite(umpK) && umpK < -0.015) {\n'
        '    out.push(`Plate umpire suppresses K rate (Δ <strong>${umpK.toFixed(3)}</strong>) — knock <strong>${(Math.abs(umpK)*26).toFixed(1)} K</strong> off the naive projection.`);\n'
        '  }\n'
        '  if (item.p_over_7 !== null && item.p_over_7 < 0.20) {\n'
        '    out.push(`P(K ≥ 7) only <strong>${(item.p_over_7*100).toFixed(0)}%</strong> — ceiling props are out of reach unless variance breaks.`);\n'
        '  }\n'
        '  if (!out.length) {\n'
        '    out.push(`<span class="muted">No material counter-signals on this K projection.</span>`);\n'
        '  }\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        'function _deepPivotPointsK(r, item) {\n'
        '  const out = [];\n'
        '  out.push(`Pitcher pulled before 5 IP (early hook on contact outing) — expected K drops to <strong>~${(item.expected_K*0.75).toFixed(1)}</strong> on a 5-inning workload.`);\n'
        '  out.push(`Opposing lineup posts an all-righties / all-lefties day that exploits the pitcher\'s platoon split — K% can move ±3pp.`);\n'
        '  const umpK = parseFloat(r.ump_k_pct_delta);\n'
        '  if (isFinite(umpK)) {\n'
        '    out.push(`If the announced umpire changes, swap K Δ — current <strong>${umpK.toFixed(3)}</strong>; league avg ~0.000.`);\n'
        '  }\n'
        '  return `<ul>${out.map(s => `<li>${s}</li>`).join("")}</ul>`;\n'
        '}\n'
        '\n'
        '// Inline "Ask Claude about this" button HTML. Wires the click via\n'
        '// data-deep-ask-claude attribute so the event-delegation handler can\n'
        '// pre-fill the Ask-the-Slate textarea and scroll to it.\n'
        'function _deepAskClaudeButton(propType, payload) {\n'
        '  let q = "";\n'
        '  if (propType === "ml") {\n'
        '    q = `Why does the model favor ${payload.pick} ML in ${payload.matchup}? What\'s the single weakest link in that thesis?`;\n'
        '  } else if (propType === "ou") {\n'
        '    q = `Why does the model lean ${payload.side} ${payload.line ? payload.line.toFixed(1) : ""} on ${payload.matchup}? Where could it be wrong?`;\n'
        '  } else if (propType === "k") {\n'
        '    q = `Why does the model project ${payload.expected_K ? payload.expected_K.toFixed(1) : "?"} K for ${payload.name} in ${payload.matchup}? Which threshold prop has the best edge?`;\n'
        '  }\n'
        '  const safe = q.replace(/"/g, "&quot;").replace(/</g, "&lt;");\n'
        '  return `<button class="deep-ask-claude" data-deep-ask-claude data-question="${safe}">Ask Claude about this →</button>`;\n'
        '}\n'
        '\n'
    )

    src = must_replace(
        src,
        '// === Game ML deep narrative ===\nfunction _deepNarrativeML(item) {\n',
        helpers
        + '// === Game ML deep narrative ===\nfunction _deepNarrativeML(item) {\n',
        "2: insert deep-section helpers + counter-signal/pivot builders",
    )

    # ---------- 3. Replace _deepNarrativeML body ----------
    new_ml = (
        'function _deepNarrativeML(item) {\n'
        '  const r = item._row || {};\n'
        '  const pick = item.pick;\n'
        '  const matchup = item.matchup;\n'
        '  const prob = item.prob;\n'
        '  const edgePp = item.edge_pp;\n'
        '  const grade = item.grade || "—";\n'
        '  const f5 = parseFloat(r.f5_prob);\n'
        '  const full = parseFloat(r.full_prob);\n'
        '  const fair = parseFloat(r.fair_prob);\n'
        '  const homeSp = (r.home_sp_name || "").trim();\n'
        '  const awaySp = (r.away_sp_name || "").trim();\n'
        '  const sigList = _parseSignalsList(r.signals);\n'
        '  const reasons = _capRulesFromReasons(r.grade_reasons || "");\n'
        '  const stress = (r.stress_warnings || "").trim();\n'
        '  const hlBp = parseFloat(r.hl_bullpen_xwoba_gap);\n'
        '  const pqi = parseFloat(r.pqi_diff);\n'
        '  const tier = (r.tier || "").trim();\n'
        '\n'
        '  // --- THESIS (open by default) ---\n'
        '  const probTT  = _dt(_fmtPct(prob), "p_model from CSV pick_prob");\n'
        '  const fairTT  = _dt(_fmtPct(fair), "fair_prob from CSV (devig of opener)");\n'
        '  const edgeTT  = _dt(_fmtPp(edgePp), "edge_pp = (p_model - fair_prob) * 100");\n'
        '  const gradeTT = _dt(grade, "Rule-based grader output (8 rules)");\n'
        '  const tierTT  = _dt(tier || "—", "Tier assigned BEFORE market gate");\n'
        '  let thesis = `<p><strong>${pick} ML over ${matchup}</strong> — model says ${probTT}, market implies ${fairTT}, edge ${edgeTT}. Grade ${gradeTT} at tier ${tierTT}.</p>`;\n'
        '\n'
        '  // --- PITCHING MATCHUP ---\n'
        '  let pitchHtml = `<p><span class="muted">${awaySp || "(away SP TBA)"} vs ${homeSp || "(home SP TBA)"}.</span></p>`;\n'
        '  if (isFinite(f5) && isFinite(full)) {\n'
        '    const f5Tilt = f5 >= 0.5 ? "favors home" : "favors away";\n'
        '    const fullTilt = full >= 0.5 ? "favors home" : "favors away";\n'
        '    const agree = (f5 >= 0.5) === (full >= 0.5);\n'
        '    const f5TT = _dt(_fmtPct(f5), "f5_prob from CSV (Stage 1 model output)");\n'
        '    const fullTT = _dt(_fmtPct(full), "full_prob from CSV (Stage 2 model output)");\n'
        '    pitchHtml += `<p>Stage 1 (first-5) ${f5TT} ${f5Tilt}; Stage 2 (full) ${fullTT} ${fullTilt}. ` +\n'
        '      (agree ? `<strong>Stages agree</strong> on direction.` : `<strong>Stages disagree</strong> — bullpen / late-leverage carries the deciding signal.`) + `</p>`;\n'
        '  }\n'
        '\n'
        '  // --- SIGNALS + RULE FIRINGS ---\n'
        '  let sigHtml = "";\n'
        '  if (sigList.length) {\n'
        '    sigHtml += `<p><strong>Active signals (${sigList.length}):</strong> ${sigList.map(s => `<code>${s.replace(/</g,"&lt;")}</code>`).join(", ")}.</p>`;\n'
        '  }\n'
        '  if (reasons.length) {\n'
        '    sigHtml += `<p><strong>Rule layer firings:</strong> ${reasons.map(c => `<span style="background:rgba(255,255,255,0.06);padding:0.05rem 0.3rem;border-radius:3px;font-size:0.82rem;"><strong>${c.tag}</strong></span>`).join(" ")}</p>`;\n'
        '  }\n'
        '  if (stress) sigHtml += `<p><span class="flag">Stress warnings:</span> <span class="muted">${stress}</span></p>`;\n'
        '\n'
        '  // --- PQI + BULLPEN ---\n'
        '  let pqiHtml = "";\n'
        '  if (isFinite(pqi)) {\n'
        '    const pqiTT = _dt(_fmtNum(pqi, 1), "pqi_diff from CSV (PitchingQualityIndex Δ)");\n'
        '    const bpTT  = _dt(_fmtNum(hlBp, 4), "hl_bullpen_xwoba_gap from CSV (pick vs opp bullpen)");\n'
        '    const pqiTilt = pqi > 0 ? "with the pick" : "against the pick";\n'
        '    pqiHtml += `<p><strong>PQI Δ:</strong> ${pqiTT} <span class="muted">(${pqiTilt})</span>. Bullpen-quality gap: ${bpTT} <span class="muted">(${isFinite(hlBp) && hlBp < 0 ? "pick has better bullpen" : "pick has worse bullpen"}).</span></p>`;\n'
        '  }\n'
        '\n'
        '  // --- LINEUP (platoon) ---\n'
        '  const platoonAway = _summarizeLineupJson(r.away_top_5_batters_json, "Away");\n'
        '  const platoonHome = _summarizeLineupJson(r.home_top_5_batters_json, "Home");\n'
        '  let lineupHtml = "";\n'
        '  if (platoonAway || platoonHome) {\n'
        '    lineupHtml += `<ul>`;\n'
        '    if (platoonAway) lineupHtml += `<li>${platoonAway}</li>`;\n'
        '    if (platoonHome) lineupHtml += `<li>${platoonHome}</li>`;\n'
        '    lineupHtml += `</ul>`;\n'
        '  }\n'
        '\n'
        '  // --- BvP ---\n'
        '  const bvpAway = _summarizeBvpJson(r.away_bvp_top5_json, "Away");\n'
        '  const bvpHome = _summarizeBvpJson(r.home_bvp_top5_json, "Home");\n'
        '  let bvpHtml = "";\n'
        '  if (bvpAway || bvpHome) {\n'
        '    bvpHtml += `<ul>`;\n'
        '    if (bvpAway) bvpHtml += `<li>${bvpAway}</li>`;\n'
        '    if (bvpHome) bvpHtml += `<li>${bvpHome}</li>`;\n'
        '    bvpHtml += `</ul>`;\n'
        '  }\n'
        '\n'
        '  // --- BULLPEN OUTLOOK (from sidecar) ---\n'
        '  let bpHtml = "";\n'
        '  try {\n'
        '    const _bpMeta = _bullpenMetaForMatchup(item.matchup);\n'
        '    if (_bpMeta && (_bpMeta.away || _bpMeta.home)) {\n'
        '      bpHtml += `<ul style="margin:0.3rem 0;font-size:0.88rem;">`;\n'
        '      if (_bpMeta.away) bpHtml += `<li>${_bullpenTeamNarrative(_bpMeta.away, _bpMeta.awayTeam)}</li>`;\n'
        '      if (_bpMeta.home) bpHtml += `<li>${_bullpenTeamNarrative(_bpMeta.home, _bpMeta.homeTeam)}</li>`;\n'
        '      bpHtml += `</ul>`;\n'
        '    }\n'
        '  } catch (_e_bp_narr) {}\n'
        '\n'
        '  // --- COUNTER-SIGNALS (new) ---\n'
        '  const counterHtml = _deepCounterSignalsML(r, item);\n'
        '\n'
        '  // --- WHAT WOULD CHANGE MY MIND (new) ---\n'
        '  const pivotHtml = _deepPivotPointsML(r, item);\n'
        '\n'
        '  // --- BOTTOM LINE ---\n'
        '  let verdict;\n'
        '  if (["A","A-"].includes(grade)) verdict = `<span class="flag">High conviction.</span> Multiple confirming layers; treat as anchor candidate.`;\n'
        '  else if (["B","B+","B-"].includes(grade)) verdict = `<span class="flag">Moderate conviction.</span> Some signals present but not a parlay-anchor.`;\n'
        '  else if (grade === "C") verdict = `<span class="flag-red">Skip.</span> Cap layer or low conviction — do not stake.`;\n'
        '  else if (grade === "D") verdict = `<span class="flag-red">Avoid.</span> No confluence, market disagreement, or stress warnings active.`;\n'
        '  else verdict = `Tier not graded yet (PENDING_SP_DATA or equivalent).`;\n'
        '\n'
        '  // Assemble — thesis is open; everything else is collapsed by default.\n'
        '  return _deepSection("Pick thesis", thesis, true)\n'
        '       + _deepSection("Pitching matchup", pitchHtml, false)\n'
        '       + _deepSection("Signals &amp; rule firings", sigHtml, false)\n'
        '       + _deepSection("PQI &amp; bullpen gap", pqiHtml, false)\n'
        '       + _deepSection("Lineup (platoon splits)", lineupHtml, false)\n'
        '       + _deepSection("BvP (career vs SP)", bvpHtml, false)\n'
        '       + _deepSection("Bullpen outlook", bpHtml, false)\n'
        '       + _deepSection("Counter-signals", counterHtml, false)\n'
        '       + _deepSection("What would change my mind", pivotHtml, false)\n'
        '       + _deepSection("Bottom line", `<p>${verdict}</p>`, true)\n'
        '       + _deepAskClaudeButton("ml", item);\n'
        '}\n'
    )

    # Replace the OLD _deepNarrativeML function body with new_ml.
    # Strategy: find the OLD function start, find the next "// === O/U Totals"
    # comment, splice in new_ml in place of everything between.
    OLD_ML_START = 'function _deepNarrativeML(item) {\n'
    OLD_OU_HEADER = '\n// === O/U Totals deep narrative ===\n'
    a = src.index(OLD_ML_START)
    b = src.index(OLD_OU_HEADER, a)
    # new_ml must end with the closing brace + a trailing newline so the
    # next section's "\n// === ..." comment lines up cleanly.
    src = src[:a] + new_ml + src[b:]
    print("[ok]   3: full _deepNarrativeML body swapped")

    # ---------- 4. Replace _deepNarrativeOU body ----------
    new_ou = (
        'function _deepNarrativeOU(item) {\n'
        '  const r = item._row || {};\n'
        '  const t = item._totalsRow || {};\n'
        '  const matchup = item.matchup;\n'
        '  const line = item.line;\n'
        '  const side = item.side;\n'
        '  const prob = item.prob;\n'
        '  const pred = parseFloat(item.pred_runs);\n'
        '  const edge = item.edge_pp;\n'
        '  const stake = parseFloat(t.stake_units);\n'
        '  const homeSp = (r.home_sp_name || "").trim();\n'
        '  const awaySp = (r.away_sp_name || "").trim();\n'
        '  const homeK = parseFloat(r.home_sp_k_pct);\n'
        '  const awayK = parseFloat(r.away_sp_k_pct);\n'
        '  const hlBp = parseFloat(r.hl_bullpen_xwoba_gap);\n'
        '  const homeConc = parseFloat(r.home_lineup_concentration);\n'
        '  const awayConc = parseFloat(r.away_lineup_concentration);\n'
        '  const umpK = parseFloat(r.ump_k_pct_delta);\n'
        '  const umpBB = parseFloat(r.ump_bb_pct_delta);\n'
        '\n'
        '  const predTT = _dt(_fmtNum(pred, 1), "pred_runs from picks_totals_<date>.csv (model output)");\n'
        '  const lineTT = _dt(line.toFixed(1), "total_line from picks_totals_<date>.csv (market)");\n'
        '  const edgeTT = _dt(_fmtPp(edge), "(p_model - book_fair) * 100");\n'
        '  let thesis = `<p><strong>${side} ${lineTT} on ${matchup}</strong> — projection ${predTT} runs (${_fmtNum(pred - line, 1)} runs on the ${side === "OVER" ? "over" : "under"} side). Model prob ${_dt(_fmtPct(prob), "our_prob from CSV")} vs book-fair ${_dt(_fmtPct(parseFloat(t.book_fair)), "book_fair from CSV")}, edge ${edgeTT}.${isFinite(stake) ? ` Recommended stake: <strong>${stake.toFixed(2)}u</strong> (Kelly-fractional).` : ""}</p>`;\n'
        '\n'
        '  let pitchHtml = `<p><span class="muted">${awaySp || "(away SP TBA)"} vs ${homeSp || "(home SP TBA)"}.</span></p>`;\n'
        '  if (isFinite(homeK) && isFinite(awayK)) {\n'
        '    const totalK = (homeK + awayK) / 2;\n'
        '    const lgAvg = 22.5;\n'
        '    const tilt = totalK > lgAvg + 1 ? "above league average (K-friendly = run-suppressing)" :\n'
        '                 totalK < lgAvg - 1 ? "below league average (lower K = more contact = more runs)" :\n'
        '                                       "near league average";\n'
        '    pitchHtml += `<p>Combined SP K%: ${_dt(totalK.toFixed(1)+"%", "(home_sp_k_pct + away_sp_k_pct) / 2")} (${tilt}). Home ${_dt(_fmtNum(homeK,1)+"%", "home_sp_k_pct CSV")}; Away ${_dt(_fmtNum(awayK,1)+"%", "away_sp_k_pct CSV")}.</p>`;\n'
        '  }\n'
        '\n'
        '  let bpHtml = "";\n'
        '  if (isFinite(hlBp)) {\n'
        '    const sign = hlBp >= 0 ? "home worse" : "home better";\n'
        '    bpHtml += `<p>Bullpen gap (home vs away xwOBA-allowed): ${_dt(_fmtNum(hlBp,4), "hl_bullpen_xwoba_gap CSV")} <span class="muted">(${sign}).</span> ${Math.abs(hlBp) > 0.015 ? "Material gap — could push runs to one side late." : "Narrow gap — comparable late."}</p>`;\n'
        '  }\n'
        '\n'
        '  let lineupHtml = "";\n'
        '  if (isFinite(homeConc) || isFinite(awayConc)) {\n'
        '    lineupHtml += `<p>Lineup concentration (top-3 vs bottom-3 xwOBA): home ${_dt(_fmtNum(homeConc,2), "home_lineup_concentration CSV")}, away ${_dt(_fmtNum(awayConc,2), "away_lineup_concentration CSV")}. <span class="muted">>1.5 = top-heavy; >2.0 = severe star-anchored shape that bullpens can navigate around late.</span></p>`;\n'
        '  }\n'
        '\n'
        '  let umpHtml = "";\n'
        '  if (isFinite(umpK) || isFinite(umpBB)) {\n'
        '    umpHtml += `<p>Plate ump: K Δ ${_dt(_fmtNum(umpK,3), "ump_k_pct_delta CSV")}, BB Δ ${_dt(_fmtNum(umpBB,3), "ump_bb_pct_delta CSV")}. <span class="muted">+K Δ = pitcher-friendly (run-suppressing); +BB Δ = hitter-friendly (run-boosting).</span></p>`;\n'
        '  }\n'
        '\n'
        '  const bvpAway = _summarizeBvpJson(r.away_bvp_top5_json, "Away");\n'
        '  const bvpHome = _summarizeBvpJson(r.home_bvp_top5_json, "Home");\n'
        '  let bvpHtml = "";\n'
        '  if (bvpAway || bvpHome) {\n'
        '    bvpHtml += `<ul>`;\n'
        '    if (bvpAway) bvpHtml += `<li>${bvpAway}</li>`;\n'
        '    if (bvpHome) bvpHtml += `<li>${bvpHome}</li>`;\n'
        '    bvpHtml += `</ul>`;\n'
        '  }\n'
        '\n'
        '  const counterHtml = _deepCounterSignalsOU(r, item);\n'
        '  const pivotHtml = _deepPivotPointsOU(r, item);\n'
        '\n'
        '  let verdict;\n'
        '  const absEdge = Math.abs(edge || 0);\n'
        '  if (absEdge >= 5) verdict = `<span class="flag">Strong edge.</span> Model materially diverges from book-fair; ${side} side worth a look.`;\n'
        '  else if (absEdge >= 2) verdict = `<span class="flag">Moderate edge.</span> Some signal, but within typical model-to-market noise.`;\n'
        '  else verdict = `<span class="muted">Thin edge.</span> Model and market closely aligned; little to extract.`;\n'
        '\n'
        '  return _deepSection("Pick thesis", thesis, true)\n'
        '       + _deepSection("Pitching environment", pitchHtml, false)\n'
        '       + _deepSection("Bullpen", bpHtml, false)\n'
        '       + _deepSection("Lineup concentration", lineupHtml, false)\n'
        '       + _deepSection("Umpire", umpHtml, false)\n'
        '       + _deepSection("BvP run-environment", bvpHtml, false)\n'
        '       + _deepSection("Counter-signals", counterHtml, false)\n'
        '       + _deepSection("What would change my mind", pivotHtml, false)\n'
        '       + _deepSection("Bottom line", `<p>${verdict}</p>`, true)\n'
        '       + _deepAskClaudeButton("ou", item);\n'
        '}\n'
    )

    # OU function: find boundary the same way.
    OLD_OU_START = 'function _deepNarrativeOU(item) {\n'
    OLD_K_HEADER = '\n// === Pitcher K deep narrative ===\n'
    a = src.index(OLD_OU_START)
    b = src.index(OLD_K_HEADER, a)
    src = src[:a] + new_ou + src[b:]
    print("[ok]   4: full _deepNarrativeOU body swapped")

    # ---------- 5. Replace _deepNarrativeK body ----------
    new_k = (
        'function _deepNarrativeK(item) {\n'
        '  const r = item._row || {};\n'
        '  const name = item.name;\n'
        '  const matchup = item.matchup;\n'
        '  const team = item.team;\n'
        '  const expK = item.expected_K;\n'
        '  const kPct = item.k_pct;\n'
        '  const umpK = parseFloat(r.ump_k_pct_delta);\n'
        '\n'
        '  const expTT = _dt(expK.toFixed(1), "expected_K from K-prop model (lambda over ~26 BF, 6 IP)");\n'
        '  const kPctTT = _dt(kPct.toFixed(1)+"%", "season K rate for the SP");\n'
        '  let thesis = `<p><strong>${name}</strong> (${team === "home" ? "home" : "away"} starter in <strong>${matchup}</strong>) — projecting ${expTT} K over ~26 BF / 6 IP at a ${kPctTT} K rate.</p>`;\n'
        '\n'
        '  // League context\n'
        '  const lgAvg = 22.5;\n'
        '  let leagueHtml = "";\n'
        '  if (kPct > lgAvg + 3) leagueHtml = `<p><strong>Above-average K artist.</strong> ${(kPct - lgAvg).toFixed(1)}pp above the ~${lgAvg}% league average. Tends to outperform implied K props when stuff is on.</p>`;\n'
        '  else if (kPct < lgAvg - 3) leagueHtml = `<p><strong>Pitch-to-contact profile.</strong> ${(lgAvg - kPct).toFixed(1)}pp below the ~${lgAvg}% league average. Expect K props to settle low even on a good night.</p>`;\n'
        '  else leagueHtml = `<p><strong>Roughly league-average</strong> K rate (~${lgAvg}%). Threshold-relative props track conditions (umpire, opposing whiff rate, lineup contact quality).</p>`;\n'
        '\n'
        '  // Thresholds\n'
        '  let threshHtml = `<ul>`;\n'
        '  if (item.p_over_5 !== null) threshHtml += `<li>P(K ≥ 5) = <strong>${(item.p_over_5*100).toFixed(0)}%</strong> ${item.p_over_5 > 0.85 ? "<span class=\\"muted\\">— near-lock floor</span>" : ""}</li>`;\n'
        '  if (item.p_over_6 !== null) threshHtml += `<li>P(K ≥ 6) = <strong>${(item.p_over_6*100).toFixed(0)}%</strong></li>`;\n'
        '  if (item.p_over_7 !== null) threshHtml += `<li>P(K ≥ 7) = <strong>${(item.p_over_7*100).toFixed(0)}%</strong> ${item.p_over_7 > 0.50 ? "<span class=\\"muted\\">— ceiling pick territory</span>" : ""}</li>`;\n'
        '  threshHtml += `</ul><p class="muted" style="font-size:0.78rem;">Source: normal approximation to Poisson(${expK.toFixed(1)}).</p>`;\n'
        '\n'
        '  // Umpire\n'
        '  let umpHtml = "";\n'
        '  if (isFinite(umpK)) {\n'
        '    const tilt = umpK > 0 ? "boosts K rate" : "suppresses K rate";\n'
        '    umpHtml = `<p>Umpire K Δ: ${_dt(_fmtNum(umpK,3), "ump_k_pct_delta CSV (vs league avg 0)")} <span class="muted">(${tilt} vs league average).</span></p>`;\n'
        '  }\n'
        '\n'
        '  const counterHtml = _deepCounterSignalsK(r, item);\n'
        '  const pivotHtml = _deepPivotPointsK(r, item);\n'
        '\n'
        '  let verdict;\n'
        '  if (kPct >= 27) verdict = `<span class="flag">Plus K profile.</span> Comfortable looking at over 5.5 / 6.5; over 7.5 carries real edge.`;\n'
        '  else if (kPct >= 23) verdict = `<span class="flag">Solid K floor.</span> Over 5.5 is the safe play; 6.5 needs the ump + matchup to lean.`;\n'
        '  else verdict = `<span class="flag-red">Contact-tolerant.</span> Default lean is under unless lineup whiff rate is materially elevated.`;\n'
        '\n'
        '  return _deepSection("Pick thesis", thesis, true)\n'
        '       + _deepSection("League comparison", leagueHtml, false)\n'
        '       + _deepSection("Threshold probabilities", threshHtml, false)\n'
        '       + _deepSection("Umpire effect", umpHtml, false)\n'
        '       + _deepSection("Counter-signals", counterHtml, false)\n'
        '       + _deepSection("What would change my mind", pivotHtml, false)\n'
        '       + _deepSection("Bottom line", `<p>${verdict}</p>`, true)\n'
        '       + _deepAskClaudeButton("k", item);\n'
        '}\n'
    )

    # K function: find boundary.
    OLD_K_START = 'function _deepNarrativeK(item) {\n'
    OLD_DISPATCH = '\nfunction _propRenderDeepDeterministic'
    a = src.index(OLD_K_START)
    b = src.index(OLD_DISPATCH, a)
    src = src[:a] + new_k + src[b:]
    print("[ok]   5: full _deepNarrativeK body swapped")

    # ---------- 6. Click handler for deep-sections + ask-claude buttons ----------
    handler = (
        '\n// =============================================================\n'
        '// Deep-analysis click handlers (2026-05-24)\n'
        '//   - .deep-h5 toggles its parent .deep-section open/close.\n'
        '//   - .deep-ask-claude pre-fills the Ask-the-Slate textarea\n'
        '//     and scrolls to it (works on github.io fallback too —\n'
        '//     the user just clicks Ask to send).\n'
        '// =============================================================\n'
        'document.addEventListener("click", function (e) {\n'
        '  const h = e.target.closest(".deep-h5");\n'
        '  if (h) {\n'
        '    const section = h.parentElement;\n'
        '    if (section && section.classList.contains("deep-section")) {\n'
        '      section.classList.toggle("open");\n'
        '      e.stopPropagation();\n'
        '      return;\n'
        '    }\n'
        '  }\n'
        '  const ask = e.target.closest("[data-deep-ask-claude]");\n'
        '  if (ask) {\n'
        '    const q = ask.getAttribute("data-question") || "";\n'
        '    const ta = document.getElementById("ask-claude-input");\n'
        '    const card = document.getElementById("ask-claude-card");\n'
        '    if (ta && q) {\n'
        '      ta.value = q;\n'
        '      if (card && card.style.display === "none") card.style.display = "";\n'
        '      ta.focus();\n'
        '      ta.scrollIntoView({behavior: "smooth", block: "center"});\n'
        '    }\n'
        '    e.stopPropagation();\n'
        '  }\n'
        '});\n'
    )

    src = must_replace(
        src,
        'document.addEventListener("DOMContentLoaded", _initAskClaude);\n',
        'document.addEventListener("DOMContentLoaded", _initAskClaude);\n' + handler,
        "6: deep-section + ask-claude click delegation",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
