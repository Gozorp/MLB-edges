"""
End-to-end smoke test with synthetic data. Verifies:
  - Stage 1 and Stage 2 models train
  - Walk-forward predictions run without errors
  - ROI simulation produces a coherent summary
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from mlb_edge import backtesting as bt
from mlb_edge import model as md


def make_synthetic_games(n=500, seed=0):
    rng = np.random.default_rng(seed)
    # Build features where sp_xera_gap genuinely drives home_win prob.
    sp_xera_gap = rng.normal(0, 0.8, n)
    sp_xwoba_allowed_gap = rng.normal(0, 0.02, n)
    sp_k_bb_pct_gap = rng.normal(0, 3.0, n)
    sp_siera_gap = rng.normal(0, 0.6, n)
    sp_fip_gap = rng.normal(0, 0.7, n)
    sp_recent_form_gap = rng.normal(0, 0.5, n)
    sp_hardhit_gap = rng.normal(0, 2.0, n)
    sp_stamina_gap = rng.normal(0, 0.3, n)

    team_wrcplus_gap = rng.normal(0, 8.0, n)
    team_woba_gap = rng.normal(0, 0.015, n)
    team_bbk_gap = rng.normal(0, 2.0, n)
    team_hardhit_gap = rng.normal(0, 2.0, n)
    bullpen_siera_gap = rng.normal(0, 0.4, n)
    bullpen_fatigue_gap = rng.normal(0, 0.15, n)
    park_runs_factor = rng.normal(1.0, 0.05, n)
    park_hr_factor = rng.normal(1.0, 0.08, n)
    home_ump_boost = np.ones(n)
    away_ump_boost = np.ones(n)
    home_catcher_penalty = np.ones(n)
    away_catcher_penalty = np.ones(n)
    home_sp_luck = rng.normal(0, 0.8, n)
    away_sp_luck = rng.normal(0, 0.8, n)
    is_divisional = rng.integers(0, 2, n)
    tz_diff = rng.integers(-3, 4, n)
    is_opener = rng.integers(0, 2, n)
    is_quick_turnaround = rng.integers(0, 2, n)

    # Latent utility — home advantage + SP signal dominance.
    latent = (0.08
              + 0.45 * sp_xera_gap
              + 25.0 * sp_xwoba_allowed_gap
              + 0.04 * sp_k_bb_pct_gap
              + 0.30 * sp_siera_gap
              + 0.02 * team_wrcplus_gap
              + 0.20 * bullpen_siera_gap
              - 0.35 * bullpen_fatigue_gap)
    p_win = 1 / (1 + np.exp(-latent))
    home_win = (rng.random(n) < p_win).astype(int)
    # F5 is noisier but correlated
    p_f5 = 1 / (1 + np.exp(-(latent * 1.1)))
    home_f5_win = (rng.random(n) < p_f5).astype(int)

    game_dates = pd.date_range("2024-04-01", periods=n, freq="D")
    teams = [f"T{i%30}" for i in range(n)]
    opps = [f"T{(i+1)%30}" for i in range(n)]

    return pd.DataFrame({
        "game_id": [f"g{i}" for i in range(n)],
        "game_date": game_dates,
        "home_team": teams,
        "away_team": opps,
        "sp_xera_gap": sp_xera_gap,
        "sp_xwoba_allowed_gap": sp_xwoba_allowed_gap,
        "sp_k_bb_pct_gap": sp_k_bb_pct_gap,
        "sp_siera_gap": sp_siera_gap,
        "sp_fip_gap": sp_fip_gap,
        "sp_recent_form_gap": sp_recent_form_gap,
        "sp_hardhit_gap": sp_hardhit_gap,
        "sp_stamina_gap": sp_stamina_gap,
        "team_wrcplus_gap": team_wrcplus_gap,
        "team_woba_gap": team_woba_gap,
        "team_bbk_gap": team_bbk_gap,
        "team_hardhit_gap": team_hardhit_gap,
        "bullpen_siera_gap": bullpen_siera_gap,
        "bullpen_fatigue_gap": bullpen_fatigue_gap,
        "park_runs_factor": park_runs_factor,
        "park_hr_factor": park_hr_factor,
        "home_ump_boost": home_ump_boost,
        "away_ump_boost": away_ump_boost,
        "home_catcher_penalty": home_catcher_penalty,
        "away_catcher_penalty": away_catcher_penalty,
        "home_sp_luck": home_sp_luck,
        "away_sp_luck": away_sp_luck,
        "is_divisional": is_divisional,
        "tz_diff": tz_diff,
        "is_opener": is_opener,
        "is_quick_turnaround": is_quick_turnaround,
        "home_win": home_win,
        "home_f5_win": home_f5_win,
    })


def make_synthetic_odds(games):
    # Synthetic market: Vegas sees 80% of the truth + some noise + vig.
    rng = np.random.default_rng(1)
    n = len(games)
    true_p = 1 / (1 + np.exp(-(0.08 + 0.45 * games["sp_xera_gap"])))
    noisy_p = true_p * 0.85 + rng.normal(0, 0.03, n)
    noisy_p = np.clip(noisy_p, 0.15, 0.85)
    vig = 0.045
    home_p_w_vig = noisy_p + vig / 2
    away_p_w_vig = (1 - noisy_p) + vig / 2

    def prob_to_american(p):
        if p >= 0.5:
            return -round(p / (1 - p) * 100)
        return round((1 - p) / p * 100)

    rows = []
    for i, g in games.iterrows():
        rows.append({"game_id": g["game_id"], "market": "h2h",
                     "outcome": g["home_team"],
                     "price": prob_to_american(home_p_w_vig[i])})
        rows.append({"game_id": g["game_id"], "market": "h2h",
                     "outcome": g["away_team"],
                     "price": prob_to_american(away_p_w_vig[i])})
    return pd.DataFrame(rows)


def main():
    print("Building synthetic training frame (n=500)...")
    games = make_synthetic_games(n=500)
    odds = make_synthetic_odds(games)

    print("Walk-forward training (3 folds)...")
    preds = bt.fit_and_predict_walk_forward(games, n_splits=3)
    print(f"  predictions produced: {len(preds)}")

    print("Simulating ROI...")
    result = bt.simulate_roi(preds, odds, start_bankroll=100.0)

    print("\n=== Backtest Summary ===")
    for k, v in result.summary.items():
        if k == "by_tier":
            print(f"  by_tier: {v.get('n', {})}")
        else:
            print(f"  {k}: {v}")
    print("\n[OK] End-to-end pipeline executed.")


if __name__ == "__main__":
    main()
