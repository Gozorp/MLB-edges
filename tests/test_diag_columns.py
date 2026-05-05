"""Regression tests for the two bugs surfaced 2026-05-02.

Bug 1 — `p_model` perspective. Eval scripts read `picks_<date>_diag.csv` and
treated `p_model` as home-perspective. It is pick-perspective per
`main_predict.build_diagnostic_table`. Fix: an explicit `pick_prob` column
was added; this test pins the perspective contract.

Bug 2 — silent odds drop. When OddsClient returned an empty payload the
diag was written with NaN fair_prob/edge_pp and no signal in the pipeline
output. Fix: an `odds_status` column was added to every diag row and
loud-log error in main_predict on no-API-key / empty-payload / exception.
This test pins both columns and the predictable distinction between
"fetched" / "no_match" / "unavailable".

Run with:
    python -m pytest tests/test_diag_columns.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mlb_edge.main_predict import build_diagnostic_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_games() -> pd.DataFrame:
    """Two games. Game A: model picks home @ 0.62. Game B: model picks away
    @ 0.65 (i.e., model_prob = 0.35 home-perspective). Both have the
    feature columns score_conviction expects (zeros for the SKIP path)."""
    base = {
        "sp_xera_gap": 0.0, "team_woba_gap": 0.0, "sp_k_bb_pct_gap": 0.0,
        "sp_siera_gap": 0.0, "sp_fip_gap": 0.0,
        "bullpen_siera_gap": 0.0, "bullpen_xwoba_gap": 0.0,
        "bullpen_k_pct_gap": 0.0, "bullpen_bb_pct_gap": 0.0,
        "bullpen_hardhit_gap": 0.0, "bullpen_fatigue_gap": 0.0,
        "swing_take_gap": 0.0,
        "home_sp_n_pitches": 800, "away_sp_n_pitches": 800,
        "home_bullpen_n_pitches": 4000, "away_bullpen_n_pitches": 4000,
        "home_sp_luck": 0.0, "away_sp_luck": 0.0,
        "f5_prob": 0.50,
    }
    return pd.DataFrame([
        {**base, "home_team": "NYY", "away_team": "BOS", "model_prob": 0.62,
         "f5_prob": 0.55},
        {**base, "home_team": "TEX", "away_team": "HOU", "model_prob": 0.35,
         "f5_prob": 0.40},
    ])


@pytest.fixture
def sample_odds() -> pd.DataFrame:
    """h2h odds for game A only — game B should mark odds_status=no_match.
    NYY -150 (1.667 dec), BOS +130 (2.30 dec)."""
    # Use a tz-aware ISO string the existing pivot's pd.to_datetime path
    # accepts. Trailing 'Z' alone (without the +00:00) parses cleanly.
    ct = "2026-05-02T23:00:00Z"
    return pd.DataFrame([
        {"market": "h2h", "home_team": "NYY", "away_team": "BOS",
         "outcome": "NYY", "price": -150, "decimal": 1.667,
         "commence_time": ct},
        {"market": "h2h", "home_team": "NYY", "away_team": "BOS",
         "outcome": "BOS", "price": 130, "decimal": 2.30,
         "commence_time": ct},
    ])


# ---------------------------------------------------------------------------
# Bug 1: pick_prob perspective contract
# ---------------------------------------------------------------------------
def test_diag_has_pick_prob_alias_column(sample_games, sample_odds):
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    assert "pick_prob" in table.columns, \
        "diag must include `pick_prob` so consumers can't confuse perspective"
    assert "p_model" in table.columns, "p_model retained for backward compat"


def test_pick_prob_equals_p_model_always(sample_games, sample_odds):
    """pick_prob is an explicit alias of p_model; both are pick-perspective."""
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    for _, r in table.iterrows():
        assert r["pick_prob"] == r["p_model"], \
            f"pick_prob and p_model must be equal for {r['matchup']}"


def test_p_model_is_pick_perspective_for_away_pick(sample_games, sample_odds):
    """For an away pick, p_model must equal 1 - full_prob (NOT full_prob).
    This is the contract eval scripts must rely on."""
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    away_picks = table[table["pick"] != table["matchup"].str.split(" @ ").str[1]]
    assert len(away_picks) == 1, "fixture should produce exactly one away pick"
    row = away_picks.iloc[0]
    # Game B: home_team=TEX, away_team=HOU, model_prob=0.35 → pick=HOU
    # full_prob = 0.35, p_model = 1 - 0.35 = 0.65, pick_prob = 0.65.
    assert row["pick"] == "HOU"
    assert row["full_prob"] == pytest.approx(0.35, abs=1e-3)
    assert row["p_model"] == pytest.approx(0.65, abs=1e-3)
    assert row["pick_prob"] == pytest.approx(0.65, abs=1e-3)
    # Invariant: full_prob + p_model == 1 for away picks.
    assert abs(row["full_prob"] + row["p_model"] - 1.0) < 1e-6


def test_p_model_equals_full_prob_for_home_pick(sample_games, sample_odds):
    """For a home pick, p_model == full_prob (both home-perspective on home
    pick = pick-perspective on home pick)."""
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    home_picks = table[table["pick"] == table["matchup"].str.split(" @ ").str[1]]
    assert len(home_picks) == 1, "fixture should produce exactly one home pick"
    row = home_picks.iloc[0]
    assert row["full_prob"] == pytest.approx(row["p_model"], abs=1e-6)


# ---------------------------------------------------------------------------
# Bug 2: odds_status sentinel + non-empty fair_prob when odds available
# ---------------------------------------------------------------------------
def test_diag_has_odds_status_column(sample_games, sample_odds):
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    assert "odds_status" in table.columns


def test_odds_status_marks_fetched_vs_no_match(sample_games, sample_odds):
    """Game A has odds — should be 'fetched'. Game B has no odds row —
    should be 'no_match' (not 'unavailable'), so a downstream reader can
    distinguish 'API didn't fire' from 'API fired, no row for this game'."""
    table = build_diagnostic_table(sample_games, sample_odds, odds_status="fetched")
    a = table[table["matchup"] == "BOS @ NYY"].iloc[0]
    b = table[table["matchup"] == "HOU @ TEX"].iloc[0]
    assert a["odds_status"] == "fetched"
    assert pd.notna(a["fair_prob"]), \
        "fair_prob must be populated when odds were fetched and matched"
    assert b["odds_status"] == "no_match"
    assert pd.isna(b["fair_prob"])


