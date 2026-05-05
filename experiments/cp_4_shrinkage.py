"""CP-4 — Bayesian shrinkage on small-sample features.

Implementation:
  - Read the v12 scrubbed cache (2024 train, 2025 val).
  - For each gap-feature group, compute n_eff = min(home_side_n, away_side_n)
    and replace the raw gap with: shrunk = (n / (n + tau)) * raw + 0.
  - When n_eff is NaN or 0, the gap collapses to 0 (the prior). This
    directly neutralizes the "missing-bullpen-data inflates the gap" failure
    mode the 5/2 slate documented.
  - Train CLEAN baseline vs SHRUNK experimental on the same data.
  - Standard ship-gate report.

Replace-not-add: when USE_BAYESIAN_SHRINKAGE=True, the raw gap columns are
*replaced* with shrunk values in the feature frame before training.
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

# Shrinkage groups
SP_GAPS = [
    "sp_xera_gap", "sp_siera_gap", "sp_fip_gap", "sp_k_bb_pct_gap",
    "sp_xwoba_allowed_gap", "sp_recent_form_gap", "sp_hardhit_gap",
    "sp_stamina_gap", "sp_velo_drop_gap", "sp_vs_lineup_gap",
]
BULLPEN_GAPS = [
    "bullpen_siera_gap", "bullpen_xwoba_gap",
    "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
    "bullpen_hardhit_gap", "bullpen_fatigue_gap",
]
HL_BULLPEN_GAPS = ["hl_bullpen_xera_gap", "hl_bullpen_xwoba_gap"]
LINEUP_GAPS = ["lineup_wrcplus_gap", "lineup_vs_sp_gap", "lineup_hardhit_gap"]

SHRINKAGE_PLAN = [
    ("SP", SP_GAPS, "home_sp_n_pitches", "away_sp_n_pitches", 600),
    ("Bullpen", BULLPEN_GAPS, "home_bullpen_n_pitches", "away_bullpen_n_pitches", 3000),
    ("HL_Bullpen", HL_BULLPEN_GAPS, "home_hl_bullpen_n_pitches", "away_hl_bullpen_n_pitches", 1000),
    ("Lineup", LINEUP_GAPS, "home_lineup_n_slots", "away_lineup_n_slots", 9),
]


def apply_shrinkage(df: pd.DataFrame, verbose=True) -> pd.DataFrame:
    """Replace raw gap features in-place with shrunk versions."""
    out = df.copy()
    summary = []
    for name, gaps, home_n_col, away_n_col, tau in SHRINKAGE_PLAN:
        if home_n_col not in out.columns or away_n_col not in out.columns:
            if verbose:
                print(f"  [{name}] missing sample-size cols, skipping")
            continue
        # n_eff = min of the two sides; treat NaN as 0 (full shrinkage to prior)
        h_n = pd.to_numeric(out[home_n_col], errors="coerce").fillna(0)
        a_n = pd.to_numeric(out[away_n_col], errors="coerce").fillna(0)
        n_eff = np.minimum(h_n, a_n)
        weight_raw = n_eff / (n_eff + tau)
        # Track stats for the headline group
        n_full = (n_eff >= tau).sum()
        n_zero = (n_eff <= 1e-6).sum()
        n_total = len(out)
        if verbose:
            print(f"  [{name}] tau={tau}  n_full_weight={n_full}/{n_total} ({n_full/n_total*100:.1f}%)  "
                  f"n_zero_eff={n_zero}/{n_total} ({n_zero/n_total*100:.1f}%)  "
                  f"median_weight={float(weight_raw.median()):.3f}")
        for col in gaps:
            if col not in out.columns:
                continue
            raw = pd.to_numeric(out[col], errors="coerce")
            shrunk = weight_raw * raw  # prior=0
            # Where the raw was NaN, keep NaN (XGBoost handles it)
            shrunk = shrunk.where(raw.notna(), np.nan)
            out[col] = shrunk
        summary.append({
            "group": name, "tau": tau,
            "n_full_weight": int(n_full), "n_zero_eff": int(n_zero),
            "n_total": int(n_total), "median_weight": float(weight_raw.median()),
        })
    return out, summary


def load_year(year: int, shrunk: bool):
    feats = pd.read_parquet(CACHE_DIR / f"features_{year}_full_1_v12.parquet")
    tgt = CACHE_DIR / f"targets_{year}_v12.parquet"
    if tgt.exists():
        feats = feats.merge(pd.read_parquet(tgt), on="game_id", how="left")
    if shrunk:
        feats, summary = apply_shrinkage(feats, verbose=False)
        return feats, summary
    return feats, None


def metric_set(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    pick = (p >= 0.5).astype(int)
    return {"n": int(len(y)),
            "brier": float(brier_score_loss(y, p)),
            "log_loss": float(log_loss(y, p)),
            "hit_rate": float((pick == y).mean())}


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


def reliability_bins(y, p_a, p_b, edges=(0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0)):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m_a = (p_a > lo) & (p_a <= hi); m_b = (p_b > lo) & (p_b <= hi)
        n_a, n_b = m_a.sum(), m_b.sum()
        if n_a == 0 and n_b == 0: continue
        ga = (y[m_a].mean() - p_a[m_a].mean()) * 100 if n_a else float("nan")
        gb = (y[m_b].mean() - p_b[m_b].mean()) * 100 if n_b else float("nan")
        rows.append({"bin": f"({lo:.2f}, {hi:.2f}]", "n_clean": int(n_a),
                     "gap_clean_pp": ga, "n_shrunk": int(n_b), "gap_shrunk_pp": gb})
    return pd.DataFrame(rows)


def tier_segmented(y, p_a, p_b, edges=(0, 0.55, 0.60, 0.65, 0.72, 1.0)):
    rows = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (p_a > lo) & (p_a <= hi)
        n = mask.sum()
        if n < 20: continue
        b_a = brier_score_loss(y[mask], np.clip(p_a[mask], 1e-6, 1 - 1e-6))
        b_b = brier_score_loss(y[mask], np.clip(p_b[mask], 1e-6, 1 - 1e-6))
        h_a = ((p_a[mask] >= 0.5).astype(int) == y[mask]).mean()
        h_b = ((p_b[mask] >= 0.5).astype(int) == y[mask]).mean()
        rows.append({"p_clean_band": f"({lo:.2f}, {hi:.2f}]", "n": int(n),
                     "brier_clean": b_a, "brier_shrunk": b_b, "delta_brier": b_b - b_a,
                     "hit_clean": h_a, "hit_shrunk": h_b, "delta_hit": h_b - h_a})
    return pd.DataFrame(rows)


def archetype_check(val_df, p_clean, p_shrunk):
    """Find games where missing-bullpen-data inflated p_clean past 0.80,
    and report what shrinkage did to the prediction."""
    df = val_df.copy()
    df["p_clean"] = p_clean
    df["p_shrunk"] = p_shrunk
    h_n = pd.to_numeric(df.get("home_bullpen_n_pitches"), errors="coerce").fillna(0)
    a_n = pd.to_numeric(df.get("away_bullpen_n_pitches"), errors="coerce").fillna(0)
    df["bp_min"] = np.minimum(h_n, a_n)
    archetype = df[(df["p_clean"] >= 0.78) | (df["p_clean"] <= 0.22)]
    archetype = archetype[archetype["bp_min"] < 1500]  # missing or very thin bp
    archetype = archetype.assign(p_pick=np.where(archetype["p_clean"] >= 0.5, archetype["p_clean"], 1 - archetype["p_clean"]))
    archetype = archetype.nlargest(15, "p_pick")
    return archetype[["game_date", "home_team", "away_team", "bp_min",
                      "p_clean", "p_shrunk", "home_win"]]


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    print("=" * 76)
    print("CP-4 — Bayesian shrinkage on small-sample features")
    print("=" * 76)

    # CLEAN run — raw v12 cache
    df_2024_clean, _ = load_year(2024, shrunk=False)
    df_2025_clean, _ = load_year(2025, shrunk=False)
    train_clean = df_2024_clean.dropna(subset=["home_win", "home_f5_win"]).sort_values("game_date").copy()
    val_clean = df_2025_clean.dropna(subset=["home_win", "home_f5_win"]).copy()
    print(f"  train (2024): {len(train_clean):,} games")
    print(f"  val (2025):   {len(val_clean):,} games")

    # Shrinkage diagnostic — what fraction of the train set has full weight vs neutralized
    print("\nShrinkage stats (on train set):")
    _, summary = apply_shrinkage(train_clean, verbose=True)

    feats = list(md.FULL_FEATURES_EXTRA)
    print(f"\n  Feature count (same for both runs): {len(feats)}")

    print("\nTraining CLEAN (raw gaps)...")
    s1c = md.train_stage1_f5(train_clean)
    s2c = md.train_stage2_full(train_clean, s1c)
    print(f"  stage 2 train AUC: {s2c.metadata.get('train_auc'):.4f}")

    print("\nTraining SHRUNK (gap features replaced by shrunk versions)...")
    train_shrunk, _ = apply_shrinkage(train_clean.copy(), verbose=False)
    val_shrunk_df, _ = apply_shrinkage(val_clean.copy(), verbose=False)
    s1s = md.train_stage1_f5(train_shrunk)
    s2s = md.train_stage2_full(train_shrunk, s1s)
    print(f"  stage 2 train AUC: {s2s.metadata.get('train_auc'):.4f}")

    pred_clean = md.predict(s1c, s2c, val_clean.copy())
    pred_shrunk = md.predict(s1s, s2s, val_shrunk_df.copy())
    y = val_clean["home_win"].astype(int).values
    p_clean = pred_clean["model_prob"].values[:len(y)]
    p_shrunk = pred_shrunk["model_prob"].values[:len(y)]
    m_clean = metric_set(y, p_clean)
    m_shrunk = metric_set(y, p_shrunk)

    print("\n" + "=" * 76)
    print("HOLD-OUT (2025) pooled metrics")
    print("=" * 76)
    print(f"  {'metric':<12} {'CLEAN':>10} {'SHRUNK':>10} {'delta':>12}")
    for k in ("brier", "log_loss", "hit_rate"):
        d = m_shrunk[k] - m_clean[k]
        print(f"  {k:<12} {m_clean[k]:>10.4f} {m_shrunk[k]:>10.4f}   {d:+10.4f}")

    lo, hi, mean = bootstrap_brier_ci(y, p_clean, p_shrunk, n_resamples=500)
    print(f"\n  Bootstrap Brier delta (CLEAN-SHRUNK) mean={mean:+.4f}  CI95=[{lo:+.4f}, {hi:+.4f}]")
    print(f"  CI excludes zero: {('YES' if (lo > 0 or hi < 0) else 'NO')}")
    ship = (m_shrunk["brier"] - m_clean["brier"]) <= -0.005 and (lo > 0 or hi < 0)
    print(f"  SHIP GATE (Brier improvement >= 0.005, CI excludes zero): {'PASS' if ship else 'FAIL'}")

    rel = reliability_bins(y, p_clean, p_shrunk)
    print("\nReliability bins (HEADLINE — does (0.65, 1.00] band soften?):")
    print(rel.to_string(index=False))

    seg = tier_segmented(y, p_clean, p_shrunk)
    print("\nTier-segmented (by p_clean band):")
    print(seg.to_string(index=False))

    print("\nArchetype check — games where missing bullpen data may have inflated p_clean:")
    arch = archetype_check(val_clean.assign(p_clean=p_clean, p_shrunk=p_shrunk), p_clean, p_shrunk)
    print(arch.to_string(index=False))

    # SHAP gain on shrunk model
    booster = s2s.booster.get_booster()
    score = booster.get_score(importance_type="gain")
    feat_names = list(s2s.feature_cols)
    items = sorted(score.items(), key=lambda kv: -kv[1])
    print("\nSHRUNK model — top 15 SHAP-gain features:")
    shrunk_set = set(SP_GAPS + BULLPEN_GAPS + HL_BULLPEN_GAPS + LINEUP_GAPS)
    for i, (k, v) in enumerate(items[:15], 1):
        if k.startswith("f") and k[1:].isdigit():
            idx = int(k[1:])
            name = feat_names[idx] if idx < len(feat_names) else k
        else:
            name = k
        flag = "**" if name in shrunk_set else "  "
        print(f"  {i:>2} {v:>8.1f} {flag} {name}")

    out_path = Path(r"D:\mlb_edge\mlb_edge\data\pitch_quality\phase4_summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "train_size": int(len(train_clean)),
        "val_size": int(len(val_clean)),
        "shrinkage_summary": summary,
        "clean": m_clean, "shrunk": m_shrunk,
        "brier_delta": m_shrunk["brier"] - m_clean["brier"],
        "bootstrap_ci95": [lo, hi], "bootstrap_mean": mean,
        "ship_gate_pass": ship,
        "reliability_bins": rel.to_dict(orient="records"),
        "tier_segmented": seg.to_dict(orient="records"),
        "archetype_check": arch.to_dict(orient="records"),
    }, indent=2, default=str))
    print(f"\nSummary written: {out_path}")


if __name__ == "__main__":
    main()
