#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_local_selflearn.py -- local nightly self-learn (Phase 2 self-hosting).

Mirrors self-learn.yml: awu.run(yesterday_UTC, picks_dir=docs/data). LOCAL ONLY
(no commit/push). The completeness guard inside auto_weight_update skips any
slate that is not 100% Final, so an early run safely no-ops.

NOTE (parallel-build safety): while the cloud self-learn is still live, this
writes data/state/weights_state.json LOCALLY and uncommitted. The cloud copy
stays authoritative until cutover; a `git reset --hard origin/main` discards any
local divergence. Optional arg: YYYY-MM-DD (else yesterday UTC).
"""
import sys
import os
from datetime import date, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from mlb_edge import auto_weight_update as awu
from tools.slate_date import slate_today

arg = (sys.argv[1].strip() if len(sys.argv) > 1 else "")
d = date.fromisoformat(arg) if arg else (slate_today() - timedelta(days=1))
print("[self-learn] target slate_date =", d)
awu.run(d, picks_dir=Path("docs/data"))
print("[self-learn] done")
