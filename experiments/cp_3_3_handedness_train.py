"""CP-3.3 — train experimental stage-2 with handedness-stratified park features.

Setup mirrors CP-A2:
  - Train: 2024 scrubbed v12 cache.
  - Val:   2025 scrubbed v12 cache.
  - CLEAN baseline (51 feats) vs LINE_HANDED (51 + 4 handedness park features).
  - Bootstrap CI on Brier delta, reliability bins, top-shifted games, SHAP.

Ship gate: Brier delta >= 0.005 with CI excluding zero.
"""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge import model as md

CACHE_DIR = Path(r"D:\mlb_edge\mlb_edge\data\feature_cache")

HANDEDNESS_FEATURES = [
    "lhb_pa_pct",
    "park_runs_factor_handed",
    "park_hr_factor_handed",
    "park_hr_lhb_minus_rhb",
]


def load_year(year: int) -> pd.DataFrame:
    feats = pd.read_parquet(CACHE_DIR / f"features_{year}_full_1_v12.parquet")
    tgt = CACHE_DIR / f"targets_{year}_v12.parquet"
    if tgt.exists():
        feats = feats.merge(pd.read_parquet(tgt), on="game_id", how="left")
    hp = CACHE_DIR / "handedness_park_factors_per_game.parquet"
    if hp.exists():
        hpdf = pd.read_parquet(hp)[["game_pk"] + HANDEDNESS_FEATURES]
        hpdf = hpdf.rename(columns={"game_pk": "game_id"})
        feats = feats.merge(hpdf, on="game_id", how="left")
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


def reliability_bins(y, p_clean, p_exp, edges=(0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0)):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask_c = (p_clean > lo) & (p_clean <= hi)
        mask_e = (p_exp > lo) & (p_exp <= hi)
        n_c, n_e = mask_c.sum(), mask_e.sum()
        if n_c == 0 and n_e == 0:
            continue
        gap_c = (y[mask_c].mean() - p_clean[mask_c].mean()) * 100 if n_c else float("nan")
        gap_e = (y[mask_e].mean() - p_exp[mask_e].mean()) * 100 if n_e else float("nan")
        rows.append({"bin": f"({lo:.2f}, {hi:.2f}]",
                     "n_clean": int(n_c), "gap_clean_pp": gap_c,
                     "n_exp": int(n_e), "gap_exp_pp": gap_e})
    return pd.DataFrame(rows)


def top_shifted_games(val_df, p_clean, p_exp, k=15):
    df = val_df.copy()
    df["p_clean"] = p_clean
    df["p_exp"] = p_exp
    df["shift"] = (df["p_exp"] - df["p_clean"]).abs()
    df = df.nlargest(k, "shift")
    out = []
    for _, r in df.iterrows():
        won_home = bool(r["home_win"]) if pd.notna(r["home_win"]) else None
        pick_clean = "home" if r["p_clean"] >= 0.5 else "away"
        pick_exp = "home" if r["p_exp"] >= 0.5 else "away"
        right_clean = (pick_clean == "home" and won_home) or (pick_clean == "away" and not won_home)
        right_exp = (pick_exp == "home" and won_home) or (pick_exp == "away" and not won_home)
        out.append({
            "date": str(r["game_date"])[:10],
            "matchup": f"{r['away_team']} @ {r['home_team']}",
            "lhb_pct": float(r.get("lhb_pa_pct", float("nan"))),
            "park_hr_handed": float(r.get("park_hr_factor_handed", float("nan"))),
            "p_clean": float(r["p_clean"]),
            "p_exp": float(r["p_exp"]),
            "shift": float(r["shift"]),
            "home_won": int(won_home) if won_home is not None else None,
            "shift_right": "+" if right_exp else ("=" if right_clean == right_exp else "-"),
        })
    return out


