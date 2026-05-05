"""
Minimal smoke tests. Run with:
    python -m pytest tests/ -q
or:
    python tests/test_smoke.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from mlb_edge.edge_calculator import (
    american_to_decimal,
    expected_value,
    kelly_stake,
    score_conviction,
)
from mlb_edge.market_analysis import american_to_implied, shin, devig_two_way


def test_odds_conversions():
    # -110 in American odds ≈ 52.38% implied probability
    assert abs(american_to_implied(-110) - 0.5238) < 1e-3
    # +100 ≈ 50%
    assert abs(american_to_implied(100) - 0.5) < 1e-6
    # Decimal: -110 -> 1.909; +150 -> 2.50
    assert abs(american_to_decimal(-110) - 1.909) < 1e-2
    assert abs(american_to_decimal(150) - 2.50) < 1e-6


def test_devig_sums_to_one():
    a, b = devig_two_way(0.55, 0.50)
    assert abs(a + b - 1.0) < 1e-9
    c, d = shin(0.55, 0.50)
    assert abs(c + d - 1.0) < 1e-6


def test_ev_math():
    # At fair odds, EV should be zero.
    # prob=0.5, decimal=2.0 -> EV = 0.5*1 - 0.5 = 0
    assert abs(expected_value(0.5, 2.0)) < 1e-9
    # prob=0.55 at decimal=2.0 -> EV = 0.55*1 - 0.45 = 0.10
    assert abs(expected_value(0.55, 2.0) - 0.10) < 1e-9
    # prob=0.60 at decimal=1.91 (-110) -> EV = 0.60*0.91 - 0.40 = 0.146
    assert abs(expected_value(0.60, 1.91) - 0.146) < 1e-3


def test_kelly_nonneg():
    # At fair odds, Kelly returns 0 (no edge).
    assert kelly_stake(0.5, 2.0) == 0.0
    # With edge, positive.
    s = kelly_stake(0.55, 2.0, fraction=1.0)
    assert s > 0
    # Clamped at max_stake
    assert kelly_stake(0.90, 10.0, fraction=1.0, max_stake=0.05) == 0.05


def test_conviction_skip_on_no_signals():
    r = pd.Series({
        "sp_xera_gap": 0.1,         # below threshold
        "team_woba_gap": 0.005,     # below
        "swing_take_gap": 5.0,      # below
        "home_sp_luck": 0.0,
        "away_sp_luck": 0.0,
    })
    result = score_conviction(r)
    assert result.tier == "SKIP"


def test_conviction_diamond_on_three_signals():
    r = pd.Series({
        "sp_xera_gap": 1.5,
        "team_woba_gap": 0.040,
        "swing_take_gap": 30.0,
        "home_sp_luck": 0.0,
        "away_sp_luck": 0.0,
    })
    result = score_conviction(r)
    assert result.tier == "DIAMOND"
    assert result.primary_score >= 3


def test_conviction_gold_on_luck_only():
    r = pd.Series({
        "sp_xera_gap": 0.2,
        "team_woba_gap": 0.005,
        "swing_take_gap": 3.0,
        "home_sp_luck": 1.5,      # our SP ERA >> xERA, due to improve
        "away_sp_luck": 0.0,
    })
    result = score_conviction(r)
    assert result.tier == "GOLD"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [OK] {name}")
    print("\nAll smoke tests passed.")
