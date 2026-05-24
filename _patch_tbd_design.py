"""
TBD-design patch:
  1. fetchMLBResults: doubleheader-aware keying via gameNumber
  2. matchResult: parse (G1)/(G2) tag, look up specific game
  3. _resultChipHtml: LIVE / PREGAME / POSTPONED with distinct colors;
     LIVE accepts live-score extra
  4. _gameMLStatus / _totalStatus / _pitcherKStatus: return
     LIVE / PREGAME based on statusText
  5. TPO renderTopProbableOutcomes: pass live-score to _resultChipHtml
  6. renderSlate result cell: route through chip helper with live score
  7. Remove _isPostponedRow filter from all 3 call sites so
     POSTPONED badge code path actually renders

Adheres to feedback_edit_tool_pivot: uses Python str.replace + ast/parse,
NOT the Edit tool.
"""

from pathlib import Path
import ast

P = Path("docs/index.html")
src = P.read_text(encoding="utf-8")
N0 = len(src)

# ---------- 1. fetchMLBResults: doubleheader-aware keying ----------
OLD_FETCH_LOOP = """      if (!awayAbbr || !homeAbbr) continue;
      const aliases = (a) => [a, TEAM_ABBR_ALIASES[a]].filter(Boolean);
      for (const aA of aliases(awayAbbr)) {
        for (const hA of aliases(homeAbbr)) {
          out[`${aA}@${hA}`] = entry;
          out[`${hA}@${aA}`] = entry;
        }
      }
    }
    return out;"""

NEW_FETCH_LOOP = """      if (!awayAbbr || !homeAbbr) continue;
      // Doubleheader-aware keying (2026-05-23): preserve gameNumber so the
      // second game of a DH does not overwrite the first.  Bare key still
      // points to the first game encountered for back-compat.
      const gameNumber = g.gameNumber || 1;
      const doubleHeader = (g.doubleHeader || "N").toUpperCase();
      entry.gameNumber = gameNumber;
      entry.isDoubleHeader = doubleHeader !== "N";
      const aliases = (a) => [a, TEAM_ABBR_ALIASES[a]].filter(Boolean);
      for (const aA of aliases(awayAbbr)) {
        for (const hA of aliases(homeAbbr)) {
          const k1 = `${aA}@${hA}`;
          const k2 = `${hA}@${aA}`;
          // Always write G-suffixed key so matchResult can disambiguate
          out[`${k1}@G${gameNumber}`] = entry;
          out[`${k2}@G${gameNumber}`] = entry;
          // Bare key: only set if not yet taken (preserve G1 as default).
          if (out[k1] == null) out[k1] = entry;
          if (out[k2] == null) out[k2] = entry;
        }
      }
    }
    return out;"""

assert OLD_FETCH_LOOP in src, "fetch loop anchor missing"
src = src.replace(OLD_FETCH_LOOP, NEW_FETCH_LOOP, 1)

# ---------- 2. matchResult: parse (G1)/(G2) ----------
OLD_MATCH = """function matchResult(r, results) {
  if (!results) return null;
  const matchup = (r.matchup || "").trim();
  if (!matchup) return null;
  // Pull two ALL-CAPS abbrev tokens out of the matchup string
  const abbrs = matchup.match(/[A-Z]{2,4}/g);
  if (abbrs && abbrs.length >= 2) {
    const a = abbrs[0], b = abbrs[1];
    return results[`${a}@${b}`] || results[`${b}@${a}`] || null;
  }
  return null;
}"""

NEW_MATCH = """function matchResult(r, results) {
  if (!results) return null;
  const matchup = (r.matchup || "").trim();
  if (!matchup) return null;
  // Doubleheader disambiguation (2026-05-23): if the matchup string has
  // "(G1)" or "(G2)" — appended at render time via _addSeriesSuffix —
  // look up the specific game first before falling back to bare key.
  let gameNumber = null;
  const gMatch = matchup.match(/\\(G([12])(?:\\s+of\\s+\\d+)?\\)/i);
  if (gMatch) gameNumber = parseInt(gMatch[1], 10);
  // Pull two ALL-CAPS abbrev tokens out of the matchup string
  const abbrs = matchup.match(/[A-Z]{2,4}/g);
  if (abbrs && abbrs.length >= 2) {
    const a = abbrs[0], b = abbrs[1];
    if (gameNumber != null) {
      const specific = results[`${a}@${b}@G${gameNumber}`]
                    || results[`${b}@${a}@G${gameNumber}`];
      if (specific) return specific;
    }
    return results[`${a}@${b}`] || results[`${b}@${a}`] || null;
  }
  return null;
}"""

assert OLD_MATCH in src, "matchResult anchor missing"
src = src.replace(OLD_MATCH, NEW_MATCH, 1)

