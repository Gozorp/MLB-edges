#!/usr/bin/env python3
"""
_patch_sever_blowout.py
========================
Commit 2 of the legacy-blowout teardown sequence. Removes the chained
apply_blowout_penalties call from auto_weight_update.run() so the
gradient loop (apply_calibration_from_all_picks) becomes the sole
learning path. Side effect: the daily +/-4% gradient cap becomes a
hard invariant for the first time, since the blowout shock (-15% per
qualifying bust) is what could previously exceed it.

Why: the 28-day baseline snapshot (Commit 1) showed our losses go to
blowouts at 31.9% vs MLB baseline 30.1% — blowouts are bullpen
variance, not signal failure. Magnitude weighting injects noise.

Two edits to mlb_edge/auto_weight_update.py:

  1. The blowout call block (lines ~482-501 and the use of
     state_after_blowout at lines ~511 and ~527) is replaced with the
     simplified path: prev_state -> apply_calibration_from_all_picks
     -> new_state. The "blowout_penalty" learn_mode value is replaced
     with "no_learn" (which fires only when learn_from_all=False or
     diag_df is empty — i.e., genuine no-op slates).
  2. The default learn_mode parameter on _write_audit_entry is
     updated from "blowout_penalty" to "no_learn" to match.

Imports of apply_blowout_penalties and the blowout-specific constants
(BLOWOUT_RUN_DIFF, BLOWOUT_TIERS_PENALIZED, PENALTY_PER_BLOWOUT,
RECOVERY_PER_GOOD_DAY, MIN_RELATIVE_WEIGHT) are intentionally LEFT
alone in this commit — they become unused but harmless. They get
cleaned up in Commit 3 when recursive_weight_update.py is purged.
Smaller diff = lower risk for the load-bearing change.

Per locked memory: bash + Python str.replace; no Edit tool.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TARGET = REPO / "mlb_edge" / "auto_weight_update.py"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# ---------------------------------------------------------------------------
# Edit 1: replace the entire blowout-chain block with the simplified
# calibration-only path. Anchor on the full block from the line right
# after `prev_state = get_active_weights(SP_WEIGHTS)` through the
# learn_mode assignment.
# ---------------------------------------------------------------------------
OLD_BLOCK = '''        prev_state = get_active_weights(SP_WEIGHTS)

        if not picks_df.empty:
            picks_norm = _picks_to_recursive_schema(picks_df, audit_df)
            outcomes_norm = _outcomes_to_recursive_schema(outcomes_df, picks_df)
            if dry_run:
                _orig_state_text = None
                from .recursive_weight_update import WEIGHTS_STATE_FILE as _WSF
                if _WSF.exists():
                    _orig_state_text = _WSF.read_text(encoding="utf-8")
                state_after_blowout = apply_blowout_penalties(
                    picks_norm, outcomes_norm, SP_WEIGHTS)
                if _orig_state_text is not None:
                    _WSF.write_text(_orig_state_text, encoding="utf-8")
                else:
                    try: _WSF.unlink()
                    except FileNotFoundError: pass
            else:
                state_after_blowout = apply_blowout_penalties(
                    picks_norm, outcomes_norm, SP_WEIGHTS)
        else:
            state_after_blowout = prev_state

        n_picks_total = 0
        n_picks_used_for_learning = 0
        if learn_from_all and not diag_df.empty:
            if dry_run:
                _orig_state_text2 = None
                from .recursive_weight_update import WEIGHTS_STATE_FILE as _WSF
                if _WSF.exists():
                    _orig_state_text2 = _WSF.read_text(encoding="utf-8")
                _WSF.write_text(json.dumps(state_after_blowout, indent=2),
                                encoding="utf-8")
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
                if _orig_state_text2 is not None:
                    _WSF.write_text(_orig_state_text2, encoding="utf-8")
                else:
                    try: _WSF.unlink()
                    except FileNotFoundError: pass
            else:
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
            learn_mode = "all_picks_tier_weighted"
        else:
            new_state = state_after_blowout
            n_picks_total = int(len(diag_df)) if not diag_df.empty else 0
            learn_mode = "blowout_penalty"
'''

NEW_BLOCK = '''        prev_state = get_active_weights(SP_WEIGHTS)

        # 2026-05-26: legacy apply_blowout_penalties chain removed.
        # See data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/
        # for the evidence: our losses go to blowouts at 31.9% vs MLB
        # baseline 30.1% — blowouts are bullpen variance, not signal
        # failure. apply_calibration_from_all_picks is now the sole
        # learning path. The daily +/-4% gradient cap is now a hard
        # invariant (previously the blowout shock at -15% per bust
        # could exceed it on qualifying slates).
        n_picks_total = 0
        n_picks_used_for_learning = 0
        if learn_from_all and not diag_df.empty:
            if dry_run:
                _orig_state_text = None
                from .recursive_weight_update import WEIGHTS_STATE_FILE as _WSF
                if _WSF.exists():
                    _orig_state_text = _WSF.read_text(encoding="utf-8")
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
                if _orig_state_text is not None:
                    _WSF.write_text(_orig_state_text, encoding="utf-8")
                else:
                    try: _WSF.unlink()
                    except FileNotFoundError: pass
            else:
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
            learn_mode = "all_picks_tier_weighted"
        else:
            new_state = prev_state
            n_picks_total = int(len(diag_df)) if not diag_df.empty else 0
            learn_mode = "no_learn"
'''

must_replace(TARGET, OLD_BLOCK, NEW_BLOCK,
             "1/2: sever blowout chain in auto_weight_update.run()")


# ---------------------------------------------------------------------------
# Edit 2: update default learn_mode in _write_audit_entry signature
# from "blowout_penalty" to "no_learn". (The kwarg is always passed
# explicitly from run(), but the default should reflect the new
# enum so future call sites can't accidentally re-introduce the dead
# value.)
# ---------------------------------------------------------------------------
must_replace(
    TARGET,
    '                        learn_mode="blowout_penalty",\n',
    '                        learn_mode="no_learn",\n',
    "2/2: update default learn_mode in _write_audit_entry",
)


# ---------------------------------------------------------------------------
# Final gate: parse.
# ---------------------------------------------------------------------------
src = TARGET.read_text(encoding="utf-8")
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"[FAIL] ast.parse after patch: {e}")
    sys.exit(3)
print("[ok]   ast.parse clean")
print("[done] all 2 patches applied")
