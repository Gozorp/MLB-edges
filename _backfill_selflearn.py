#!/usr/bin/env python3
"""
_backfill_selflearn.py
======================
One-shot backfill: run auto_weight_update.run(force=True) for each
missing date 2026-05-06 .. 2026-05-24 so the recalibration log
accumulates 19 days of all_picks_tier_weighted entries that the
ephemeral runner FS threw away.
"""
from datetime import date, timedelta
from mlb_edge import auto_weight_update as awu
from pathlib import Path

START = date(2026, 5, 6)
END   = date(2026, 5, 24)

log_path = Path("data/state/recalibration_log.jsonl")
state_path = Path("data/state/weights_state.json")
before_lines = len(log_path.read_text().splitlines()) if log_path.exists() else 0
print(f"=== BACKFILL START ===")
print(f"recalibration_log.jsonl: {before_lines} lines before")
print(f"weights_state.json present: {state_path.exists()}")
print()

d = START
ok_count = 0
fail_count = 0
while d <= END:
    print(f"--- {d} ---", flush=True)
    try:
        awu.run(d, force=True)
        ok_count += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        fail_count += 1
    d += timedelta(days=1)

after_lines = len(log_path.read_text().splitlines()) if log_path.exists() else 0
print()
print(f"=== BACKFILL DONE ===")
print(f"ok: {ok_count}, fail: {fail_count}")
print(f"recalibration_log.jsonl: {before_lines} -> {after_lines} lines (+{after_lines - before_lines})")
if state_path.exists():
    print(f"weights_state.json size: {state_path.stat().st_size} bytes")
