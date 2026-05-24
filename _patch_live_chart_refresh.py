#!/usr/bin/env python3
"""
_patch_live_chart_refresh.py
============================
Fix: the per-game expanded-row win-prob chart never updates during a live
game.

Root cause (two bugs):
  1. _ensureWinProbChart short-circuits on `el.dataset.rendered === "1"`,
     so once the chart is drawn it can never be re-drawn.
  2. The function only fetches actualCurve when `result.isFinal` is true.
     For live games it draws ONLY the static model curve and stops.

Fix:
  1. Add a `forceRefresh` 4th arg. When true, skip the rendered short-circuit.
  2. Allow actual-curve fetch for LIVE games too (not just final). Uses
     `_ltClassifyStatus(result.statusText)` to detect live.
  3. After each successful poll cycle in the live-tracker tick, call
     `_ensureWinProbChart(rowIndex, r, result, true)` so the chart refreshes
     with the new live win-prob data each cycle.

Per locked memory: bash + Python str.replace only.
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
    print("=== _patch_live_chart_refresh.py ===")
    print(f"input: {INDEX} ({n0} bytes)")

    # ---------- 1. Widen _ensureWinProbChart for live refresh ----------
    src = must_replace(
        src,
        'async function _ensureWinProbChart(rowIndex, r, result) {\n'
        '  const placeholderId = "winprob-canvas-" + rowIndex;\n'
        '  const el = document.getElementById(placeholderId);\n'
        '  if (!el || el.dataset.rendered === "1") return;\n'
        '  // Both f5 and full need to be the PICK\'s probability so the chart curve\n'
        '  // represents the picked team\'s win-prob trajectory across innings.\n'
        '  const f5 = _pickSideF5Prob(r);\n'
        '  const full = parseFloat(r.pick_prob || r.p_model);\n'
        '  const fair = parseFloat(r.fair_prob);\n'
        '  const modelCurve = _modelForecastCurve(f5, full, fair);\n'
        '  const picked = pickedTeam(r) || "pick";\n'
        '  let actualCurve = null;\n'
        '  if (result && result.gamePk && result.isFinal) {\n'
        '    const wp = await _fetchActualWinProb(result.gamePk);\n'
        '    if (wp) actualCurve = _actualEndOfInningProbs(wp, _pickedSideFor(r, result));\n'
        '  }\n'
        '  _renderWinProbChart(placeholderId, picked, modelCurve, actualCurve, !!(result && result.isFinal));\n'
        '  el.dataset.rendered = "1";\n'
        '}',
        'async function _ensureWinProbChart(rowIndex, r, result, forceRefresh) {\n'
        '  // 2026-05-24: live-chart refresh fix. Was short-circuiting on the\n'
        '  // first render so the chart never redrew during a live game; also\n'
        '  // only fetched the actual curve for FINAL games, so live games\n'
        '  // showed nothing but the static model dashed line.\n'
        '  // Now: forceRefresh=true bypasses the cache; live games fetch the\n'
        '  // actual curve each tick so the chart trails the in-game state.\n'
        '  const placeholderId = "winprob-canvas-" + rowIndex;\n'
        '  const el = document.getElementById(placeholderId);\n'
        '  if (!el) return;\n'
        '  if (!forceRefresh && el.dataset.rendered === "1") return;\n'
        '  // Both f5 and full need to be the PICK\'s probability so the chart curve\n'
        '  // represents the picked team\'s win-prob trajectory across innings.\n'
        '  const f5 = _pickSideF5Prob(r);\n'
        '  const full = parseFloat(r.pick_prob || r.p_model);\n'
        '  const fair = parseFloat(r.fair_prob);\n'
        '  const modelCurve = _modelForecastCurve(f5, full, fair);\n'
        '  const picked = pickedTeam(r) || "pick";\n'
        '  let actualCurve = null;\n'
        '  if (result && result.gamePk) {\n'
        '    const _liveStatus = _ltClassifyStatus(result.statusText);\n'
        '    if (result.isFinal || _liveStatus === "live") {\n'
        '      try {\n'
        '        const wp = await _fetchActualWinProb(result.gamePk);\n'
        '        if (wp) actualCurve = _actualEndOfInningProbs(wp, _pickedSideFor(r, result));\n'
        '      } catch (_e_wp) { /* silent — leave actualCurve null */ }\n'
        '    }\n'
        '  }\n'
        '  _renderWinProbChart(placeholderId, picked, modelCurve, actualCurve, !!(result && result.isFinal));\n'
        '  el.dataset.rendered = "1";\n'
        '}',
        "1: widen _ensureWinProbChart for live refresh + forceRefresh",
    )

    # ---------- 2. Re-render chart inside live-tracker tick ----------
    # After every successful poll cycle that yielded a live or final status,
    # re-draw the chart with the freshest actual-curve from /winProbability.
    src = must_replace(
        src,
        '    let curStatus;\n'
        '    try {\n'
        '      curStatus = await _ltOnePollCycle(rowIndex, result.gamePk, r, result, opts);\n'
        '      opts.errors = 0;\n'
        '    } catch (e) {\n'
        '      opts.errors += 1;\n'
        '    }\n'
        '    // Stop conditions',
        '    let curStatus;\n'
        '    try {\n'
        '      curStatus = await _ltOnePollCycle(rowIndex, result.gamePk, r, result, opts);\n'
        '      opts.errors = 0;\n'
        '      // 2026-05-24: refresh the per-row win-prob chart each cycle so\n'
        '      // the actual-trajectory curve trails the live game (was static\n'
        '      // model-only line during live play).\n'
        '      if (curStatus === "live" || curStatus === "final") {\n'
        '        try { await _ensureWinProbChart(rowIndex, r, result, true); }\n'
        '        catch (_e_chart) { /* silent — keep poller running */ }\n'
        '      }\n'
        '    } catch (e) {\n'
        '      opts.errors += 1;\n'
        '    }\n'
        '    // Stop conditions',
        "2: re-render win-prob chart inside live tick",
    )

    INDEX.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {INDEX} ({n1} bytes, delta {n1-n0:+d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
