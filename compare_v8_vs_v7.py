"""
compare_v8_vs_v7.py
-------------------
Head-to-head on the 2026 OOS slate: v7 (no lineup features) vs v8 (lineup
features baked into the retrained model).

Reads:
  backtest_fill_2026_preds_v7.csv   -- preserved prior-run baseline
  backtest_fill_2026_preds.csv      -- fresh output from the v8 retrained model

Computes three verdict tables:
  1. Full-slate Brier / log loss / accuracy / sharpness (raw + filled + blended)
  2. Patched-game subset only (games where fill_one_game substituted values)
  3. Flip analysis: games where v8 picks a different side than v7

The v7 headline numbers from the prior run (for reference if the preserved
CSV is ever lost):
    RAW:      n=417  Brier 0.2514  acc 51.6%
    FILLED:   n=417  Brier 0.2540  acc 53.2%
    BLENDED:  n=417  Brier 0.2500  acc 53.7%   (alpha=0.35)
    Patched subset (n=213): blended Brier 0.2456, acc 56.3%

v8's hypothesis: lineup-aware offense should improve raw (the model now sees
per-hitter hand-split signals the team aggregates miss), and therefore should
improve the blended number too.

Usage:
    python compare_v8_vs_v7.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


V7_PATH = Path("backtest_fill_2026_preds_v7.csv")
V8_PATH = Path("backtest_fill_2026_preds.csv")


def brier(p: np.ndarray, y: np.ndarray) -> float:
    """Brier = mean squared error of probability vs. binary outcome."""
    return float(((p - y) ** 2).mean())


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-15) -> float:
    p = np.clip(p, eps, 1.0 - eps)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float(((p >= 0.5).astype(int) == y).mean())


def sharpness(p: np.ndarray) -> float:
    """Average |p - 0.5|; higher = model is more confident (not necessarily
    better calibrated — compare to Brier)."""
    return float(np.abs(p - 0.5).mean())


def summarize(df: pd.DataFrame, label: str, prob_col: str,
              y_col: str = "home_win") -> dict:
    p = df[prob_col].to_numpy(dtype=float)
    y = df[y_col].to_numpy(dtype=int)
    mask = ~np.isnan(p)
    if mask.sum() < len(p):
        print(f"  {label}: dropping {(~mask).sum()} rows with NaN {prob_col}")
    p, y = p[mask], y[mask]
    return {
        "label":     label,
        "n":         len(p),
        "brier":     brier(p, y),
        "log_loss":  log_loss(p, y),
        "accuracy":  accuracy(p, y),
        "sharpness": sharpness(p),
    }


def fmt_row(r: dict) -> str:
    return (f"  {r['label']:<22s}  n={r['n']:>4d}  "
            f"Brier={r['brier']:.4f}  LL={r['log_loss']:.4f}  "
            f"Acc={r['accuracy']*100:5.2f}%  Sharp={r['sharpness']:.3f}")


def compare_tables(v7: pd.DataFrame, v8: pd.DataFrame) -> None:
    """Print the three verdict tables."""
    # -----------------------------------------------------------------
    # 1. Full-slate comparison
    # -----------------------------------------------------------------
    print("=" * 78)
    print("TABLE 1: Full-slate metrics (all 2026 games with a final score)")
    print("=" * 78)
    for side, label in [(v7, "v7"), (v8, "v8")]:
        for prob_col in ("raw_prob", "fill_prob", "blend_prob"):
            if prob_col not in side.columns:
                continue
            r = summarize(side, f"{label} {prob_col}", prob_col)
            print(fmt_row(r))
        print()

    # -----------------------------------------------------------------
    # 2. Patched-subset comparison — games where MLB-API fill kicked in
    # -----------------------------------------------------------------
    print("=" * 78)
    print("TABLE 2: Patched subset (games where fill changed raw->filled)")
    print("=" * 78)
    for side, label in [(v7, "v7"), (v8, "v8")]:
        if "raw_prob" not in side.columns or "fill_prob" not in side.columns:
            continue
        # Patched: raw and filled differ materially (> 0.002 prob gap). Same
        # heuristic backtest_fill_2026.py uses for its patched flag.
        patched = side[np.abs(side["raw_prob"] - side["fill_prob"]) > 0.002]
        print(f"  {label}: {len(patched)} patched of {len(side)} "
              f"({len(patched)/max(len(side),1)*100:.1f}%)")
        for prob_col in ("raw_prob", "fill_prob", "blend_prob"):
            if prob_col not in patched.columns:
                continue
            r = summarize(patched, f"{label} {prob_col} (patched)", prob_col)
            print(fmt_row(r))
        print()

    # -----------------------------------------------------------------
    # 3. Flip analysis — games where v7 vs v8 pick different sides
    # -----------------------------------------------------------------
    print("=" * 78)
    print("TABLE 3: Flip analysis (v7 vs v8 disagree on pick side)")
    print("=" * 78)
    # Join on game_id + home_team to ensure same game.
    join_cols = [c for c in ("game_id", "home_team") if c in v7.columns and c in v8.columns]
    if not join_cols:
        print("  Cannot join — missing game_id / home_team in one of the CSVs.")
        return
    merged = v7.merge(v8, on=join_cols, suffixes=("_v7", "_v8"))
    if "blend_prob_v7" not in merged.columns or "blend_prob_v8" not in merged.columns:
        # Back off to raw_prob if blend_prob isn't in one file.
        pa, pb = "raw_prob_v7", "raw_prob_v8"
    else:
        pa, pb = "blend_prob_v7", "blend_prob_v8"
    merged["v7_pick"] = (merged[pa] >= 0.5).astype(int)
    merged["v8_pick"] = (merged[pb] >= 0.5).astype(int)
    y_col = "home_win_v7" if "home_win_v7" in merged.columns else "home_win_v8"
    if y_col not in merged.columns:
        y_col = "home_win"
    if y_col not in merged.columns:
        print("  No outcome column found; skipping flip analysis.")
        return
    flips = merged[merged["v7_pick"] != merged["v8_pick"]].copy()
    flips["v7_right"] = (flips["v7_pick"] == flips[y_col]).astype(int)
    flips["v8_right"] = (flips["v8_pick"] == flips[y_col]).astype(int)
    n_flip = len(flips)
    n_v7 = int(flips["v7_right"].sum())
    n_v8 = int(flips["v8_right"].sum())
    print(f"  Total flipped games:  {n_flip}")
    print(f"  v7 right on flips:    {n_v7}/{n_flip} ({n_v7/max(n_flip,1)*100:.1f}%)")
    print(f"  v8 right on flips:    {n_v8}/{n_flip} ({n_v8/max(n_flip,1)*100:.1f}%)")
    delta = n_v8 - n_v7
    print(f"  Net delta (v8 - v7):  {delta:+d} correct picks on flipped games")
    print()
    print("  Sample flipped games (first 20):")
    sample = flips[join_cols + [pa, pb, y_col, "v7_right", "v8_right"]].head(20)
    print(sample.to_string(index=False))


def main() -> int:
    if not V7_PATH.exists():
        print(f"ERROR: v7 baseline not found at {V7_PATH}")
        print("       Run `cp backtest_fill_2026_preds.csv backtest_fill_2026_preds_v7.csv` "
              "BEFORE running backtest_fill_2026.py on the v8 model, to preserve the baseline.")
        return 1
    if not V8_PATH.exists():
        print(f"ERROR: v8 output not found at {V8_PATH}")
        print("       Run: python backtest_fill_2026.py")
        return 1

    v7 = pd.read_csv(V7_PATH)
    v8 = pd.read_csv(V8_PATH)
    print(f"Loaded v7: {len(v7)} rows, cols={list(v7.columns)[:12]}...")
    print(f"Loaded v8: {len(v8)} rows, cols={list(v8.columns)[:12]}...")
    print()

    compare_tables(v7, v8)
    print()
    print("=" * 78)
    print("NOTES")
    print("=" * 78)
    print("""
  Brier lower = better. Log loss lower = better. Accuracy higher = better.
  Sharpness alone is not better/worse — it only matters if Brier also drops.

  The key v8 question: does the lineup-aware signal improve raw_prob on
  the 2026 OOS slate? If v8 raw Brier < v7 raw Brier, the lineup features
  carry signal that the team aggregates miss. Most of the v8 lift (if any)
  should land in the PATCHED SUBSET — early-season games where the team
  aggregate is noisy but a lineup posted with real batters gives a stable
  prior via the hand-split cascade.

  Expect v8 accuracy to flip-flop some games against v7. A win is: v8
  flips MORE games to the right side than to the wrong side.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
