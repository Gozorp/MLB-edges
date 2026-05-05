"""Quantify the leakage impact properly.

Train TWO models on the same data (2023 + 2024 scrubbed cache), differing
only in whether `home_f5_win` is in the feature list:

  - CLEAN: trains using `FULL_FEATURES_EXTRA` (production's feature list,
    51 features, no leak). This matches what `models/latest.pkl` does.
  - LEAKY: trains using `FULL_FEATURES_EXTRA + ["home_f5_win"]` (simulates a
    naive trainer that picks up the leaked target column).

Both score the same 2025 hold-out. The delta tells us:
  - "What would have happened if production had been using the leak?"
  - "How much risk does keeping the leak in the cache carry?"

Comparing `latest.pkl` directly to a retrain isn't apples-to-apples:
shipped model trained on 2025 data per its metadata (stage-2 n_train=5476),
so the 2025 'hold-out' is in-sample for it.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge import model as md


CACHE_DIR = Path("data/feature_cache")


def load_scrubbed_year(year: int) -> pd.DataFrame:
    feats = pd.read_parquet(CACHE_DIR / f"features_{year}_full_1_v12.parquet")
    tgt_path = CACHE_DIR / f"targets_{year}_v12.parquet"
    if tgt_path.exists():
        feats = feats.merge(pd.read_parquet(tgt_path), on="game_id", how="left")
    return feats


def metric_set(y_true, p_pred):
    p = np.clip(p_pred, 1e-6, 1 - 1e-6)
    pick = (p >= 0.5).astype(int)
    return {
        "n": int(len(y_true)),
        "brier": float(brier_score_loss(y_true, p)),
        "log_loss": float(log_loss(y_true, p)),
        "hit_rate": float((pick == y_true).mean()),
    }


def train_with_features(train_df, feats: list[str], name: str):
    """Train Stage 1 + Stage 2 with a CUSTOM feature list for stage-2.
    Stage 1 is unchanged in both runs (it doesn't see home_f5_win as a
    feature anyway — it predicts it as a target). The leak only matters
    for stage-2."""
    log = logging.getLogger(__name__)
    log.info("[%s] feature count=%d (leak in list: %s)",
             name, len(feats), "home_f5_win" in feats)

    # Stage 1 — same call regardless. Trains its own f5 model.
    stage1 = md.train_stage1_f5(train_df)

    # Stage 2 — monkey-patch FULL_FEATURES_EXTRA for this call only.
    original = md.FULL_FEATURES_EXTRA
    try:
        md.FULL_FEATURES_EXTRA = feats
        stage2 = md.train_stage2_full(train_df, stage1)
    finally:
        md.FULL_FEATURES_EXTRA = original

    return stage1, stage2


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("=" * 76)
    print("Leakage impact quantification — clean vs leaky on same train+val")
    print("=" * 76)

    # Load.
    df_2023 = load_scrubbed_year(2023)
    df_2024 = load_scrubbed_year(2024)
    df_2025 = load_scrubbed_year(2025)
    train_df = (pd.concat([df_2023, df_2024], ignore_index=True)
                  .sort_values("game_date")
                  .dropna(subset=["home_win", "home_f5_win"]))
    val_df = df_2025.dropna(subset=["home_win", "home_f5_win"]).copy()
    print(f"  train (2023+2024): {len(train_df):,} games")
    print(f"  val (2025):        {len(val_df):,} games")

    base_feats = list(md.FULL_FEATURES_EXTRA)
    leak_feats = base_feats + ["home_f5_win"]

    print(f"\n  CLEAN feature count: {len(base_feats)}")
    print(f"  LEAKY feature count: {len(leak_feats)} (+ home_f5_win)")

    # Train both.
    print("\nTraining CLEAN model (no leak)...")
    s1_clean, s2_clean = train_with_features(train_df, base_feats, "CLEAN")
    print(f"  stage 2 train AUC: {s2_clean.metadata.get('train_auc'):.4f}")

    print("\nTraining LEAKY model (with home_f5_win)...")
    s1_leak, s2_leak = train_with_features(train_df, leak_feats, "LEAKY")
    print(f"  stage 2 train AUC: {s2_leak.metadata.get('train_auc'):.4f}")

    # Score both on 2025 hold-out.
    val_clean = md.predict(s1_clean, s2_clean, val_df.copy())
    val_leak = md.predict(s1_leak, s2_leak, val_df.copy())
    y = val_df["home_win"].astype(int).values
    p_clean = val_clean["model_prob"].values[: len(y)]
    p_leak = val_leak["model_prob"].values[: len(y)]

    m_clean = metric_set(y, p_clean)
    m_leak = metric_set(y, p_leak)

    print("\n" + "=" * 76)
    print("HOLD-OUT (2025) — same train, only feature-list differs")
    print("=" * 76)
    print(f"  {'metric':<12} {'CLEAN':>10} {'LEAKY':>10} "
          f"{'delta (LEAKY−CLEAN)':>22}")
    for k in ("brier", "log_loss", "hit_rate"):
        d = m_leak[k] - m_clean[k]
        sign = "+" if d > 0 else ""
        # For brier/log_loss lower=better; for hit_rate higher=better.
        better = ("(leaky better)" if (k == "hit_rate" and d > 0) or
                                         (k != "hit_rate" and d < 0) else "(clean better)")
        if abs(d) < 1e-4:
            better = "(no diff)"
        print(f"  {k:<12} {m_clean[k]:>10.4f} {m_leak[k]:>10.4f}"
              f"   {sign}{d:>+10.4f}  {better}")

    # Also compare CLEAN to shipped latest.pkl on 2025 — even though shipped
    # trained partly on 2025 (so this is in-sample for it), we want to see
    # whether the CLEAN retrained matches it directionally.
    print("\n" + "=" * 76)
    print("Shipped latest.pkl on the same val (2025, in-sample for shipped)")
    print("=" * 76)
    s1_old, s2_old = md.load("models/latest.pkl")
    val_old = md.predict(s1_old, s2_old, val_df.copy())
    p_old = val_old["model_prob"].values[: len(y)]
    m_old = metric_set(y, p_old)
    print(f"  shipped: brier={m_old['brier']:.4f}  hit={m_old['hit_rate']:.4f}")
    print("  (This is in-sample evaluation — shipped model trained on 2025;"
          " not directly comparable.)")

    # Persist.
    summary = {
        "train_size": int(len(train_df)),
        "val_size": int(len(val_df)),
        "clean": m_clean,
        "leaky": m_leak,
        "shipped_in_sample": m_old,
        "delta_leaky_vs_clean": {
            k: float(m_leak[k] - m_clean[k]) for k in ("brier", "log_loss", "hit_rate")
        },
        "n_features_clean": len(base_feats),
        "n_features_leaky": len(leak_feats),
    }
    Path("data/pitch_quality").mkdir(parents=True, exist_ok=True)
    out = Path("data/pitch_quality/leakage_scrub_retrain.json")
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nSaved summary to {out}")


if __name__ == "__main__":
    main()
