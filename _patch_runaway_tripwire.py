#!/usr/bin/env python3
"""
_patch_runaway_tripwire.py
==========================
One-line tripwire in audit entries: runaway_ceiling_alarm = True iff
any weight's new value >= 1.4 * its baseline. The new ceiling is
1.5 * base, so a 1.4x trigger gives us ~10% headroom — far enough
above normal upward drift to never false-alarm, close enough to the
ceiling to catch a signal-stacking runaway before it caps out.
"""
from __future__ import annotations
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "mlb_edge" / "auto_weight_update.py"


def must_replace(src: str, old: str, new: str, label: str = "") -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    n0 = len(src)

    # Augment the entry dict with runaway_ceiling_alarm
    src = must_replace(
        src,
        '    max_change_pct = 0.0\n'
        '    growing_past_prior: List[str] = []\n'
        '    for k, d in deltas.items():\n'
        '        prev_v = prev_state.get(k, 1.0)\n'
        '        if prev_v:\n'
        '            pct = abs(d) / abs(prev_v)\n'
        '            if pct > max_change_pct:\n'
        '                max_change_pct = pct\n'
        '        new_v = new_state.get(k, prev_v)\n'
        '        base_v = _BASELINES.get(k)\n'
        '        if base_v is not None and new_v > base_v:\n'
        '            growing_past_prior.append(k)\n',
        '    max_change_pct = 0.0\n'
        '    growing_past_prior: List[str] = []\n'
        '    runaway_alarm = False\n'
        '    runaway_features: List[str] = []\n'
        '    for k, d in deltas.items():\n'
        '        prev_v = prev_state.get(k, 1.0)\n'
        '        if prev_v:\n'
        '            pct = abs(d) / abs(prev_v)\n'
        '            if pct > max_change_pct:\n'
        '                max_change_pct = pct\n'
        '        new_v = new_state.get(k, prev_v)\n'
        '        base_v = _BASELINES.get(k)\n'
        '        if base_v is not None and new_v > base_v:\n'
        '            growing_past_prior.append(k)\n'
        '        # Runaway tripwire (2026-05-25): any weight >= 1.4 * base\n'
        '        # is 10pp from the new 1.5 * base ceiling and signals\n'
        '        # potential signal-stacking that magnitude weighting\n'
        '        # (Phase 4) would address. Flag it loudly.\n'
        '        if base_v is not None and new_v >= 1.4 * base_v:\n'
        '            runaway_alarm = True\n'
        '            runaway_features.append(k)\n',
        "add runaway alarm computation",
    )

    src = must_replace(
        src,
        '        "max_weight_change_pct": round(max_change_pct, 6),\n'
        '        "weights_growing_past_prior": growing_past_prior,\n',
        '        "max_weight_change_pct": round(max_change_pct, 6),\n'
        '        "weights_growing_past_prior": growing_past_prior,\n'
        '        "runaway_ceiling_alarm": runaway_alarm,\n'
        '        "runaway_features": runaway_features,\n',
        "add runaway fields to entry dict",
    )

    # Also log a warning when the alarm fires so the workflow logs surface it
    src = must_replace(
        src,
        '    log.info("Wrote audit entry for %s (mode=%s, n_bets=%d, wins=%d)",\n'
        '             target_date, learn_mode, n_bets, wins)\n',
        '    log.info("Wrote audit entry for %s (mode=%s, n_bets=%d, wins=%d)",\n'
        '             target_date, learn_mode, n_bets, wins)\n'
        '    if runaway_alarm:\n'
        '        log.warning(\n'
        '            "[runaway-ceiling-alarm] %s: weights >= 1.4 * base: %s",\n'
        '            target_date, runaway_features,\n'
        '        )\n',
        "log a warning when alarm fires",
    )

    TARGET.write_text(src, encoding="utf-8")
    print(f"output: {TARGET} ({len(src)} bytes, delta {len(src)-n0:+d})")

    import ast
    ast.parse(src)
    print("[ok] AST parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
