#!/usr/bin/env python3
"""
feature_adaptive_poll.py
------------------------
Make the dashboard's slate poll ADAPTIVE instead of a fixed 3-minute setInterval:
  - 30s   when any game is live or within ~2.5h of first pitch (the announcement
          + lineup-confirmation + in-game window) -> near-real-time pre-game.
  - 3min  when there are games today but still hours out.
  - 20min in the overnight / all-final dead zones -> near-zero idle requests.

Recomputed every cycle from the slate's first-pitch times + live statuses, both
of which silentRefresh already refreshes (it re-fetches fetchMLBResults each
tick). Stays purely client-side / serverless.

Two edits to docs/index.html:
  1. fetchMLBResults: capture gameDate (ISO first-pitch UTC) on each result.
  2. startPolling/stopPolling: add _adaptivePollMs + a self-rescheduling
     setTimeout loop in place of the fixed setInterval.

Idempotent. Run from the repo root.
"""
import sys

IDX = "docs/index.html"
EDITS = []

# --- Edit 1: capture first-pitch time in the results entry ------------------
EDITS.append((
    '        isFinal, statusText: status.detailedState || "",\n'
    '        gamePk: g.gamePk,',
    '        isFinal, statusText: status.detailedState || "",\n'
    '        gameDate: g.gameDate || null,          // ISO first-pitch UTC -- drives adaptive poll cadence\n'
    '        gamePk: g.gamePk,',
    'gameDate: g.gameDate || null,'))

# --- Edit 2: adaptive cadence + self-rescheduling loop ----------------------
EDITS.append((
    'function startPolling(date) {\n'
    '  stopPolling();\n'
    '  if (date !== todayISO()) return;          // only poll for today\n'
    '  window.__poll.timer = setInterval(() => {\n'
    '    silentRefresh(date);\n'
    '  }, window.__poll.intervalMs);\n'
    '}\n'
    '\n'
    'function stopPolling() {\n'
    '  if (window.__poll.timer) {\n'
    '    clearInterval(window.__poll.timer);\n'
    '    window.__poll.timer = null;\n'
    '  }\n'
    '}',
    '// Adaptive poll cadence: aggressive near first pitch / during games,\n'
    '// relaxed in the overnight & post-game dead zones. Recomputed every cycle\n'
    '// from the slate first-pitch times + live statuses (both refreshed by\n'
    '// silentRefresh, which re-fetches fetchMLBResults each tick).\n'
    'const POLL_AGGRESSIVE_MS = 30 * 1000;        // 30s  -- live, or within ~2.5h of first pitch\n'
    'const POLL_ACTIVE_MS     = 3 * 60 * 1000;    // 3min -- games today but still hours out\n'
    'const POLL_RELAXED_MS    = 20 * 60 * 1000;   // 20min -- overnight / everything final\n'
    '\n'
    'function _adaptivePollMs(results, now) {\n'
    '  const games = results ? Object.values(results) : [];\n'
    '  if (!games.length) return POLL_RELAXED_MS;\n'
    '  let aggressive = false, active = false;\n'
    '  for (const g of games) {\n'
    '    if (/progress|warmup|pre-?game|delayed/i.test(g.statusText || "")) { aggressive = true; continue; }\n'
    '    if (g.isFinal) continue;                       // done -- contributes nothing\n'
    '    const fp = g.gameDate ? Date.parse(g.gameDate) : NaN;\n'
    '    if (isFinite(fp)) {\n'
    '      const mins = (fp - now) / 60000;\n'
    '      if (mins <= 150 && mins >= -240) aggressive = true;   // ~2.5h before -> ~4h after first pitch\n'
    '      else if (mins > 150)             active = true;       // scheduled later today\n'
    '    } else {\n'
    '      active = true;                                 // scheduled, time unknown\n'
    '    }\n'
    '  }\n'
    '  return aggressive ? POLL_AGGRESSIVE_MS : active ? POLL_ACTIVE_MS : POLL_RELAXED_MS;\n'
    '}\n'
    '\n'
    'function _pollTick(date) {\n'
    '  silentRefresh(date).finally(() => {\n'
    '    if (date !== todayISO()) { stopPolling(); return; }     // rolled past midnight\n'
    '    const ms = _adaptivePollMs(window.__slate && window.__slate.results, Date.now());\n'
    '    window.__poll.intervalMs = ms;\n'
    '    window.__poll.timer = setTimeout(() => _pollTick(date), ms);\n'
    '  });\n'
    '}\n'
    '\n'
    'function startPolling(date) {\n'
    '  stopPolling();\n'
    '  if (date !== todayISO()) return;          // only poll for today\n'
    '  const ms = _adaptivePollMs(window.__slate && window.__slate.results, Date.now());\n'
    '  window.__poll.intervalMs = ms;\n'
    '  window.__poll.timer = setTimeout(() => _pollTick(date), ms);\n'
    '}\n'
    '\n'
    'function stopPolling() {\n'
    '  if (window.__poll.timer) {\n'
    '    clearTimeout(window.__poll.timer);\n'
    '    window.__poll.timer = null;\n'
    '  }\n'
    '}',
    'function _adaptivePollMs(results, now) {'))


def _read(p):
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(p, t):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def main():
    applied = skipped = 0
    for old, new, mark in EDITS:
        raw = _read(IDX)
        nl = "\r\n" if "\r\n" in raw else "\n"
        work = raw.replace("\r\n", "\n")
        if mark in work:
            print(f"  skip (already applied): {mark[:46]}")
            skipped += 1
            continue
        if work.count(old) != 1:
            print(f"  ERROR anchor count={work.count(old)} (need 1): {mark[:46]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        _write(IDX, work.replace("\n", nl))
        applied += 1
        print(f"  applied: {mark[:46]}")
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
