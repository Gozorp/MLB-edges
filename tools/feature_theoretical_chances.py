#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feature_theoretical_chances.py
------------------------------
Wires the "Theoretical chances" HYPOTHETICAL toy model into the dashboard,
front-end only (zero backend/pipeline change -> freeze-safe, works for any
loaded date). A faithful JS port of mlb_edge/theoretical_chances.py computes an
independent, from-scratch win probability from each game's pitching/lineup
signal in the diag, and renders it as a clearly-labeled card in the expanded
deep-analysis panel -- explicitly separate from the model's real pick.

4 idempotent edits to docs/index.html:
  A. CSS for the .theo-card block (before </style>).
  B. Engine JS (_theoChances + inning Monte-Carlo + convolution + WP), inserted
     before toggleNarrative.
  C. A labeled placeholder card in formatNarrative's output (after the win-prob
     card, before the Pitching-matchup section).
  D. A lazy compute+fill hook in toggleNarrative (runs once per game on expand,
     memoized) -- same pattern as the win-prob chart.

Run from repo root. Exits non-zero if any anchor fails to match exactly once.
"""
import sys

F = "docs/index.html"
EDITS = []  # (old, new, sentinel)

# --- A. CSS ------------------------------------------------------------------
CSS = """
/* === theoretical-chances-2026: hypothetical toy WP card === */
.theo-card{margin:.6rem 0;padding:.7rem .9rem;border:1px dashed var(--border);border-radius:8px;background:rgba(88,166,255,0.045);}
.theo-card h4{margin:0 0 .4rem;font-size:.92rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;}
.theo-tag{font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--yellow);border:1px solid var(--yellow);border-radius:999px;padding:.05rem .45rem;font-weight:600;}
.theo-body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:1.05rem;line-height:1.4;}
.theo-num{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;}
.theo-side{color:var(--muted);font-size:.8rem;margin-right:.35rem;}
.theo-vs{color:var(--muted);margin:0 .35rem;}
.theo-card .legend-note{margin:.45rem 0 0;}
/* === end theoretical-chances-2026 === */
"""
EDITS.append(("</style>", CSS + "</style>", "theoretical-chances-2026"))

# --- B. Engine JS (faithful port of mlb_edge/theoretical_chances.py) ---------
ENGINE = r"""
// ===== Theoretical chances (HYPOTHETICAL toy model) =========================
// Front-end port of mlb_edge/theoretical_chances.py. An independent, from-
// scratch win probability shown as a curiosity / cross-check -- NOT the model's
// pick, and isolated from it. Real methods (Log5 + James-Stein shrinkage, a 24
// base-out-state Markov inning sim, 9-inning convolution, honest 0.5 tie split)
// are implemented faithfully; offense/bullpen tilts are small bounded
// heuristics. Memoized; computed lazily on row expand so it costs the slate 0.
const _THEO_LEAGUE_PA = [0.690, 0.085, 0.140, 0.045, 0.004, 0.036]; // out,bb,1b,2b,3b,hr
const _theoMemo = new Map();
function _theoNum(v){ const n = parseFloat(v); return isFinite(n) ? n : NaN; }
function _theoKpct(v){ let n = _theoNum(v); if (!isFinite(n)) return 0.22; if (n > 1) n /= 100; return Math.min(Math.max(n, 0.10), 0.40); }
function _theoRates(oppKpct, offMod){
  // League prior tilted by the OPPOSING starter's K% (whiff suppression vs a
  // ~22% league SP) and a small bounded offense modifier; renormalized (Log5 +
  // shrinkage, compressed).
  const pa = _THEO_LEAGUE_PA.slice();
  const kTilt = oppKpct - 0.22;
  pa[0] *= (1 + 1.4 * kTilt);
  for (let j = 1; j < 6; j++) pa[j] *= (1 - 0.9 * kTilt);
  const m = Math.max(Math.min(offMod, 0.15), -0.15);
  pa[0] *= (1 - 0.6 * m); pa[5] *= (1 + 2.0 * m); pa[3] *= (1 + 1.2 * m); pa[2] *= (1 + 0.8 * m);
  let s = 0; for (let j = 0; j < 6; j++){ pa[j] = Math.max(pa[j], 1e-9); s += pa[j]; }
  return pa.map(x => x / s);
}
function _theoInningPMF(pa, sims){
  const cum = []; let c = 0; for (const p of pa){ c += p; cum.push(c); }
  const maxR = 16, counts = new Array(maxR + 1).fill(0);
  for (let s = 0; s < sims; s++){
    let on1 = false, on2 = false, on3 = false, outs = 0, runs = 0;
    while (outs < 3){
      const x = Math.random(); let ev = 0; while (ev < 5 && x > cum[ev]) ev++;
      if (ev === 0){ outs++; }
      else if (ev === 1){ if (on1 && on2 && on3) runs++; else if (on1 && on2) on3 = true; else if (on1) on2 = true; on1 = true; }
      else if (ev === 2){ if (on3) runs++; on3 = on2; on2 = on1; on1 = true; }
      else if (ev === 3){ runs += (on3 ? 1 : 0) + (on2 ? 1 : 0); on3 = on1; on2 = true; on1 = false; }
      else if (ev === 4){ runs += (on1 ? 1 : 0) + (on2 ? 1 : 0) + (on3 ? 1 : 0); on1 = false; on2 = false; on3 = true; }
      else { runs += 1 + (on1 ? 1 : 0) + (on2 ? 1 : 0) + (on3 ? 1 : 0); on1 = false; on2 = false; on3 = false; }
    }
    counts[Math.min(runs, maxR)]++;
  }
  let tot = 0; for (const v of counts) tot += v;
  return counts.map(v => v / tot);
}
function _theoConv(a, b){ const out = new Array(a.length + b.length - 1).fill(0);
  for (let i = 0; i < a.length; i++) for (let j = 0; j < b.length; j++) out[i + j] += a[i] * b[j]; return out; }