# ---------- 3. _resultChipHtml: add LIVE / PREGAME ----------
OLD_CHIP = """function _resultChipHtml(status) {
  if (status === "HIT")  return `<span class="result-chip win">✓ HIT</span>`;
  if (status === "MISS") return `<span class="result-chip loss">✗ MISS</span>`;
  if (status === "PUSH") return `<span class="result-chip push">— PUSH</span>`;
  if (status === "POSTPONED") return `<span class="result-chip tbd" style="background:#3a2c1a;color:#d29922;border-color:#d29922;">POSTPONED</span>`;
  if (status === "TBD")  return `<span class="result-chip tbd">TBD</span>`;
  return "";
}"""

NEW_CHIP = """function _resultChipHtml(status, extra) {
  if (status === "HIT")  return `<span class="result-chip win">✓ HIT</span>`;
  if (status === "MISS") return `<span class="result-chip loss">✗ MISS</span>`;
  if (status === "PUSH") return `<span class="result-chip push">— PUSH</span>`;
  if (status === "POSTPONED") return `<span class="result-chip tbd" style="background:#3a2c1a;color:#d29922;border-color:#d29922;">POSTPONED</span>`;
  if (status === "LIVE") {
    const label = extra ? `● LIVE ${extra}` : `● LIVE`;
    return `<span class="result-chip tbd" style="background:#1a3a2a;color:#3fb950;border-color:#3fb950;">${label}</span>`;
  }
  if (status === "PREGAME") {
    return `<span class="result-chip tbd" style="background:#1a2a3a;color:#79c0ff;border-color:#79c0ff;">⏱ PRE-GAME</span>`;
  }
  if (status === "TBD")  return `<span class="result-chip tbd">TBD</span>`;
  return "";
}"""

assert OLD_CHIP in src, "_resultChipHtml anchor missing"
src = src.replace(OLD_CHIP, NEW_CHIP, 1)

# ---------- 4a. _gameMLStatus: LIVE / PREGAME ----------
OLD_ML_STATUS = """function _gameMLStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  if (/postpon|suspend|cancel/i.test(result.statusText || "")) return "POSTPONED";
  if (result.isFinal === false) return "TBD";
  if (result.winner == null) return "TBD";"""

NEW_ML_STATUS = """function _gameMLStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  const _st = (result.statusText || "").toLowerCase();
  if (/postpon|suspend|cancel/.test(_st)) return "POSTPONED";
  if (result.isFinal === false) {
    if (/in progress|manager challenge|delayed/.test(_st)) return "LIVE";
    if (/pre-game|warmup|scheduled/.test(_st)) return "PREGAME";
    return "TBD";
  }
  if (result.winner == null) return "TBD";"""

assert OLD_ML_STATUS in src, "_gameMLStatus anchor missing"
src = src.replace(OLD_ML_STATUS, NEW_ML_STATUS, 1)

# ---------- 4b. _totalStatus: LIVE / PREGAME ----------
OLD_TOTAL_STATUS = """function _totalStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  if (/postpon|suspend|cancel/i.test(result.statusText || "")) return "POSTPONED";
  if (result.isFinal === false) return "TBD";"""

NEW_TOTAL_STATUS = """function _totalStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  const _st = (result.statusText || "").toLowerCase();
  if (/postpon|suspend|cancel/.test(_st)) return "POSTPONED";
  if (result.isFinal === false) {
    if (/in progress|manager challenge|delayed/.test(_st)) return "LIVE";
    if (/pre-game|warmup|scheduled/.test(_st)) return "PREGAME";
    return "TBD";
  }"""

assert OLD_TOTAL_STATUS in src, "_totalStatus anchor missing"
src = src.replace(OLD_TOTAL_STATUS, NEW_TOTAL_STATUS, 1)

# ---------- 4c. _pitcherKStatus: LIVE / PREGAME ----------
OLD_K_STATUS = """function _pitcherKStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  if (/postpon|suspend|cancel/i.test(result.statusText || "")) return "POSTPONED";
  if (result.isFinal === false) return "TBD";"""

NEW_K_STATUS = """function _pitcherKStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  const _st = (result.statusText || "").toLowerCase();
  if (/postpon|suspend|cancel/.test(_st)) return "POSTPONED";
  if (result.isFinal === false) {
    if (/in progress|manager challenge|delayed/.test(_st)) return "LIVE";
    if (/pre-game|warmup|scheduled/.test(_st)) return "PREGAME";
    return "TBD";
  }"""

assert OLD_K_STATUS in src, "_pitcherKStatus anchor missing"
src = src.replace(OLD_K_STATUS, NEW_K_STATUS, 1)

