"""
train_signal_meta.py
--------------------
Train a small logistic-regression "signals-only" classifier as a foil to
the main XGBoost model. Inputs are JUST the 5 conviction signal votes
(home / away / abstain) per game. Output is P(home_win).

The point: we get a CLEAN signal-driven probability that's independent
of the XGBoost model's 70+ features. At predict time we can compare
the two probabilities; large divergences are interesting signals.

Saves to models/signal_meta.pkl.
"""
from __future__ import annotations
import sys, os
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def vote_to_int(features) -> dict:
    """Per-signal vote: +1 = home, -1 = away, 0 = abstain.
    Mirrors the directional thresholds in game_end_watcher._compute_signal_votes."""
    out = {}

    f1 = features.get("sp_xera_gap", 0) or 0
    out["F1"] = 1 if f1 >= 0.75 else (-1 if f1 <= -0.75 else 0)

    f2 = features.get("team_woba_gap", 0) or 0
    out["F2"] = 1 if f2 >= 0.020 else (-1 if f2 <= -0.020 else 0)

    f3 = features.get("swing_take_gap", 0) or 0
    out["F3"] = 1 if f3 >= 15 else (-1 if f3 <= -15 else 0)

    home_luck = features.get("home_sp_luck", 0) or 0
    away_luck = features.get("away_sp_luck", 0) or 0
    if abs(home_luck) >= 1.0 and abs(home_luck) >= abs(away_luck):
        out["F4"] = 1 if home_luck > 0 else -1
    elif abs(away_luck) >= 1.0:
        out["F4"] = -1 if away_luck > 0 else 1
    else:
        out["F4"] = 0

    f5 = features.get("bullpen_siera_gap", 0) or 0
    out["F5"] = 1 if f5 >= 0.40 else (-1 if f5 <= -0.40 else 0)

    return out


def main() -> int:
    print("Loading historical caches…")
    cache_dir = ROOT / "data" / "feature_cache"
    frames = []
    for season in (2023, 2024, 2025):
        # Prefer newest version available
        for ver in ("v12", "v11", "v10", "v9"):
            p = cache_dir / f"features_{season}_full_1_{ver}.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                frames.append(df)
                print(f"  loaded {p.name}: {len(df)} games")
                break

    train = pd.concat(frames, ignore_index=True)
    train = train.dropna(subset=["home_win"]).reset_index(drop=True)
    print(f"  total: {len(train)} games\n")

    # Build feature matrix from signal votes
    print("Computing per-row signal votes…")
    rows = []
    for _, r in train.iterrows():
        v = vote_to_int(r)
        v["home_win"] = int(r["home_win"])
        rows.append(v)
    X = pd.DataFrame(rows)
    print(f"  vote frame: {len(X)} rows × {len(X.columns)} cols")

    # Train logistic regression — just the 5 vote columns
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score, log_loss

    Xtr = X[["F1", "F2", "F3", "F4", "F5"]].values
    ytr = X["home_win"].values

    print("\nTraining logistic regression (signals only)...")
    lr = LogisticRegression(max_iter=200, C=1.0)
    lr.fit(Xtr, ytr)
    proba = lr.predict_proba(Xtr)[:, 1]
    auc = roc_auc_score(ytr, proba)
    ll = log_loss(ytr, proba)
    print(f"  Train AUC:     {auc:.4f}")
    print(f"  Train log-loss: {ll:.4f}")
    print(f"  Coefficients (intercept + per-signal weights):")
    print(f"    intercept = {lr.intercept_[0]:+.3f}")
    for name, coef in zip(["F1", "F2", "F3", "F4", "F5"], lr.coef_[0]):
        print(f"    {name} = {coef:+.3f}")

    # Save
    out_path = ROOT / "models" / "signal_meta.pkl"
    joblib.dump({"model": lr, "features": ["F1", "F2", "F3", "F4", "F5"]}, out_path)
    print(f"\n✓ Saved to {out_path.name}")

    # Quick sanity check: predict on a hold-out sample
    print("\nValidation note: this is in-sample AUC. The model uses only 5 binary")
    print("votes — far less information than the XGBoost main model. We expect")
    print("AUC around 0.55-0.62, much lower than the main model's 0.83. The")
    print("point isn't to BEAT the main model — it's to give us a pure-signal")
    print("baseline to compare against.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
