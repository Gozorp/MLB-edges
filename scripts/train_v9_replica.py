"""
train_v9_replica.py
-------------------
Train a v9-era model directly from the cached v9 feature parquets — used to
restore the model after v10/v11/v12 chain was shown to be -14.9pp worse on
04-24 + 04-25 slates.

The v9 cache files preserve the v9 feature schema (raw stats, no prior-year
shrinkage) AND the v9 column set (no bullpen rate stats, no HL bullpen, no
umpire). Training Stage 1 + Stage 2 on those gives a model that thinks like
v9 did, on the most data available (3 full seasons + 2026 partial).

Saves to models/latest.pkl (replacing the v12 in there) and ALSO to
models/v9_replica.pkl as a stable reference. v12_backup.pkl exists for
future A/B testing.
"""
from __future__ import annotations
import sys, os
from pathlib import Path
import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from mlb_edge.model import (
    train_stage1_f5, train_stage2_full,
    F5_FEATURES, FULL_FEATURES_EXTRA,
)

print("Loading v9 cache files...")
cache_dir = ROOT / "data" / "feature_cache"
frames = []
for season in (2023, 2024, 2025):
    p = cache_dir / f"features_{season}_full_1_v9.parquet"
    if not p.exists():
        print(f"  MISSING: {p.name}")
        continue
    df = pd.read_parquet(p)
    frames.append(df)
    print(f"  loaded {p.name}: {len(df)} games, {len(df.columns)} cols")
# 2026 partial v9
p2026 = cache_dir / "features_2026_2026-04-24_1_v9.parquet"
if p2026.exists():
    df = pd.read_parquet(p2026)
    frames.append(df)
    print(f"  loaded {p2026.name}: {len(df)} games")

train = pd.concat(frames, ignore_index=True)
print(f"\nTotal training games: {len(train)}")

# v9-era feature columns: filter FULL_FEATURES_EXTRA to columns the v9 cache
# actually has. The newer additions (bullpen rate stats, HL bullpen, umpire)
# simply won't be in the v9 frame, so they're dropped from the training set.
v9_full_features = [c for c in FULL_FEATURES_EXTRA if c in train.columns]
v9_f5_features = [c for c in F5_FEATURES if c in train.columns]
new_features = [c for c in FULL_FEATURES_EXTRA if c not in train.columns]
print(f"\nv9 F5 features: {len(v9_f5_features)} of {len(F5_FEATURES)}")
print(f"v9 FULL features: {len(v9_full_features)} of {len(FULL_FEATURES_EXTRA)}")
if new_features:
    print(f"Dropped (not in v9 cache): {new_features}")

# Filter to required cols + labels and drop bad rows
needed = list(set(v9_f5_features + v9_full_features
                  + ["home_win", "home_f5_win", "game_date"]))
train = train[needed].copy()
train = train.dropna(subset=["home_win", "home_f5_win"]).reset_index(drop=True)
train["game_date"] = pd.to_datetime(train["game_date"])
print(f"After NA-drop: {len(train)} games")

print("\nTraining Stage 1 (F5)...")
# Monkeypatch FULL_FEATURES_EXTRA temporarily — train functions use the
# module-level constant. Cleanest: pass through the v9 list via env or
# just edit the list in place.
import mlb_edge.model as model_mod
orig_full = model_mod.FULL_FEATURES_EXTRA
orig_f5 = model_mod.F5_FEATURES
model_mod.FULL_FEATURES_EXTRA = v9_full_features
model_mod.F5_FEATURES = v9_f5_features

try:
    stage1 = train_stage1_f5(train)
    print(f"  Stage 1 train AUC: {stage1.metadata.get('train_auc'):.4f}")
    print("\nTraining Stage 2 (full game)...")
    stage2 = train_stage2_full(train, stage1)
    print(f"  Stage 2 train AUC: {stage2.metadata.get('train_auc'):.4f}")
finally:
    model_mod.FULL_FEATURES_EXTRA = orig_full
    model_mod.F5_FEATURES = orig_f5

out = {"stage1": stage1, "stage2": stage2}
joblib.dump(out, ROOT / "models" / "v9_replica.pkl")
joblib.dump(out, ROOT / "models" / "latest.pkl")
print("\nSaved models/v9_replica.pkl AND models/latest.pkl (v9-era schema)")