function _theoGamePMF(inn){ let p = inn.slice(); for (let k = 0; k < 8; k++) p = _theoConv(p, inn);
  let s = 0; for (const v of p) s += v; return p.map(v => v / s); }
function _theoLeverage(pmf, supp){ if (!supp) return pmf;
  const out = pmf.map((v, i) => v * Math.exp(-Math.abs(supp) * 0.04 * i));
  let s = 0; for (const v of out) s += v; return out.map(v => v / s); }
function _theoWP(h, a){ const ca = []; let c = 0; for (const v of a){ c += v; ca.push(c); }
  let more = 0, tie = 0;
  for (let i = 0; i < h.length; i++){ const below = i - 1 >= 0 ? ca[i - 1] : 0; more += h[i] * below; if (i < a.length) tie += h[i] * a[i]; }
  return more + 0.5 * tie; }
function _theoChances(r){
  if (!r) return { home: 0.5, away: 0.5 };
  const key = (r.matchup || "") + "|" + (r.home_sp_name || "") + "|" + (r.away_sp_name || "");
  if (_theoMemo.has(key)) return _theoMemo.get(key);
  const homeK = _theoKpct(r.home_sp_k_pct), awayK = _theoKpct(r.away_sp_k_pct);
  const hConc = _theoNum(r.home_lineup_concentration), aConc = _theoNum(r.away_lineup_concentration);
  const hOff = isFinite(hConc) ? (hConc - 0.5) * 0.10 : 0;   // tiny, bounded inside _theoRates
  const aOff = isFinite(aConc) ? (aConc - 0.5) * 0.10 : 0;
  const homePA = _theoRates(awayK, hOff);   // home offense faces the AWAY starter
  const awayPA = _theoRates(homeK, aOff);   // away offense faces the HOME starter
  const penGap = _theoNum(r.hl_bullpen_xwoba_gap) || 0;     // +ve favors home pen
  const sims = 1200;
  let homeInn = _theoInningPMF(homePA, sims), awayInn = _theoInningPMF(awayPA, sims);
  awayInn = _theoLeverage(awayInn, Math.max(penGap, 0) * 8);    // home pen suppresses away
  homeInn = _theoLeverage(homeInn, Math.max(-penGap, 0) * 8);   // away pen suppresses home
  const wpHome = _theoWP(_theoGamePMF(homeInn), _theoGamePMF(awayInn));
  const res = { home: Math.min(Math.max(wpHome, 0.01), 0.99), away: 0 };
  res.away = 1 - res.home;
  _theoMemo.set(key, res);
  return res;
}
// ===== end Theoretical chances =============================================

