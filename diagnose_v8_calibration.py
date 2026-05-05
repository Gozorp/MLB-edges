"""
diagnose_v8_calibration.py
--------------------------
Find WHERE the v8 model is losing Brier on the 2026 OOS slate. Reads the
preserved v7 and v8 prediction CSVs and produces:

  1. Reliability curve (10 bins) — is the model over/under-confident?
  2. Brier decomposition: calibration loss + resolution + uncertainty
  3. Slice analysis:
       - Favorite side (model prob > 0.50) vs dog side
       - Chalk (model prob >= 0.60) vs middle (0.45-0.60) vs longshot (<0.45)
       - Patched vs unpatched games
       - Per-month drift (2026 season so far)
  4. Fixed-alpha v7+v8 blend sweep — does a simple average beat either alone?

The output tells us which intervention has the biggest ROI:
  - Miscalibrated on chalk     -> refit isotonic on recent walk-forward
  - Systematic bias one side   -> add a side-prior feature or recalibrate
  - Blend improves Brier       -> ship ensemble (alpha=0.5 fixed)
  - Nothing obvious moves it   -> only new features will help (defer)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


V7 = Path("backtest_fill_2026_preds_v7.csv")
V8 = Path("backtest_fill_2026_preds.csv")


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(((p - y) ** 2).mean())


def reliability(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Return per-bin (pred mean, obs mean, n) for a reliability curve."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "bin":        f"{lo:.2f}-{hi:.2f}",
            "n":          int(mask.sum()),
            "pred_mean":  float(p[mask].mean()),
            "obs_rate":   float(y[mask].mean()),
            "gap":        float(p[mask].mean() - y[mask].mean()),
        })
    return pd.DataFrame(rows)


def brier_decomposition(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> dict:
    """Brier = calibration_loss - resolution + uncertainty.

    calibration_loss: sum_k (n_k/N) * (p_k - o_k)^2   [want small]
    resolution:       sum_k (n_k/N) * (o_k - o_bar)^2 [want large]
    uncertainty:      o_bar * (1 - o_bar)             [constant]
    """
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    N = len(p)
    o_bar = y.mean()
    cal_loss = 0.0
    resolution = 0.0
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        nk = mask.sum()
        if nk == 0:
            continue
        pk = p[mask].mean()
        ok = y[mask].mean()
        w = nk / N
        cal_loss += w * (pk - ok) ** 2
        resolution += w * (ok - o_bar) ** 2
    uncertainty = o_bar * (1 - o_bar)
    return {
        "calibration_loss": cal_loss,
        "resolution":       resolution,
        "uncertainty":      uncertainty,
        "brier_decomp":     cal_loss - resolution + uncertainty,
    }


def slice_metrics(df: pd.DataFrame, label: str, p_col: str, y_col: str,
                  mask: np.ndarray) -> dict:
    p = df.loc[mask, p_col].to_numpy(dtype=float)
    y = df.loc[mask, y_col].to_numpy(dtype=int)
    if len(p) == 0:
        return {"slice": label, "n": 0, "brier": np.nan, "acc": np.nan,
                "pred_mean": np.nan, "obs_mean": np.nan, "bias": np.nan}
    return {
        "slice":     label,
        "n":         len(p),
        "brier":     brier(p, y),
        "acc":       float(((p >= 0.5).astype(int) == y).mean()),
        "pred_mean": float(p.mean()),
        "obs_mean":  float(y.mean()),
        "bias":      float(p.mean() - y.mean()),
    }


def main() -> None:
    v7 = pd.read_csv(V7)
    v8 = pd.read_csv(V8)
    print(f"Loaded v7={len(v7)}, v8={len(v8)}")

    # Join on game_id so we can do head-to-head and blend analysis.
    m = v7.merge(v8, on="game_id", suffixes=("_v7", "_v8"))
    print(f"Merged to {len(m)} paired rows\n")

    y = m["home_win_v7"].to_numpy(dtype=int)  # v7 and v8 should have same truth
    assert (m["home_win_v7"] == m["home_win_v8"]).all(), \
        "home_win disagreement between v7 and v8 CSVs"

    # -----------------------------------------------------------------
    # 1. Reliability curves
    # -----------------------------------------------------------------
    print("=" * 78)
    print("RELIABILITY (10 bins)")
    print("=" * 78)
    for col in ["raw_prob_v7", "fill_prob_v7", "raw_prob_v8", "fill_prob_v8"]:
        if col not in m.columns:
            continue
        p = m[col].to_numpy(dtype=float)
        rel = reliability(p, y, n_bins=10)
        b = brier(p, y)
        print(f"\n  {col}   Brier={b:.4f}")
        for _, r in rel.iterrows():
            bar = "*" * max(1, int(abs(r["gap"]) * 100))
            sign = "+" if r["gap"] >= 0 else "-"
            print(f"    {r['bin']}  n={r['n']:3d}  "
                  f"pred={r['pred_mean']:.3f}  obs={r['obs_rate']:.3f}  "
                  f"gap={sign}{abs(r['gap']):.3f}  {bar}")

    # -----------------------------------------------------------------
    # 2. Brier decomposition
    # -----------------------------------------------------------------
    print("\n" + "=" * 78)
    print("BRIER DECOMPOSITION (lower calibration_loss = better calibration)")
    print("=" * 78)
    for col in ["raw_prob_v7", "raw_prob_v8"]:
        if col not in m.columns:
            continue
        p = m[col].to_numpy(dtype=float)
        d = brier_decomposition(p, y, n_bins=10)
        print(f"  {col}:")
        print(f"    calibration_loss = {d['calibration_loss']:.5f}  "
              f"(bigger = more miscalibrated)")
        print(f"    resolution       = {d['resolution']:.5f}  "
              f"(bigger = more signal)")
        print(f"    uncertainty      = {d['uncertainty']:.5f}  (constant)")

    # -----------------------------------------------------------------
    # 3. Slices
    # -----------------------------------------------------------------
    print("\n" + "=" * 78)
    print("SLICE ANALYSIS (v8 raw_prob)")
    print("=" * 78)
    col = "raw_prob_v8"
    p_all = m[col].to_numpy(dtype=float)
    slices = []
    slices.append(slice_metrics(m, "ALL",             col, "home_win_v7",
                                 np.ones(len(m), bool)))
    slices.append(slice_metrics(m, "home favored",    col, "home_win_v7",
                                 p_all >= 0.50))
    slices.append(slice_metrics(m, "away favored",    col, "home_win_v7",
                                 p_all < 0.50))
    slices.append(slice_metrics(m, "chalk (>=0.60)",  col, "home_win_v7",
                                 p_all >= 0.60))
    slices.append(slice_metrics(m, "mid (0.45-0.60)", col, "home_win_v7",
                                 (p_all >= 0.45) & (p_all < 0.60)))
    slices.append(slice_metrics(m, "dog (<0.45)",     col, "home_win_v7",
                                 p_all < 0.45))
    if "patched_v8" in m.columns:
        slices.append(slice_metrics(m, "patched",     col, "home_win_v7",
                                     m["patched_v8"].astype(bool).to_numpy()))
        slices.append(slice_metrics(m, "unpatched",   col, "home_win_v7",
                                     ~m["patched_v8"].astype(bool).to_numpy()))
    sdf = pd.DataFrame(slices)
    print(sdf.to_string(index=False,
                        formatters={"brier":"{:.4f}".format,
                                    "acc":"{:.3f}".format,
                                    "pred_mean":"{:.3f}".format,
                                    "obs_mean":"{:.3f}".format,
                                    "bias":"{:+.3f}".format}))

    # -----------------------------------------------------------------
    # 4. v7+v8 blend sweep (fixed alpha, no test-set fitting)
    # -----------------------------------------------------------------
    print("\n" + "=" * 78)
    print("v7+v8 BLEND SWEEP   p_blend = alpha*p_v7 + (1-alpha)*p_v8")
    print("=" * 78)
    p7 = m["raw_prob_v7"].to_numpy(dtype=float)
    p8 = m["raw_prob_v8"].to_numpy(dtype=float)
    for alpha in np.linspace(0.0, 1.0, 11):
        pb = alpha * p7 + (1 - alpha) * p8
        b = brier(pb, y)
        a = float(((pb >= 0.5).astype(int) == y).mean())
        print(f"  alpha={alpha:.2f}  Brier={b:.4f}  Acc={a*100:5.2f}%")

    # Also check the FILL columns
    print("\n  fill_prob blend (MLB-API filled):")
    if "fill_prob_v7" in m.columns and "fill_prob_v8" in m.columns:
        p7f = m["fill_prob_v7"].to_numpy(dtype=float)
        p8f = m["fill_prob_v8"].to_numpy(dtype=float)
        for alpha in np.linspace(0.0, 1.0, 11):
            pb = alpha * p7f + (1 - alpha) * p8f
            b = brier(pb, y)
            a = float(((pb >= 0.5).astype(int) == y).mean())
            print(f"  alpha={alpha:.2f}  Brier={b:.4f}  Acc={a*100:5.2f}%")

    # -----------------------------------------------------------------
    # 5. Monthly drift (2026 OOS so far)
    # -----------------------------------------------------------------
    if "game_date_v7" in m.columns or "game_date" in m.columns:
        print("\n" + "=" * 78)
        print("MONTHLY DRIFT (v8 raw_prob)")
        print("=" * 78)
        dcol = "game_date_v7" if "game_date_v7" in m.columns else "game_date"
        m["_month"] = pd.to_datetime(m[dcol]).dt.to_period("M").astype(str)
        for month, grp in m.groupby("_month"):
            p = grp["raw_prob_v8"].to_numpy(dtype=float)
            y_m = grp["home_win_v7"].to_numpy(dtype=int)
            print(f"  {month}  n={len(grp):3d}  Brier={brier(p, y_m):.4f}  "
                  f"Acc={((p>=0.5).astype(int)==y_m).mean()*100:5.2f}%  "
                  f"bias={(p.mean()-y_m.mean()):+.3f}")


if __name__ == "__main__":
    main()