def test_odds_status_marks_unavailable_when_no_odds(sample_games):
    """When OddsClient returned empty (rate-limit / API down / no key),
    every row should be marked with the upstream status, NOT 'fetched'.
    This is the failure mode that produced 2026-04-30 and 2026-05-01 with
    silently empty fair_prob columns."""
    empty_odds = pd.DataFrame()
    table = build_diagnostic_table(sample_games, empty_odds, odds_status="empty_payload")
    assert (table["odds_status"] == "empty_payload").all()
    assert table["fair_prob"].isna().all()


def test_diag_perspective_invariant_holds_on_real_artifact():
    """Spot-check on a real shipped diag: full_prob + p_model == 1 for
    away-pick rows. Re-runs against the latest available picks_*_diag.csv
    in the working tree. Catches the case where build_diagnostic_table is
    edited and the perspective contract drifts."""
    repo_root = Path(__file__).resolve().parents[1]
    diag_files = sorted(repo_root.glob("picks_2026-*_diag.csv"))
    if not diag_files:
        pytest.skip("no diag artifacts to check")
    latest = diag_files[-1]
    df = pd.read_csv(latest).drop_duplicates("matchup")
    parts = df["matchup"].str.split(" @ ", expand=True)
    df["home"] = parts[1]
    df["pick_is_home"] = df["pick"] == df["home"]
    away_rows = df[(~df["pick_is_home"])
                   & df["full_prob"].notna() & df["p_model"].notna()]
    if away_rows.empty:
        pytest.skip(f"{latest.name} has no away-pick rows")
    delta = (away_rows["full_prob"] + away_rows["p_model"] - 1.0).abs()
    assert delta.max() < 1e-3, (
        f"{latest.name}: full_prob + p_model should = 1 for away picks, "
        f"max deviation = {delta.max():.4f}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