def tier_segmented(y, p_clean, p_exp, edges=(0, 0.55, 0.60, 0.65, 0.72, 1.0)):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        # Segment by p_clean band
        mask = (p_clean > lo) & (p_clean <= hi)
        n = mask.sum()
        if n < 20:
            continue
        b_c = brier_score_loss(y[mask], np.clip(p_clean[mask], 1e-6, 1 - 1e-6))
        b_e = brier_score_loss(y[mask], np.clip(p_exp[mask], 1e-6, 1 - 1e-6))
        h_c = ((p_clean[mask] >= 0.5).astype(int) == y[mask]).mean()
        h_e = ((p_exp[mask] >= 0.5).astype(int) == y[mask]).mean()
        rows.append({
            "p_clean_band": f"({lo:.2f}, {hi:.2f}]", "n": int(n),
            "brier_clean": b_c, "brier_exp": b_e,
            "delta_brier": b_e - b_c,
            "hit_clean": h_c, "hit_exp": h_e, "delta_hit": h_e - h_c,
        })
    return pd.DataFrame(rows)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    print("=" * 76)
    print("CP-3.3 — handedness-stratified park factors experimental stage-2")
    print("=" * 76)
    df_2024 = load_year(2024)
    df_2025 = load_year(2025)
    train_df = df_2024.dropna(subset=["home_win", "home_f5_win"]).sort_values("game_date").copy()
    val_df = df_2025.dropna(subset=["home_win", "home_f5_win"]).copy()
    print(f"  train (2024): {len(train_df):,} games  ({train_df['lhb_pa_pct'].notna().sum()} have handedness features)")
    print(f"  val (2025):   {len(val_df):,} games  ({val_df['lhb_pa_pct'].notna().sum()} have handedness features)")

    base_feats = list(md.FULL_FEATURES_EXTRA)
    exp_feats = base_feats + HANDEDNESS_FEATURES
    print(f"\n  CLEAN feature count: {len(base_feats)}")
    print(f"  EXP   feature count: {len(exp_feats)} (+{len(HANDEDNESS_FEATURES)} handedness)")

    print("\nTraining CLEAN...")
    s1c, s2c = train_with_features(train_df, base_feats, "CLEAN")
    print(f"  stage 2 train AUC: {s2c.metadata.get('train_auc'):.4f}")

    print("\nTraining EXP (handedness)...")
    s1e, s2e = train_with_features(train_df, exp_feats, "EXP")
    print(f"  stage 2 train AUC: {s2e.metadata.get('train_auc'):.4f}")

    val_clean = md.predict(s1c, s2c, val_df.copy())
    val_exp = md.predict(s1e, s2e, val_df.copy())
    y = val_df["home_win"].astype(int).values
    p_clean = val_clean["model_prob"].values[:len(y)]
    p_exp = val_exp["model_prob"].values[:len(y)]
    m_clean = metric_set(y, p_clean)
    m_exp = metric_set(y, p_exp)

    print("\n" + "=" * 76)
    print("HOLD-OUT (2025) pooled metrics")
    print("=" * 76)
    print(f"  {'metric':<12} {'CLEAN':>10} {'EXP':>10} {'delta':>12}")
    for k in ("brier", "log_loss", "hit_rate"):
        d = m_exp[k] - m_clean[k]
        print(f"  {k:<12} {m_clean[k]:>10.4f} {m_exp[k]:>10.4f}   {d:+10.4f}")

    lo, hi, mean = bootstrap_brier_ci(y, p_clean, p_exp, n_resamples=500)
    print(f"\n  Bootstrap Brier delta (CLEAN-EXP) mean={mean:+.4f}  CI95=[{lo:+.4f}, {hi:+.4f}]")
    print(f"  CI excludes zero: {('YES' if (lo > 0 or hi < 0) else 'NO')}")
    ship = (m_exp["brier"] - m_clean["brier"]) <= -0.005 and (lo > 0 or hi < 0)
    print(f"  SHIP GATE (Brier improvement >= 0.005, CI excludes zero): {'PASS' if ship else 'FAIL'}")

    rel = reliability_bins(y, p_clean, p_exp)
    print("\nReliability bins:")
    print(rel.to_string(index=False))

    seg = tier_segmented(y, p_clean, p_exp)
    print("\nTier-segmented metrics (by p_clean band):")
    print(seg.to_string(index=False))

    print("\nTop 15 most-shifted games (val):")
    shifts = top_shifted_games(val_df, p_clean, p_exp, k=15)
    for s in shifts:
        print(f"  {s['date']} {s['matchup']:<14} lhb%={s['lhb_pct']:.3f} pf_hr={s['park_hr_handed']:.3f} "
              f"p_clean={s['p_clean']:.3f}  p_exp={s['p_exp']:.3f} shift={s['shift']:+.3f} "
              f"home_won={s['home_won']} {s['shift_right']}")

    booster = s2e.booster.get_booster()
    score = booster.get_score(importance_type="gain")
    feat_names = list(s2e.feature_cols)
    items = sorted(score.items(), key=lambda kv: -kv[1])
    print("\nEXP model — top 20 SHAP-gain features:")
    handed_set = set(HANDEDNESS_FEATURES)
    for i, (k, v) in enumerate(items[:20], 1):
        if k.startswith("f") and k[1:].isdigit():
            idx = int(k[1:])
            name = feat_names[idx] if idx < len(feat_names) else k
        else:
            name = k
        is_h = "**" if name in handed_set else "  "
        print(f"  {i:>2} {v:>8.1f} {is_h} {name}")

    out_path = Path(r"D:\mlb_edge\mlb_edge\data\pitch_quality\phase3_summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "train_size": int(len(train_df)),
        "val_size": int(len(val_df)),
        "n_train_with_handedness": int(train_df['lhb_pa_pct'].notna().sum()),
        "n_val_with_handedness": int(val_df['lhb_pa_pct'].notna().sum()),
        "clean": m_clean, "exp": m_exp,
        "brier_delta": m_exp["brier"] - m_clean["brier"],
        "bootstrap_ci95": [lo, hi], "bootstrap_mean": mean,
        "ship_gate_pass": ship,
        "reliability_bins": rel.to_dict(orient="records"),
        "tier_segmented": seg.to_dict(orient="records"),
        "top_shifted": shifts,
    }, indent=2, default=str))
    print(f"\nSummary written: {out_path}")


if __name__ == "__main__":
    main()
