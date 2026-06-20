# T-30 per-game refresh + lock — feature branch (`feat/t30-rolling-scheduler`)

**Status:** built on this branch ONLY. **Not on `main`. Not scheduled. Shadow-output only.**
Production stays on the stable daily/midday cron through the unattended trip. Decision (2026-06-18, with user): do not push to production before departure; branch it, leave main alone, enable + monitor on return.

## What it does
Refreshes and **locks each game ~30 minutes before its own first pitch**, and once a game is locked, no later refresh can change it (the "crucial rule"). Picks still come from the **frozen** model — this only changes *when* a pick is frozen and adds a per-game refresh cadence.

- `tools/t30_watch.py` — run every ~10–15 min by a scheduler (when enabled). Each tick:
  1. pulls the schedule → per-game first pitch (`gameDate`) + status,
  2. loads the persistent lock store `data/state/t30_locks_<date>.json`,
  3. reads the freshest diag (with `--rebuild`, re-runs the frozen slate first so the locked value is a true T-30 refresh),
  4. locks any game now within `T-30` (or Live/Final) that has a real pick — **snapshot is immutable**,
  5. writes a locked-merged **shadow** diag to `offline_t30/picks_<date>_diag_LOCKED.csv` (locked games = locked snapshot; others = fresh). **Never writes `docs/data`.**

"Exactly 30 min" = the first tick at/after T-30 (cron granularity); the lock is exact and immutable.

## Relationship to the existing lock
`main_predict._apply_started_game_lock()` already freezes picks at **first pitch** (Live/Final). This branch moves the freeze earlier to **T-30** and adds the per-game refresh. On return, the chosen path is either (a) keep this as an overlay watcher, or (b) fold the T-30 trigger into `_apply_started_game_lock` (change `started` → `now >= first_pitch - 30min`) and publish the merged diag.

## Safety / guards
Single-instance lock, game-hours window, writes only `offline_t30/` + `data/state/`, no `docs/data` writes, no model change, no git actions. Fully reversible: `git branch -D feat/t30-rolling-scheduler`.

## Enable on return (after monitoring)
1. Run the box on this branch (or merge to main) and let `t30_watch.py --rebuild` run for a day in shadow; inspect `offline_t30/` + `data/state/t30_locks_*.json`.
2. To actually drive the published slate, point publish at the LOCKED diag (or fold the T-30 trigger into `_apply_started_game_lock`).
3. Register the schedule via `SETUP_T30_WATCH.bat` (every 15 min, 08:00–23:30).

## Test (offline, safe)
`python tools/t30_watch.py 2026-06-18 --dry`  → reports which games would lock; writes nothing.
