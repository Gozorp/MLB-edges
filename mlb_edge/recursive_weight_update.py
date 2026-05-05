"""
recursive_weight_update.py — Blowout-driven recursive feature reweighting (v5.1).

After each slate, call apply_blowout_penalties(picks_df, outcomes_df,
baseline_weights). When a PLATINUM/DIAMOND bet busts in a blowout (|run_diff|
>= BLOWOUT_RUN_DIFF), every feature linked to the conviction signals that
fired gets multiplicatively penalized for the next slate. Bounded floors
prevent permanent feature death; passive recovery returns weights toward
baseline on clean slates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pandas as pd

BLOWOUT_RUN_DIFF = 5
BLOWOUT_TIERS_PENALIZED = ("PLATINUM", "DIAMOND")
PENALTY_PER_BLOWOUT = 0.85
RECOVERY_PER_GOOD_DAY = 1.05
MIN_RELATIVE_WEIGHT = 0.25
WEIGHTS_STATE_FILE = Path("data/state/weights_state.json")

SIGNAL_TO_FEATURES: Dict[str, list[str]] = {
    "F1": ["sp_xera_gap", "sp_xwoba_allowed_gap", "sp_recent_form_gap"],
    "F2": ["team_xwoba_gap", "team_wrcplus_gap"],
    "F3": ["swing_take_gap"],
    "F5": ["bullpen_siera_gap", "bullpen_xwoba_gap"],
}


def _load_state(baseline: Dict[str, float]) -> Dict[str, float]:
    """Load persisted weights, seeding any missing keys. Features that aren't
    in the SP baseline (e.g. team_xwoba_gap, swing_take_gap, bullpen gaps)
    start at 1.0 — the penalty/recovery multipliers act as scalars."""
    seeded: Dict[str, float] = {}
    for feat_list in SIGNAL_TO_FEATURES.values():
        for f in feat_list:
            seeded[f] = 1.0
    seeded.update(baseline)
    if WEIGHTS_STATE_FILE.exists():
        state = json.loads(WEIGHTS_STATE_FILE.read_text())
        for k, v in seeded.items():
            state.setdefault(k, v)
        return state
    WEIGHTS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    return seeded


def _save_state(state: Dict[str, float]) -> None:
    WEIGHTS_STATE_FILE.write_text(json.dumps(state, indent=2))


def _parse_signals(signal_str: str) -> list[str]:
    if not isinstance(signal_str, str) or not signal_str:
        return []
    import re
    tokens = re.findall(r"F\d+", signal_str)
    return [t for t in tokens if t in SIGNAL_TO_FEATURES]


def apply_blowout_penalties(
    picks_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    baseline_weights: Dict[str, float],
) -> Dict[str, float]:
    """
    picks_df    needs: game_id, conv_tier, conv_signals, pick_winner
    outcomes_df needs: game_id, home_team, away_team, home_R, away_R
    Returns updated state dict; persists to disk.
    """
    state = _load_state(baseline_weights)
    merged = picks_df.merge(outcomes_df, on="game_id", how="inner")

    blowout_features: dict[str, int] = {}
    bets_evaluated = 0

    for _, r in merged.iterrows():
        if r["conv_tier"] not in BLOWOUT_TIERS_PENALIZED:
            continue
        bets_evaluated += 1
        winner = r["home_team"] if r["home_R"] > r["away_R"] else r["away_team"]
        run_diff = abs(int(r["home_R"]) - int(r["away_R"]))
        if winner == r["pick_winner"]:
            continue
        if run_diff < BLOWOUT_RUN_DIFF:
            continue
        for sig in _parse_signals(r.get("conv_signals", "")):
            for feat in SIGNAL_TO_FEATURES[sig]:
                blowout_features[feat] = blowout_features.get(feat, 0) + 1

    for feat, n_busts in blowout_features.items():
        base = baseline_weights.get(feat, 1.0)
        floor = MIN_RELATIVE_WEIGHT * base
        state[feat] = max(
            floor, state.get(feat, base) * (PENALTY_PER_BLOWOUT ** n_busts)
        )

    if bets_evaluated > 0 and not blowout_features:
        for feat in state:
            base = baseline_weights.get(feat, 1.0)
            if state[feat] < base:
                state[feat] = min(base, state[feat] * RECOVERY_PER_GOOD_DAY)

    _save_state(state)
    return state


def get_active_weights(baseline_weights: Dict[str, float]) -> Dict[str, float]:
    """Call at start of each slate to load penalty-adjusted weights."""
    return _load_state(baseline_weights)
