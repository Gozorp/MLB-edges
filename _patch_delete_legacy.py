#!/usr/bin/env python3
"""
_patch_delete_legacy.py
========================
Commit 3 of the legacy-blowout teardown sequence. Purges
recursive_weight_update.py and the update-weights CLI subcommand;
re-points the remaining state-IO references at the new
mlb_edge/weights_state.py (created out-of-band, committed
alongside this patch).

Edits (5 files):

  1. mlb_edge/auto_weight_update.py
       - Replace the bloated import block from .recursive_weight_update
         with a tight 1-line import from .weights_state.
       - Drop the stale module-level docstring reference to
         recursive_weight_update.apply_blowout_penalties.
       - Replace the broken try/except _BASELINES import (the inner
         import raised silently for the entire history of this code
         because recursive_weight_update.py never defined SP_WEIGHTS)
         with a direct reference to the module-level SP_WEIGHTS from
         .config. Side effect: weights_growing_past_prior +
         runaway_alarm now actually fire when conditions are met,
         instead of being dead observability.
       - Update the inline import of WEIGHTS_STATE_FILE to point at
         .weights_state.

  2. mlb_edge/edge_calculator.py
       - Re-point the get_active_weights import.

  3. mlb_edge/main.py
       - Drop the import of apply_blowout_penalties.
       - Remove "update-weights" from the CLI choices.
       - Remove the --picks / --outcomes args (update-weights specific).
       - Remove the elif branch in main() dispatch.
       - Delete _normalize_picks_csv, _normalize_outcomes_csv,
         run_update_weights (all dead after the elif goes).

  4. mlb_edge/recursive_weight_update.py
       - Deleted by the .bat via `git rm`.

  5. mlb_edge/weights_state.py
       - Already on disk (committed alongside this patch); only the
         git-add happens in the .bat.

Per locked memory: bash + Python str.replace; no Edit tool.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
AWU = REPO / "mlb_edge" / "auto_weight_update.py"
EDGE = REPO / "mlb_edge" / "edge_calculator.py"
MAIN = REPO / "mlb_edge" / "main.py"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# ===========================================================================
# 1. auto_weight_update.py
# ===========================================================================

# 1a. Replace the bloated import block.
must_replace(
    AWU,
    'from .recursive_weight_update import (\n'
    '    BLOWOUT_RUN_DIFF, BLOWOUT_TIERS_PENALIZED,\n'
    '    PENALTY_PER_BLOWOUT, RECOVERY_PER_GOOD_DAY,\n'
    '    SIGNAL_TO_FEATURES,\n'
    '    apply_blowout_penalties, get_active_weights,\n'
    '    _load_state, _save_state, _parse_signals,\n'
    '    MIN_RELATIVE_WEIGHT,\n'
    ')\n',
    'from .weights_state import (\n'
    '    SIGNAL_TO_FEATURES, WEIGHTS_STATE_FILE,\n'
    '    get_active_weights, _load_state, _save_state, _parse_signals,\n'
    ')\n',
    "1a/5: auto_weight_update.py — tighten imports to .weights_state",
)

# 1b. Drop stale docstring reference to apply_blowout_penalties.
must_replace(
    AWU,
    '    4. Reuse `recursive_weight_update.apply_blowout_penalties` to update the\n',
    '    4. Call apply_calibration_from_all_picks to symmetrically update the\n',
    "1b/5: auto_weight_update.py — fix module docstring",
)

# 1c. Replace the broken try/except _BASELINES import with the
# real, module-level SP_WEIGHTS from .config (already imported
# at line 39). The previous try/except always raised because
# recursive_weight_update.py never defined SP_WEIGHTS, which
# made the runaway_alarm + weights_growing_past_prior fields
# dead-on-arrival observability for the entire life of the
# audit log. This fixes that latent bug as a side effect.
must_replace(
    AWU,
    '    try:\n'
    '        from .recursive_weight_update import SP_WEIGHTS as _BASELINES\n'
    '    except Exception:\n'
    '        _BASELINES = {}\n',
    '    # 2026-05-26: was a try/except import from recursive_weight_update\n'
    '    # which always raised (recursive_weight_update never defined\n'
    '    # SP_WEIGHTS). Direct reference to the already-imported config\n'
    '    # constant makes the safeguard fields actually populate.\n'
    '    _BASELINES = SP_WEIGHTS\n',
    "1c/5: auto_weight_update.py — fix latent _BASELINES bug",
)

# 1d. Update inline WEIGHTS_STATE_FILE import — was inline because
# the variable name collided with something locally; now we already
# import WEIGHTS_STATE_FILE at module top via 1a, so the inline
# import becomes unnecessary. Just drop it and let `_WSF` alias to
# the module-level name.
must_replace(
    AWU,
    '            if dry_run:\n'
    '                _orig_state_text = None\n'
    '                from .recursive_weight_update import WEIGHTS_STATE_FILE as _WSF\n'
    '                if _WSF.exists():\n',
    '            if dry_run:\n'
    '                _orig_state_text = None\n'
    '                _WSF = WEIGHTS_STATE_FILE\n'
    '                if _WSF.exists():\n',
    "1d/5: auto_weight_update.py — drop inline import of WEIGHTS_STATE_FILE",
)


# ===========================================================================
# 2. edge_calculator.py
# ===========================================================================
must_replace(
    EDGE,
    'from .recursive_weight_update import get_active_weights\n',
    'from .weights_state import get_active_weights\n',
    "2a/5: edge_calculator.py — re-point get_active_weights import",
)

# 2b. Refresh the docstring that still references recursive_weight_update.
must_replace(
    EDGE,
    '    """v5.1 recursive penalty multiplier on the F1 raw signal. After a\n'
    '    PLATINUM blowout, recursive_weight_update reduces sp_xera_gap\'s stored\n'
    '    weight; we read that here and divide by the baseline so the effective\n'
    '    xera_gap a slate sees is its raw value times the (penalized/baseline)\n'
    '    ratio. On a clean baseline this is exactly 1.0 — a no-op."""\n',
    '    """Learned-weight multiplier on the F1 raw signal. The symmetric\n'
    '    gradient loop in auto_weight_update.apply_calibration_from_all_picks\n'
    '    nudges sp_xera_gap up or down per-slate; we read its current value\n'
    '    here and divide by the baseline so the effective xera_gap a slate\n'
    '    sees is its raw value times the (learned/baseline) ratio. On a\n'
    '    clean baseline this is exactly 1.0 — a no-op."""\n',
    "2b/5: edge_calculator.py — refresh stale docstring",
)


