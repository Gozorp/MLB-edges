#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_hr_props_hitmiss.py
-----------------------
Add HIT/MISS grading to the Home Run Props sub-section of "Top Probable
Outcomes" -- the same lifecycle the Game Picks / O/U / Pitcher-K rows already
have. A hitter's prop is HIT if he homered in his (final) game, MISS if the
game is final and he didn't. Pre-final shows PRE-GAME/LIVE/TBD; a section
"(X HIT / Y MISS)" summary appears once any game is final.

Mechanism mirrors the existing K prefetch: a boxscore prefetch caches each
batter's per-game homeRuns keyed by `${gamePk}|${normName}`, then re-renders the
panel. The diag batter payload has no player id, so we match by normalized
statsapi fullName (both the payload and the boxscore come from statsapi).

Front-end only (docs/index.html). No data/model/bake change. Idempotent (5
edits). Run from repo root.
"""
import sys

F = "docs/index.html"

FUNCS = r'''// ---- Home Run Props hit/miss (mirrors the pitcher-K prefetch pattern) ----
// Normalize a player name so the diag payload (statsapi fullName) matches the
// boxscore (also statsapi fullName): strip accents + punctuation, fold case.
function _normName(s) {
  return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[.\u2019'`]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
}

// HIT if the hitter homered in his (final) game; MISS if final and he didn't.
// LIVE/PREGAME/TBD/POSTPONED pre-final; null until the boxscore prefetch lands
// (or if the hitter never batted -> ungraded, no chip).
function _hrPropStatus(item, results) {
  const result = matchResult(item._row || {matchup: item.matchup}, results);
  if (!result) return null;
  const _st = (result.statusText || "").toLowerCase();
  if (/postpon|suspend|cancel/.test(_st)) return "POSTPONED";
  if (result.isFinal === false) {
    if (/in progress|manager challenge|delayed/.test(_st)) return "LIVE";
    if (/pre-game|warmup|scheduled/.test(_st)) return "PREGAME";
    return "TBD";
  }
  if (!result.gamePk) return null;
  const cache = window.__hrResults || {};
  const v = cache[`${result.gamePk}|${_normName(item.name)}`];
  if (v == null) return null;          // boxscore not fetched yet, or didn't bat
  return v >= 1 ? "HIT" : "MISS";
}

// Prefetch boxscores for the final HR-prop games, cache each batter's game HR
// count under `${gamePk}|${normName}`, then re-render so the badges appear.
// Best-effort; dedupes by game and never re-fetches a game already loaded.
async function _prefetchHRResults(hrs, results) {
  if (!hrs || !hrs.length || !results) return;
  if (!window.__hrResults) window.__hrResults = {};
  if (!window.__hrGamesFetched) window.__hrGamesFetched = {};
  const seen = new Set();
  const jobs = [];
  for (const item of hrs) {
    const result = matchResult(item._row || {matchup: item.matchup}, results);
    if (!result || !result.isFinal || !result.gamePk) continue;
    if (seen.has(result.gamePk) || window.__hrGamesFetched[result.gamePk]) continue;
    seen.add(result.gamePk);
    jobs.push(_fetchBoxscore(result.gamePk).then(box => ({gamePk: result.gamePk, box})));
  }
  if (!jobs.length) return;
  const fetched = await Promise.all(jobs);
  let anyNew = false;
  for (const {gamePk, box} of fetched) {
    if (!box || !box.teams) continue;
    window.__hrGamesFetched[gamePk] = true;
    for (const side of ["away", "home"]) {
      const teamBox = box.teams[side];
      if (!teamBox) continue;
      const players = teamBox.players || {};
      for (const pkey of Object.keys(players)) {
        const p = players[pkey] || {};
        const nm = p.person && p.person.fullName;
        if (!nm) continue;
        const hr = p.stats && p.stats.batting && p.stats.batting.homeRuns;
        if (hr == null) continue;
        const key = `${gamePk}|${_normName(nm)}`;
        if (window.__hrResults[key] !== hr) { window.__hrResults[key] = hr; anyNew = true; }
      }
    }
  }
  if (anyNew) {
    const slate = window.__slate;
    if (slate && slate.rows && slate.rows.length) {
      const el = document.getElementById("top-outcomes");
      if (el) el.innerHTML = renderTopProbableOutcomes(slate.rows, window.__totalsByMatchup, slate.results);
    }
  }
}


'''

EDITS = [
    # 1) carry the diag row on each HR item so matchResult keys correctly (DH-safe)
    (
        'oppTeam: oppTeam, _row: r',
        '        out.push({ name: b.name, team: team, matchup: matchup, prob: p, hr: hr, pa: pa, oppSP: oppSP, oppTeam: oppTeam });',
        '        out.push({ name: b.name, team: team, matchup: matchup, prob: p, hr: hr, pa: pa, oppSP: oppSP, oppTeam: oppTeam, _row: r });',
    ),
    # 2) insert the three helpers before renderTopProbableOutcomes
    (
        'function _hrPropStatus(',
        'function renderTopProbableOutcomes(rows, totalsByMatchup, results) {',
        FUNCS + 'function renderTopProbableOutcomes(rows, totalsByMatchup, results) {',
    ),
    # 3) compute statuses + add the section summary to the header
    (
        'const hrStatuses = hrs.map',
        '''  if (hrs.length) {
    html += `<h3 style="margin-top:0.8rem;color:var(--accent);">Home Run Props <span class="muted" style="font-size:0.78rem;font-weight:normal;">(ranked by single-game HR probability)</span></h3>`;''',
        '''  if (hrs.length) {
    const hrStatuses = hrs.map(it => _hrPropStatus(it, results));
    html += `<h3 style="margin-top:0.8rem;color:var(--accent);">Home Run Props <span class="muted" style="font-size:0.78rem;font-weight:normal;">(ranked by single-game HR probability)</span>${_sectionSummary(hrStatuses)}</h3>`;''',
    ),
    # 4) add the HIT/MISS chip to each row's right-hand span
    (
        '_resultChipHtml(hrStatuses[i]',
        '(${rate}%)</span></span>',
        '(${rate}%)</span> ${_resultChipHtml(hrStatuses[i], _liveScoreFor(item, results))}</span>',
    ),
    # 5) kick off the boxscore prefetch when any game is final but ungraded
    (
        'if (hrStatuses.some(s => s == null))',
        '''            + `</div>`;
    });
  } else {''',
        '''            + `</div>`;
    });
    if (hrStatuses.some(s => s == null)) {
      try { _prefetchHRResults(hrs, results); } catch (_) { /* best-effort */ }
    }
  } else {''',
    ),
]


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = 0
    for i, (sentinel, old, new) in enumerate(EDITS, 1):
        if sentinel in work:
            print(f"  edit {i}: skip (already applied)")
            continue
        c = work.count(old)
        if c != 1:
            print(f"  edit {i}: ERROR anchor count={c} (need 1)")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  edit {i}: applied")
    with open(F, "w", encoding="utf-8", newline="") as fh:
        fh.write(work.replace("\n", nl))
    print(f"  done ({applied} applied)")


if __name__ == "__main__":
    main()
