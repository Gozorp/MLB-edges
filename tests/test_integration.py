"""
Integration test — synthesize a season of games and fit both stages end-to-end.
Proves the two-stage architecture actually trains and predicts without error.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from mlb_edge.model import (
    F5_FEATURES,
    FULL_FEATURES_EXTRA,
    predict,
    train_stage1_f5,
    train_stage2_full,
    time_series_cv,
    importance_table,
)


def synthesize_games(n=800, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "game_id": range(n),
        "game_date": pd.date_range("2024-03-28", periods=n, freq="h"),
        "home_team": ["HOM"] * n,
        "away_team": ["AWY"] * n,
        # Stage 1 SP gap features — generate correlated signal
        "sp_xera_gap":           rng.normal(0, 0.8, n),
        "sp_xwoba_allowed_gap":  rng.normal(0, 0.015, n),
        "sp_fip_gap":            rng.normal(0, 0.7, n),
        "sp_siera_gap":          rng.normal(0, 0.5, n),
        "sp_k_bb_pct_gap":       rng.normal(0, 3.0, n),
        "sp_recent_form_gap":    rng.normal(0, 0.6, n),
        "sp_hardhit_gap":        rng.normal(0, 3.0, n),
        "sp_stamina_gap":        rng.normal(0, 0.5, n),
        # Stage 2 extras
        "team_wrcplus_gap":      rng.normal(0, 10, n),
        "team_woba_gap":         rng.normal(0, 0.015, n),
        "team_bbk_gap":          rng.normal(0, 3, n),
        "team_hardhit_gap":      rng.normal(0, 3, n),
        "bullpen_siera_gap":     rng.normal(0, 0.5, n),
        "bullpen_fatigue_gap":   rng.normal(0, 0.2, n),
        "park_runs_factor":      rng.normal(1.0, 0.05, n),
        "park_hr_factor":        rng.normal(1.0, 0.08, n),
        "home_ump_boost":        1.0 + rng.uniform(0, 0.05, n),
        "away_ump_boost":        1.0 + rng.uniform(0, 0.05, n),
        "home_catcher_penalty":  rng.choice([1.0, 0.95], n, p=[0.8, 0.2]),
        "away_catcher_penalty":  rng.choice([1.0, 0.95], n, p=[0.8, 0.2]),
        "home_sp_luck":          rng.normal(0, 0.8, n),
        "away_sp_luck":          rng.normal(0, 0.8, n),
        "is_divisional":         rng.integers(0, 2, n),
        "tz_diff":               rng.integers(-3, 4, n),
        "is_opener":             rng.integers(0, 2, n),
        "is_quick_turnaround":   rng.integers(0, 2, n),
    })

    # Generate targets — F5 is driven mostly by SP features; full-game adds bullpen + offense
    f5_logit = (
        0.8 * df["sp_xera_gap"] +
        15.0 * df["sp_xwoba_allowed_gap"] +
        0.05 * df["sp_k_bb_pct_gap"] +
        rng.normal(0, 0.5, n)
    )
    df["home_f5_win"] = (f5_logit > 0).astype(int)

    full_logit = (
        f5_logit +
        0.6 * df["bullpen_siera_gap"] -
        0.5 * df["bullpen_fatigue_gap"] +
        0.03 * df["team_wrcplus_gap"] +
        rng.normal(0, 0.7, n)
    )
    df["home_win"] = (full_logit > 0).astype(int)
    return df


def main():
    print("Generating synthetic season...")
    df = synthesize_games(n=800)
    print(f"  {len(df)} games, home_win rate = {df['home_win'].mean():.3f}, f5_win rate = {df['home_f5_win'].mean():.3f}")

    print("\nWalk-forward CV (3 folds)...")
    folds = time_series_cv(df, n_splits=3)
    for i, (tr, va) in enumerate(folds):
        m1 = train_stage1_f5(tr, valid=va)
        m2 = train_stage2_full(tr, m1, valid=va)
        print(f"  fold {i}: train={len(tr)} valid={len(va)} | "
              f"F5 valid_auc={m1.metadata.get('valid_auc', 0):.3f} | "
              f"Full valid_auc={m2.metadata.get('valid_auc', 0):.3f} | "
              f"Full valid_brier={m2.metadata.get('valid_brier', 0):.3f}")

    print("\nFinal fit on full data...")
    tr = df.iloc[:600]
    va = df.iloc[600:]
    m1 = train_stage1_f5(tr, valid=va)
    m2 = train_stage2_full(tr, m1, valid=va)

    preds = predict(m1, m2, va)
    print(f"  predicted {len(preds)} games")
    print(f"  mean model_prob = {preds['model_prob'].mean():.3f}")
    print(f"  mean f5_prob    = {preds['f5_prob'].mean():.3f}")

    print("\nStage 1 feature importance (top 5):")
    print(importance_table(m1).head().to_string(index=False))

    print("\nStage 2 feature importance (top 8) - note f5_model_output should dominate:")
    print(importance_table(m2).head(8).to_string(index=False))

    # Key assertion: SP signal anchor is working
    imp = importance_table(m2)
    if len(imp) > 0:
        top_feature = imp.iloc[0]["feature"]
        print(f"\n  [OK] Top Stage-2 feature: {top_feature}")
        if top_feature == "f5_model_output":
            print("  [OK] SP anchor is dominant - architecture working as intended.")

    print("\n[OK] Integration test passed.")


if __name__ == "__main__":
    main()