# ===========================================================================
# 3. main.py — gut the update-weights CLI surface
# ===========================================================================

# 3a. Drop the import.
must_replace(
    MAIN,
    'from .recursive_weight_update import apply_blowout_penalties\n',
    '',
    "3a/5: main.py — drop apply_blowout_penalties import",
)

# 3b. Drop "update-weights" from CLI choices.
must_replace(
    MAIN,
    '        choices=["backtest", "train", "predict", "update-weights"],\n',
    '        choices=["backtest", "train", "predict"],\n',
    "3b/5: main.py — drop update-weights from CLI choices",
)

# 3c. Drop --picks / --outcomes args (update-weights specific).
must_replace(
    MAIN,
    '    p.add_argument("--picks", help="Picks CSV (update-weights mode)")\n'
    '    p.add_argument("--outcomes", help="Outcomes CSV (update-weights mode)")\n',
    '',
    "3c/5: main.py — drop --picks / --outcomes args",
)

# 3d. Drop the elif branch in main() dispatch.
must_replace(
    MAIN,
    '    elif args.mode == "update-weights":\n'
    '        if not args.picks or not args.outcomes:\n'
    '            sys.exit("--picks and --outcomes required for update-weights")\n'
    '        run_update_weights(args.picks, args.outcomes)\n',
    '',
    "3d/5: main.py — drop update-weights dispatch branch",
)

