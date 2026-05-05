"""
train_v7_equiv.py
-----------------
Retrain a v7-equivalent Stage 2 model that excludes the 3 lineup features
(`lineup_vs_sp_gap`, `lineup_wrcplus_gap`, `lineup_hardhit_gap`). Uses the
EXISTING v5 feature cache — no rebuild required. Saves to
`models/v7_equiv.pkl`.

Why: the 2026-OOS diagnosis (diagnose_v8_calibration.py) showed that a
simple fixed-alpha blend of v7+v8 raw probabilities drops Brier from
0.2535 (v8 alone) to 0.2499 (alpha=0.5) — bigger win than any calibration
fix. But to deploy the blend at predict time we need both models live.
v7 weights were overwritten during the v8 retrain, so we regenerate a
v7-equivalent by training Stage 2 on the SAME cache but with a reduced
feature list.

Strategy:
  - Monkey-patch `model.FULL_FEATURES_EXTRA` to exclude the 3 lineup cols.
  - Call the same train_stage1_f5 / train_stage2_full pathways main.py uses.
  - Save to models/v7_equiv.pkl (does NOT overwrite models/latest.pkl).

Note Stage 1 is unchanged (F5_FEATURES is SP-only, no lineup involvement),
so this really only produces a different Stage 2 booster.

Usage:
    python train_v7_equiv.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from mlb_edge import build_pipeline as bp
from mlb_edge import model as md


LINEUP_FEATS = ["lineup_vs_sp_gap", "lineup_wrcplus_gap", "lineup_hardhit_gap"]
SAVE_PATH = "models/v7_equiv.pkl"


def main() -> int:
    print("Patching FULL_FEATURES_EXTRA to exclude lineup features...")
    original_full = list(md.FULL_FEATURES_EXTRA)
    md.FULL_FEATURES_EXTRA = [c for c in original_full if c not in LINEUP_FEATS]
    dropped = [c for c in original_full if c in LINEUP_FEATS]
    print(f"  dropped: {dropped}")
    print(f"  remaining features ({len(md.FULL_FEATURES_EXTRA)}): "
          f"{md.FULL_FEATURES_EXTRA}")

    print("\nBuilding training frames for 2023, 2024, 2025...")
    frames = []
    for season in [2023, 2024, 2025]:
        f = bp.build_historical_frame(season)
        if not f.empty:
            print(f"  {season}: {len(f)} games")
            frames.append(f)
    if not frames:
        print("ERROR: no training frames built")
        return 1

    df = pd.concat(frames, ignore_index=True).sort_values("game_date")
    df = df.dropna(subset=["home_win", "home_f5_win"])
    print(f"\nCombined training frame: {len(df)} games")

    print("\nTraining Stage 1 (F5)...")
    stage1 = md.train_stage1_f5(df)
    print(f"  train AUC: {stage1.metadata['train_auc']:.4f}")

    print("\nTraining Stage 2 (full, WITHOUT lineup features)...")
    stage2 = md.train_stage2_full(df, stage1)
    print(f"  train AUC: {stage2.metadata['train_auc']:.4f}")
    print(f"  feature_cols: {stage2.feature_cols}")

    print(f"\nSaving to {SAVE_PATH}...")
    Path(SAVE_PATH).parent.mkdir(parents=True, exist_ok=True)
    md.save(stage1, stage2, SAVE_PATH)
    print("  saved.")

    # Restore original list so other code in the same session isn't affected.
    md.FULL_FEATURES_EXTRA = original_full
    print("\nRestored FULL_FEATURES_EXTRA to include lineup features.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
