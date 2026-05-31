#!/usr/bin/env python3
"""
feature_selflearn_completeness_guard.py
---------------------------------------
Two coupled edits so the daily self-learn can run TWICE a day (00:00 + 18:00
UTC) without ever learning an unfinished slate:

  1. mlb_edge/auto_weight_update.py
     - add `_slate_completion(target_date)` -> (total_games, terminal_games)
       from the statsapi schedule.
     - add a COMPLETENESS GUARD at the top of run()'s locked section: if the
       slate has games and they are not ALL in a terminal state, return WITHOUT
       writing an audit entry. Because the date stays unprocessed, a later run
       retries and learns the complete slate. Prevents the 00:00 UTC run from
       grading only the day games that happen to be final by midnight (which,
       via the slate-date dedupe, would then block the full 18:00 learn and bias
       the weights toward day games). `force` (manual dispatch) bypasses it.

  2. .github/workflows/self-learn.yml
     - cron "30 13 * * *"  ->  "0 0,18 * * *"  (00:00 + 18:00 UTC).

Idempotent: each edit is skipped if its marker is already present. Run from the
repo root. Exits non-zero if any anchor fails to match exactly once.
"""
import sys

PY = "mlb_edge/auto_weight_update.py"
YML = ".github/workflows/self-learn.yml"

# (path, old_anchor, new_text, idempotency_marker)
EDITS = []

# --- Edit 1: _slate_completion helper, inserted just before def run() --------
EDITS.append((
    PY,
    "    return False\n"
    "\n"
    "\n"
    "def run(target_date,",
    "    return False\n"
    "\n"
    "\n"
    "_TERMINAL_GAME_STATES = {\n"
    "    # Played to completion\n"
    '    "Final", "Game Over", "Completed Early",\n'
    "    # Won't be played / produce a same-day final -> must not block a learn\n"
    '    "Postponed", "Cancelled", "Canceled", "Suspended", "Forfeit",\n'
    "}\n"
    "\n"
    "\n"
    "def _slate_completion(target_date):\n"
    '    """(total_games, terminal_games) for the slate schedule. The slate is\n'
    "    'complete' when every scheduled game is in a terminal state. Fail-open:\n"
    "    on any fetch error return (0, 0) so the caller does NOT block the learn.\"\"\"\n"
    "    try:\n"
    "        r = requests.get(\n"
    "            SCHEDULE_URL,\n"
    '            params={"sportId": 1, "date": target_date.isoformat()},\n'
    "            timeout=20,\n"
    "        )\n"
    "        r.raise_for_status()\n"
    "        data = r.json()\n"
    "    except Exception as e:\n"
    '        log.warning("Slate-completion fetch failed for %s: %s -- proceeding", target_date, e)\n'
    "        return (0, 0)\n"
    "    total = done = 0\n"
    '    for d in data.get("dates", []):\n'
    '        for g in d.get("games", []):\n'
    "            total += 1\n"
    '            state = (g.get("status", {}) or {}).get("detailedState", "")\n'
    "            if state in _TERMINAL_GAME_STATES:\n"
    "                done += 1\n"
    "    return (total, done)\n"
    "\n"
    "\n"
    "def run(target_date,",
    "def _slate_completion(target_date):",
))

# --- Edit 2: completeness guard at the top of run()'s locked section ---------
EDITS.append((
    PY,
    "            return get_active_weights(SP_WEIGHTS)\n"
    "\n"
    "        picks_path = picks_dir / PICKS_GLOB.format(date=target_date.isoformat())",
    "            return get_active_weights(SP_WEIGHTS)\n"
    "\n"
    "        # Completeness guard: never learn a slate whose games are not all\n"
    "        # final yet (e.g. a 00:00 UTC run firing while the prior day's\n"
    "        # west-coast night games are still in progress). Returning WITHOUT\n"
    "        # writing an audit entry leaves the date unprocessed, so a later run\n"
    "        # (e.g. 18:00 UTC) retries and learns the COMPLETE slate -- avoids a\n"
    "        # biased partial learn. force (manual dispatch) bypasses the guard.\n"
    "        if not force:\n"
    "            sched_total, sched_done = _slate_completion(target_date)\n"
    "            if sched_total > 0 and sched_done < sched_total:\n"
    '                log.info("Slate %s not all final (%d/%d games done) -- '
    'skipping; a later run will retry", target_date, sched_done, sched_total)\n'
    "                return get_active_weights(SP_WEIGHTS)\n"
    "\n"
    "        picks_path = picks_dir / PICKS_GLOB.format(date=target_date.isoformat())",
    "# Completeness guard: never learn a slate whose games are not all",
))

# --- Edit 3: cron 13:30 daily -> 00:00 + 18:00 UTC twice daily ---------------
EDITS.append((
    YML,
    '    - cron: "30 13 * * *"   # 13:30 UTC (~6:30am PT) -- prior-day games final + diag baked',
    '    - cron: "0 0,18 * * *"   # 00:00 + 18:00 UTC. auto_weight_update\'s completeness guard skips any slate not 100% Final, so the 00:00 run no-ops while the prior night\'s west-coast games are still playing and 18:00 does the learn (00:00 only learns on rare all-day-game slates already final by midnight).',
    '- cron: "0 0,18 * * *"',
))


def _read(p):
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(p, t):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def main():
    applied = skipped = 0
    for path, old, new, marker in EDITS:
        raw = _read(path)
        nl = "\r\n" if "\r\n" in raw else "\n"
        work = raw.replace("\r\n", "\n")
        if marker in work:
            print(f"  skip (already applied) [{path}]: {marker[:50]}")
            skipped += 1
            continue
        n = work.count(old)
        if n != 1:
            print(f"  ERROR anchor count={n} (need 1) [{path}]: {marker[:50]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        _write(path, work.replace("\n", nl))
        applied += 1
        print(f"  applied [{path}]: {marker[:50]}")
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
