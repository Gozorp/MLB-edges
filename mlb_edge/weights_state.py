"""
weights_state.py — disk-backed weights for the self-learning loop.

Pure state I/O. Renamed and stripped from the legacy
recursive_weight_update.py on 2026-05-26 when apply_blowout_penalties
was retired (see data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/
for the evidence behind that decision). What remains is the
load/save/parse helpers that auto_weight_update.apply_calibration_from_all_picks
and edge_calculator both still need.

Public surface:
  WEIGHTS_STATE_FILE   on-disk path
  SIGNAL_TO_FEATURES   F1..F5 conviction-signal -> feature-name mapping
  _load_state          read weights JSON (seeds missing keys)
  _save_state          write weights JSON
  _parse_signals       extract F\d+ tokens from a signal string
  get_active_weights   public alias for _load_state, used by edge_calculator
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

WEIGHTS_STATE_FILE = Path("data/state/weights_state.json")

# Conviction-signal -> feature-name groups. Used by the calibration
# loop's per-pick gradient when routing a pick's `signals` field to
# the features that should be nudged. Unchanged from the legacy
# recursive_weight_update.py — these groupings predate the gradient
# loop and are still semantically correct.
SIGNAL_TO_FEATURES: Dict[str, List[str]] = {
    "F1": ["sp_xera_gap", "sp_xwoba_allowed_gap", "sp_recent_form_gap"],
    "F2": ["team_xwoba_gap", "team_wrcplus_gap"],
    "F3": ["swing_take_gap"],
    "F5": ["bullpen_siera_gap", "bullpen_xwoba_gap"],
}

_SIGNAL_RE = re.compile(r"F\d+")


def _load_state(baseline: Dict[str, float]) -> Dict[str, float]:
    """Load persisted weights, seeding any missing keys. Features that
    aren't in the SP baseline (team_*, swing_take_gap, bullpen_*) start
    at 1.0 — the gradient multipliers act as scalars on top.
    """
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


def _parse_signals(signal_str: str) -> List[str]:
    if not isinstance(signal_str, str) or not signal_str:
        return []
    return [t for t in _SIGNAL_RE.findall(signal_str)
            if t in SIGNAL_TO_FEATURES]


def get_active_weights(baseline_weights: Dict[str, float]) -> Dict[str, float]:
    """Public alias for _load_state. Call at the start of each slate
    to read the current weights state from disk."""
    return _load_state(baseline_weights)