# ---------- 5. TPO render: pass live-score extra to _resultChipHtml ----------
# Pass live-score string ("aSc-hSc") to LIVE chip; other statuses pass undefined
OLD_TPO_ML = """    gameMLs.forEach((item, i) => {
      const title = `${item.pick} ML over ${item.matchup}`;
      html += _propCard(i, title, "", _narrateGameML(item), "ml", item, _resultChipHtml(mlStatuses[i]));
    });"""

NEW_TPO_ML = """    gameMLs.forEach((item, i) => {
      const title = `${item.pick} ML over ${item.matchup}`;
      html += _propCard(i, title, "", _narrateGameML(item), "ml", item, _resultChipHtml(mlStatuses[i], _liveScoreFor(item, results)));
    });"""

assert OLD_TPO_ML in src, "TPO ML loop anchor missing"
src = src.replace(OLD_TPO_ML, NEW_TPO_ML, 1)

OLD_TPO_OU = """    totals.forEach((item, i) => {
      const title = `${item.side} ${item.line.toFixed(1)} on ${item.matchup}`;
      html += _propCard(i, title, "", _narrateTotal(item), "ou", item, _resultChipHtml(ouStatuses[i]));
    });"""

NEW_TPO_OU = """    totals.forEach((item, i) => {
      const title = `${item.side} ${item.line.toFixed(1)} on ${item.matchup}`;
      html += _propCard(i, title, "", _narrateTotal(item), "ou", item, _resultChipHtml(ouStatuses[i], _liveScoreFor(item, results)));
    });"""

assert OLD_TPO_OU in src, "TPO OU loop anchor missing"
src = src.replace(OLD_TPO_OU, NEW_TPO_OU, 1)

OLD_TPO_K = """    ks.forEach((item, i) => {
      const title = `${item.name} — ${item.expected_K.toFixed(1)} expected K`;
      html += _propCard(i, title, "", _narrateKProp(item), "k", item, _resultChipHtml(kStatuses[i]));
    });"""

NEW_TPO_K = """    ks.forEach((item, i) => {
      const title = `${item.name} — ${item.expected_K.toFixed(1)} expected K`;
      html += _propCard(i, title, "", _narrateKProp(item), "k", item, _resultChipHtml(kStatuses[i], _liveScoreFor(item, results)));
    });"""

assert OLD_TPO_K in src, "TPO K loop anchor missing"
src = src.replace(OLD_TPO_K, NEW_TPO_K, 1)

# ---------- 5b. helper _liveScoreFor injected near _resultChipHtml ----------
# Inject the helper above _resultChipHtml
LIVE_SCORE_HELPER = """// Live-score helper for LIVE chips (2026-05-23).  Returns "AWY 4-3 HOM" or
// empty string.  Used by _resultChipHtml's `extra` arg so the chip carries
// actionable info instead of just saying "LIVE".
function _liveScoreFor(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return "";
  if (result.isFinal !== false) return "";
  const a = result.awayScore, h = result.homeScore;
  if (a == null || h == null) return "";
  return `${result.awayAbbr} ${a}-${h} ${result.homeAbbr}`;
}

// ---------- HIT/MISS/PUSH badge helpers for the Top Probable Outcomes panel ----------
function _resultChipHtml(status, extra) {"""

OLD_CHIP_HEADER = """// ---------- HIT/MISS/PUSH badge helpers for the Top Probable Outcomes panel ----------
function _resultChipHtml(status, extra) {"""

assert OLD_CHIP_HEADER in src, "chip header anchor missing"
src = src.replace(OLD_CHIP_HEADER, LIVE_SCORE_HELPER, 1)

# ---------- 6. renderSlate result cell: route through chip helper + POSTPONED ----------
OLD_RESULT_CELL = """    const result = matchResult(r, results);
    const accuracy = result && result.isFinal ? predictionAccuracy(r, result) : null;
    let resultCell = "";
    if (haveAnyResult) {
      if (accuracy === "win") {
        resultCell = `<td><span class="result-chip win">✓ WIN</span></td>`;
      } else if (accuracy === "loss") {
        resultCell = `<td><span class="result-chip loss">✗ LOSS</span></td>`;
      } else if (result && !result.isFinal) {
        resultCell = `<td><span class="result-chip tbd">${(result.statusText || "TBD").slice(0,12)}</span></td>`;
      } else {
        resultCell = `<td><span class="muted">—</span></td>`;
      }
    }"""