# 3e. Drop the three dead helper functions.
must_replace(
    MAIN,
    'def _normalize_picks_csv(df: pd.DataFrame) -> pd.DataFrame:\n'
    '    """Adapt the pipeline\'s picks CSV (team/tier/signals) to the schema\n'
    '    apply_blowout_penalties expects (pick_winner/conv_tier/conv_signals)."""\n'
    '    out = df.copy()\n'
    '    rename = {"team": "pick_winner", "tier": "conv_tier", "signals": "conv_signals"}\n'
    '    for src, dst in rename.items():\n'
    '        if src in out.columns and dst not in out.columns:\n'
    '            out = out.rename(columns={src: dst})\n'
    '    if "conv_signals" not in out.columns:\n'
    '        out["conv_signals"] = ""\n'
    '    return out[["game_id", "conv_tier", "conv_signals", "pick_winner"]]\n'
    '\n'
    '\n'
    'def _normalize_outcomes_csv(df: pd.DataFrame) -> pd.DataFrame:\n'
    '    """Adapt the pipeline\'s outcomes CSV (team/side/winner/result) to\n'
    '    home_team/away_team/home_R/away_R. When run-totals are absent, synthesize\n'
    '    them from `winner` so the function\'s win/loss check is correct (run_diff\n'
    '    is only consulted on losses, where W vs blowout-loss is determined)."""\n'
    '    out = df.copy()\n'
    '    if {"home_team", "away_team", "home_R", "away_R"}.issubset(out.columns):\n'
    '        return out[["game_id", "home_team", "away_team", "home_R", "away_R"]]\n'
    '    rows = []\n'
    '    for _, r in out.iterrows():\n'
    '        bet_team = r.get("team")\n'
    '        side = r.get("side", "home")\n'
    '        winner = r.get("winner", bet_team)\n'
    '        result = str(r.get("result", "")).upper()\n'
    '        run_diff = abs(int(r.get("run_diff", 1))) if "run_diff" in r else 1\n'
    '        if side == "home":\n'
    '            home_team, away_team = bet_team, "OPP"\n'
    '        else:\n'
    '            home_team, away_team = "OPP", bet_team\n'
    '        won = (winner == bet_team) or result == "W"\n'
    '        if won:\n'
    '            home_R, away_R = (run_diff + 1, 1) if side == "home" else (1, run_diff + 1)\n'
    '        else:\n'
    '            home_R, away_R = (1, run_diff + 1) if side == "home" else (run_diff + 1, 1)\n'
    '        rows.append({"game_id": r["game_id"], "home_team": home_team,\n'
    '                     "away_team": away_team, "home_R": home_R, "away_R": away_R})\n'
    '    return pd.DataFrame(rows)\n'
    '\n'
    '\n'
    'def run_update_weights(picks_csv: str, outcomes_csv: str) -> None:\n'
    '    """v5.1 post-slate: blowout-driven recursive weight update."""\n'
    '    log.info("=== UPDATE-WEIGHTS: picks=%s outcomes=%s ===", picks_csv, outcomes_csv)\n'
    '    picks = _normalize_picks_csv(pd.read_csv(picks_csv))\n'
    '    outcomes = _normalize_outcomes_csv(pd.read_csv(outcomes_csv))\n'
    '    new_state = apply_blowout_penalties(picks, outcomes, baseline_weights=SP_WEIGHTS)\n'
    '    print("\\n=== UPDATED WEIGHTS ===")\n'
    '    for k, v in new_state.items():\n'
    '        base = SP_WEIGHTS.get(k)\n'
    '        delta = f" ({v / base:.2%} of baseline)" if base else ""\n'
    '        print(f"  {k}: {v:.4f}{delta}")\n'
    '\n'
    '\n',
    '',
    "3e/5: main.py — delete _normalize_*_csv + run_update_weights",
)


# ===========================================================================
# Final gate: parse all three patched files.
# ===========================================================================
for label, p in [("auto_weight_update.py", AWU),
                 ("edge_calculator.py", EDGE),
                 ("main.py", MAIN)]:
    try:
        ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"[FAIL] ast.parse {label}: {e}")
        sys.exit(3)
    print(f"[ok]   ast.parse {label}")

print("[done] all patches applied")
print("[note] recursive_weight_update.py removal is handled by the .bat "
      "via `git rm`")
