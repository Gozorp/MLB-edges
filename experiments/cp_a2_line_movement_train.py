"""CP-A2 — train experimental stage-2 with line_movement features.

Setup:
  - Train: 2024 scrubbed v12 cache (single year, matches Phase 1 CP4 protocol).
  - Val: 2025 scrubbed v12 cache (hold-out).
  - Two models trained, both on the same data:
      CLEAN: FULL_FEATURES_EXTRA (51 features) — production baseline
      LINE:  FULL_FEATURES_EXTRA + line_movement features
  - Both score 2025 hold-out. Compute Brier / log_loss / hit_rate
    deltas with bootstrap 95% CI (n_resamples=500), reliability bins,
    top-shifted games, SHAP gain importance.

Ship gate (same as Phase 1): Brier delta >= 0.005 with CI excluding zero.
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


CACHE_DIR = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

LINE_MOVEMENT_FEATURES = [
    "line_movement_home_pp",
    "line_movement_away_pp",
    "open_to_close_hours",
    "n_books_open",
    "n_books_close",
]


def load_year(year: int) -> pd.DataFrame:
    feats = pd.read_parquet(CACHE_DIR / f"features_{year}_full_1_v12.parquet")
    tgt = CACHE_DIR / f"targets_{year}_v12.parquet"
    if tgt.exists():
        feats = feats.merge(pd.read_parquet(tgt), on="game_id", how="left")
    lm_path = CACHE_DIR / f"line_movement_{year}_v1.parquet"
    if lm_path.exists():
        lm = pd.read_parquet(lm_path)[["game_id"] + LINE_MOVEMENT_FEATURES]
        feats = feats.merge(lm, on="game_id", how="left")
    return feats


def metric_set(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    pick = (p >= 0.5).astype(int)
    return {
        "n": int(len(y)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p)),
        "hit_rate": float((pick == y).mean()),
    }


def bootstrap_brier_ci(y, p_a, p_b, n_resamples=500, seed=42):
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        d = brier_score_loss(y[idx], p_a[idx]) - brier_score_loss(y[idx], p_b[idx])
        diffs.append(d)
    diffs = np.array(diffs)
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)), float(diffs.mean())


def train_with_features(train_df, feats, name):
    log = logging.getLogger(__name__)
    log.info("[%s] feats=%d", name, len(feats))
    stage1 = md.train_stage1_f5(train_df)
    original = md.FULL_FEATURES_EXTRA
    try:
        md.FULL_FEATURES_EXTRA = feats
        stage2 = md.train_stage2_full(train_df, stage1)
    finally:
        md.FULL_FEATURES_EXTRA = original
    return stage1, stage2


def reliability_bins(y, p_clean, p_line, edges=(0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0)):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask_c = (p_clean > lo) & (p_clean <= hi)
        mask_l = (p_line > lo) & (p_line <= hi)
        n_c = mask_c.sum()
        n_l = mask_l.sum()
        if n_c == 0 and n_l == 0:
            continue
        gap_c = (y[mask_c].mean() - p_clean[mask_c].mean()) * 100 if n_c else float("nan")
        gap_l = (y[mask_l].mean() - p_line[mask_l].mean()) * 100 if n_l else float("nan")
        rows.append({
            "bin": f"({lo:.2f}, {hi:.2f}]",
            "n_clean": int(n_c), "gap_clean_pp": gap_c,
            "n_line": int(n_l), "gap_line_pp": gap_l,
        })
    return pd.DataFrame(rows)


def top_shifted_games(val_df, p_clean, p_line, k=10):
    df = val_df.copy()
    df["p_clean"] = p_clean
    df["p_line"] = p_line
    df["shift"] = (df["p_line"] - df["p_clean"]).abs()
    df = df.nlargest(k, "shift")
    out = []
    for _, r in df.iterrows():
        # Pick clean and pick line
        pick_clean = "home" if r["p_clean"] >= 0.5 else "away"
        pick_line = "home" if r["p_line"] >= 0.5 else "away"
        # Did line shift improve directional accuracy?
        won_home = bool(r["home_win"]) if pd.notna(r["home_win"]) else None
        right_clean = (pick_clean == "home" and won_home) or (pick_clean == "away" and not won_home)
        right_line = (pick_line == "home" and won_home) or (pick_line == "away" and not won_home)
        improved = bool(right_line) and not bool(right_clean)
        out.append({
            "date": str(r["game_date"])[:10],
            "matchup": f"{r['away_team']} @ {r['home_team']}",
            "lm_home_pp": r.get("line_movement_home_pp", float("nan")),
            "p_clean": float(r["p_clean"]),
            "p_line": float(r["p_line"]),
            "shift": float(r["shift"]),
            "home_won": int(won_home) if won_home is not None else None,
            "shift_right": "+" if right_line else "-",
        })
    return out


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")

    print("=" * 76)
    print("CP-A2 — line_movement experimental stage-2")
    print("=" * 76)

    df_2024 = load_year(2024)
    df_2025 = load_year(2025)

    train_df = df_2024.dropna(subset=["home_win", "home_f5_win"]).sort_values("game_date").copy()
    val_df = df_2025.dropna(subset=["home_win", "home_f5_win"]).copy()
    print(f"  train (2024): {len(train_df):,} games  ({train_df['line_movement_home_pp'].notna().sum()} have line_movement)")
    print(f"  val (2025):   {len(val_df):,} games  ({val_df['line_movement_home_pp'].notna().sum()} have line_movement)")

    base_feats = list(md.FULL_FEATURES_EXTRA)
    line_feats = base_feats + LINE_MOVEMENT_FEATURES
    print(f"\n  CLEAN feature count: {len(base_feats)}")
    print(f"  LINE  feature count: {len(line_feats)} (+ {len(LINE_MOVEMENT_FEATURES)} line_movement)")

    print("\nTraining CLEAN...")
    s1c, s2c = train_with_features(train_df, base_feats, "CLEAN")
    print(f"  stage 2 train AUC: {s2c.metadata.get('train_auc'):.4f}")

    print("\nTraining LINE...")
    s1l, s2l = train_with_features(train_df, line_feats, "LINE")
    print(f"  stage 2 train AUC: {s2l.metadata.get('train_auc'):.4f}")

    # Score both on 2025
    val_clean = md.predict(s1c, s2c, val_df.copy())
    val_line = md.predict(s1l, s2l, val_df.copy())
    y = val_df["home_win"].astype(int).values
    p_clean = val_clean["model_prob"].values[:len(y)]
    p_line = val_line["model_prob"].values[:len(y)]

    m_clean = metric_set(y, p_clean)
    m_line = metric_set(y, p_line)

    print("\n" + "=" * 76)
    print("HOLD-OUT (2025) pooled metrics")
    print("=" * 76)
    print(f"  {'metric':<12} {'CLEAN':>10} {'LINE':>10} {'delta':>12}")
    for k in ("brier", "log_loss", "hit_rate"):
        d = m_line[k] - m_clean[k]
        print(f"  {k:<12} {m_clean[k]:>10.4f} {m_line[k]:>10.4f}   {d:+10.4f}")

    # Bootstrap CI on Brier delta
    lo, hi, mean = bootstrap_brier_ci(y, p_clean, p_line, n_resamples=500)
    print(f"\n  Bootstrap Brier delta (CLEAN-LINE) mean={mean:+.4f}  CI95=[{lo:+.4f}, {hi:+.4f}]")
    print(f"  CI excludes zero: {('YES' if (lo > 0 or hi < 0) else 'NO')}")
    ship_gate_pass = (m_line["brier"] - m_clean["brier"]) <= -0.005 and (lo > 0 or hi < 0)
    print(f"  SHIP GATE (Brier improvement >= 0.005, CI excludes zero): {'PASS' if ship_gate_pass else 'FAIL'}")

    # Reliability bins
    rel = reliability_bins(y, p_clean, p_line)
    print("\nReliability bins:")
    print(rel.to_string(index=False))

    # Top-shifted games
    print("\nTop 10 most-shifted games (val):")
    shifts = top_shifted_games(val_df, p_clean, p_line, k=10)
    for s in shifts:
        print(f"  {s['date']} {s['matchup']:<14} lm_home={s['lm_home_pp']:+.2f}  "
              f"p_clean={s['p_clean']:.3f}  p_line={s['p_line']:.3f}  "
              f"shift={s['shift']:+.3f}  home_won={s['home_won']}  right={s['shift_right']}")

    # SHAP gain importance for the LINE model
    booster = s2l.booster.get_booster()
    score = booster.get_score(importance_type="gain")
    feat_names = list(s2l.feature_cols)
    items = sorted(score.items(), key=lambda kv: -kv[1])
    print("\nLINE model — top 20 SHAP-gain features:")
    line_feat_set = set(LINE_MOVEMENT_FEATURES)
    for i, (k, v) in enumerate(items[:20], 1):
        # XGBoost uses fX naming. Map back via feat_names index.
        if k.startswith("f") and k[1:].isdigit():
            idx = int(k[1:])
            name = feat_names[idx] if idx < len(feat_names) else k
        else:
            name = k
        is_lm = "**" if name in line_feat_set else "  "
        print(f"  {i:>2} {v:>8.1f} {is_lm} {name}")

    # Persist
    out_path = Path(r"D:\mlb_edge\mlb_edge\data\pitch_quality\cp_a2_summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "train_size": int(len(train_df)),
        "val_size": int(len(val_df)),
        "n_train_with_line": int(train_df['line_movement_home_pp'].notna().sum()),
        "n_val_with_line": int(val_df['line_movement_home_pp'].notna().sum()),
        "clean": m_clean, "line": m_line,
        "brier_delta": m_line["brier"] - m_clean["brier"],
        "bootstrap_ci95": [lo, hi],
        "bootstrap_mean": mean,
        "ship_gate_pass": ship_gate_pass,
        "reliability_bins": rel.to_dict(orient="records"),
        "top_shifted": shifts,
        "n_features_clean": len(base_feats),
        "n_features_line": len(line_feats),
    }, indent=2, default=str))
    print(f"\nSummary written: {out_path}")

    # Persist val predictions
    val_pred_df = val_df[["game_id", "game_date", "home_team", "away_team", "home_win",
                          "line_movement_home_pp", "line_movement_away_pp",
                          "open_to_close_hours"]].copy()
    val_pred_df["p_clean"] = p_clean
    val_pred_df["p_line"] = p_line
    val_pred_df.to_csv(Path(r"D:\mlb_edge\mlb_edge\data\pitch_quality\cp_a2_val_predictions.csv"), index=False)
    print("Val predictions written.")


if __name__ == "__main__":
    main()