"""
EDITS.append(("async function toggleNarrative(i) {",
              ENGINE + "async function toggleNarrative(i) {",
              "function _theoChances(r)"))

# --- C. Labeled placeholder card in the expanded narrative -------------------
CARD = (
    '  // ----- 1c. Theoretical chances (HYPOTHETICAL toy model) -----\n'
    '  html += `<div class="theo-card" data-theo-slot>\n'
    '    <h4>⚛ Theoretical chances <span class="theo-tag">hypothetical · not the pick</span></h4>\n'
    '    <div class="theo-body"><span class="muted">expand to compute…</span></div>\n'
    '    <p class="legend-note">An independent from-scratch toy — air-density carry, Log5 + James-Stein shrinkage, a 24 base-out-state Markov inning sim, and a 9-inning convolution — run on this game\\u2019s pitching &amp; lineup signal. Deliberately separate from the model\\u2019s pick above: a curiosity, not a betting number.</p>\n'
    '  </div>`;\n'
    '\n'
    '  // ----- 2. Pitching matchup (Stage 1) -----'
)
EDITS.append(("  // ----- 2. Pitching matchup (Stage 1) -----", CARD,
              'data-theo-slot'))

# --- D. Lazy compute + fill hook in toggleNarrative --------------------------
HOOK_OLD = (
    '    canvasEl.id = "winprob-canvas-" + i;\n'
    '    const r = (window.__slate.rows || [])[i];\n'
    '    const result = matchResult(r, window.__slate.results || {});\n'
    '    _ensureWinProbChart(i, r, result).catch(e => console.warn("chart render failed:", e));\n'
    '  }'
)
HOOK_NEW = HOOK_OLD + (
    '\n'
    '\n'
    '  // Theoretical chances (HYPOTHETICAL) — compute once per game on expand.\n'
    '  try {\n'
    '    const _ts = row.querySelector("[data-theo-slot] .theo-body");\n'
    '    if (_ts && _ts.dataset.filled !== "1") {\n'
    '      const _tr = (window.__slate.rows || [])[i];\n'
    '      const _tp = (_tr && _tr.matchup ? _tr.matchup : "").split(/\\s*@\\s*/);\n'
    '      const _tc = _theoChances(_tr);\n'
    '      _ts.innerHTML =\n'
    '        `<span class="theo-num">${(_tc.home*100).toFixed(1)}%</span> <span class="theo-side">${_tp[1]||"HOME"}</span>`\n'
    '        + `<span class="theo-vs">·</span>`\n'
    '        + `<span class="theo-num">${(_tc.away*100).toFixed(1)}%</span> <span class="theo-side">${_tp[0]||"AWAY"}</span>`;\n'
    '      _ts.dataset.filled = "1";\n'
    '    }\n'
    '  } catch (_e) { console.warn("theo chances fill failed:", _e); }'
)
EDITS.append((HOOK_OLD, HOOK_NEW, "theo chances fill failed"))


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = skipped = 0
    for old, new, sentinel in EDITS:
        if sentinel in work:
            print(f"  skip (already applied): {sentinel}")
            skipped += 1
            continue
        n = work.count(old)
        if n != 1:
            print(f"  ERROR anchor count={n} (need 1) for sentinel: {sentinel}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  applied: {sentinel}")
    if applied:
        with open(F, "w", encoding="utf-8", newline="") as fh:
            fh.write(work.replace("\n", nl))
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