NEW_RESULT_CELL = """    const result = matchResult(r, results);
    const accuracy = result && result.isFinal ? predictionAccuracy(r, result) : null;
    let resultCell = "";
    if (haveAnyResult) {
      if (accuracy === "win") {
        resultCell = `<td><span class="result-chip win">✓ WIN</span></td>`;
      } else if (accuracy === "loss") {
        resultCell = `<td><span class="result-chip loss">✗ LOSS</span></td>`;
      } else if (result && !result.isFinal) {
        // Route through the chip helper so postponed / live / pre-game
        // games get distinct color-coded styling instead of one undifferentiated
        // gray "TBD" chip (2026-05-23).
        const _st = (result.statusText || "").toLowerCase();
        let _sl = "TBD";
        let _extra = undefined;
        if (/postpon|suspend|cancel/.test(_st)) _sl = "POSTPONED";
        else if (/in progress|manager challenge|delayed/.test(_st)) {
          _sl = "LIVE";
          if (result.awayScore != null && result.homeScore != null) {
            _extra = `${result.awayAbbr} ${result.awayScore}-${result.homeScore} ${result.homeAbbr}`;
          }
        }
        else if (/pre-game|warmup|scheduled/.test(_st)) _sl = "PREGAME";
        resultCell = `<td>${_resultChipHtml(_sl, _extra)}</td>`;
      } else {
        resultCell = `<td><span class="muted">—</span></td>`;
      }
    }"""

assert OLD_RESULT_CELL in src, "renderSlate result cell anchor missing"
src = src.replace(OLD_RESULT_CELL, NEW_RESULT_CELL, 1)

# ---------- 7. Remove _isPostponedRow filter from all 3 sites so POSTPONED renders ----------
# Site A: K rerender (line ~3947)
OLD_K_RERENDER = """        // Apply same postponed filter as loadSlate's initial
        // render — otherwise the K-results re-render would
        // overwrite the filtered display with all 16 rows.
        const playable = slate.rows.filter(r => !_isPostponedRow(r, slate.results));
        el.innerHTML = renderTopProbableOutcomes(playable, window.__totalsByMatchup, slate.results);"""

NEW_K_RERENDER = """        // 2026-05-23: postponed games now render via the POSTPONED chip
        // (color-coded amber) instead of being filtered out, so the user
        // sees them on the slate.  Pass all rows through.
        el.innerHTML = renderTopProbableOutcomes(slate.rows, window.__totalsByMatchup, slate.results);"""

assert OLD_K_RERENDER in src, "K rerender filter anchor missing"
src = src.replace(OLD_K_RERENDER, NEW_K_RERENDER, 1)

# Site B: poller re-render (line ~4532)
OLD_POLLER_FILTER = """    window.__slate = { date, rows, gradeMap, results, parlayText: parlayText || "" };
    // Apply postponed filter (same rule as loadSlate)
    const playableRows = rows.filter(r => !_isPostponedRow(r, results));"""

NEW_POLLER_FILTER = """    window.__slate = { date, rows, gradeMap, results, parlayText: parlayText || "" };
    // 2026-05-23: postponed games render via POSTPONED chip (amber);
    // no longer filtered out.
    const playableRows = rows;"""

assert OLD_POLLER_FILTER in src, "poller filter anchor missing"
src = src.replace(OLD_POLLER_FILTER, NEW_POLLER_FILTER, 1)

# Site C: initial loadSlate render (line ~4801)
OLD_LOAD_FILTER = """    // Filter postponed / suspended / cancelled games from the
    // picks display.  Picks for these games came from the
    // pipeline (which ran with the original schedule), but MLB
    // then pulled them — they aren't actionable.  All rows are
    // preserved on window.__slate.rows for the Ask Claude /
    // search interface so users can still query them.
    const playableRows = rows.filter(r => !_isPostponedRow(r, results));"""

NEW_LOAD_FILTER = """    // 2026-05-23: postponed games render via the POSTPONED chip
    // (amber) instead of being filtered out.  All 16 rows go through.
    const playableRows = rows;"""

assert OLD_LOAD_FILTER in src, "load filter anchor missing"
src = src.replace(OLD_LOAD_FILTER, NEW_LOAD_FILTER, 1)

# ---------- Write ----------
P.write_text(src, encoding="utf-8")
N1 = len(src)
print(f"Patched: {N0} -> {N1} bytes (delta {N1 - N0:+d})")
print(f"Final file size: {N1} bytes")

# Quick sanity: count anchors
checks = [
    ("LIVE chip rendered", "● LIVE"),
    ("PREGAME chip rendered", "⏱ PRE-GAME"),
    ("DH-aware key", "@G${gameNumber}"),
    ("_liveScoreFor helper", "function _liveScoreFor"),
    ("matchResult DH parse", "gameNumber = parseInt(gMatch[1]"),
    ("Filter A removed", "el.innerHTML = renderTopProbableOutcomes(slate.rows,"),
    ("Filter B removed", "// 2026-05-23: postponed games render via POSTPONED chip"),
    ("Filter C removed", "// 2026-05-23: postponed games render via the POSTPONED chip"),
]
for label, needle in checks:
    print(f"  {'OK' if needle in src else 'MISS'}: {label}")
