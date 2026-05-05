"""
diagnose_calibrator.py
----------------------
Rigorous test of whether the Platt calibrator is correctly sharpening
Stage 2's raw probabilities, or over-fitting.

Method:
  1. Load historical games (2024 + 2025) via the normal pipeline.
  2. Run walk-forward training — each fold re-fits Stage 2 and its Platt
     calibrator on past data only, then predicts the future fold. The
     returned DataFrame has (model_prob_raw, model_prob, home_win) where
     the calibrator never saw the game it's scoring.
  3. Reliability diagram: bin raw_prob and cal_prob by decile; report the
     empirical home-win rate per bin next to what the model claimed.
  4. Brier scores: raw vs calibrated, on the same out-of-fold predictions.
  5. Verdict: Platt is legitimately sharpening iff raw is under-confident
     in the bins where Platt pushes away from 0.5, AND calibrated Brier
     beats raw Brier on out-of-fold.

Usage:
    python diagnose_calibrator.py --seasons 2024,2025 --out calibrator_diag.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

from mlb_edge import backtesting as bt
from mlb_edge import build_pipeline as bp


def _reliability_table(raw: np.ndarray, cal: np.ndarray,
                       y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """One row per equal-count bin of the RAW prob, showing the empirical
    win rate alongside what raw and calibrated say."""
    order = np.argsort(raw)
    raw_s, cal_s, y_s = raw[order], cal[order], y[order]
    rows = []
    # Equal-count bins (quantile) over raw — that's the space we want
    # to test calibration in, since cal is a function of raw.
    edges = np.quantile(raw, np.linspace(0, 1, n_bins + 1))
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (raw >= lo) & (raw <= hi) if i == n_bins - 1 else (raw >= lo) & (raw < hi)
        if mask.sum() < 5:
            continue
        rows.append({
            "bin":         i + 1,
            "raw_lo":      lo,
            "raw_hi":      hi,
            "n":           int(mask.sum()),
            "raw_mean":    float(raw[mask].mean()),
            "cal_mean":    float(cal[mask].mean()),
            "empirical":   float(y[mask].mean()),
            "raw_err":     float(raw[mask].mean() - y[mask].mean()),
            "cal_err":     float(cal[mask].mean() - y[mask].mean()),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2024,2025",
                    help="Comma-separated seasons to backtest (both training and OOF eval)")
    ap.add_argument("--n_splits", type=int, default=5,
                    help="Walk-forward splits; each fold fits its own calibrator")
    ap.add_argument("--out", default="calibrator_diag.csv",
                    help="Per-game OOF predictions written here for auditing")
    args = ap.parse_args()

    seasons = [int(s) for s in args.seasons.split(",")]
    print(f"Loading historical games for seasons: {seasons}")
    frames = []
    for s in seasons:
        g = bp.build_historical_frame(s)
        if not g.empty:
            frames.append(g)
    if not frames:
        print("No games loaded. Abort.")
        return
    games = pd.concat(frames, ignore_index=True)
    games = games.dropna(subset=["home_win", "home_f5_win"]).copy()
    print(f"  total games: {len(games)}")

    print(f"Running walk-forward ({args.n_splits} folds) — "
          "calibrator is re-fit per fold on past data only...")
    preds = bt.fit_and_predict_walk_forward(games, n_splits=args.n_splits)
    if preds.empty:
        print("Walk-forward returned nothing. Abort.")
        return
    print(f"  OOF predictions: {len(preds)}")

    # Save per-game OOF preds for the user to inspect
    cols = ["game_id", "game_date", "home_team", "away_team",
            "home_win", "f5_prob", "model_prob_raw", "model_prob", "fold"]
    available = [c for c in cols if c in preds.columns]
    preds[available].to_csv(args.out, index=False)
    print(f"  wrote per-game OOF predictions to {args.out}")
    print()

    raw = preds["model_prob_raw"].to_numpy(dtype=float)
    cal = preds["model_prob"].to_numpy(dtype=float)
    y   = preds["home_win"].to_numpy(dtype=int)

    # Reliability by decile of raw prob
    rel = _reliability_table(raw, cal, y, n_bins=10)
    print("RELIABILITY TABLE (binned by raw Stage 2 prob, decile boundaries):")
    print("  bin = decile index; n = games in bin")
    print("  raw_mean  = what Stage 2 said on average")
    print("  cal_mean  = what Platt-calibrated prob said on average")
    print("  empirical = actual home-win rate in bin")
    print("  raw_err   = raw_mean - empirical  (positive = raw over-confident toward home)")
    print("  cal_err   = cal_mean - empirical  (positive = calibrated over-confident)")
    print()
    print(rel.round(4).to_string(index=False))
    print()

    print("OVERALL BRIER (lower is better):")
    raw_brier = brier_score_loss(y, raw)
    cal_brier = brier_score_loss(y, cal)
    print(f"  raw        : {raw_brier:.5f}")
    print(f"  calibrated : {cal_brier:.5f}   (delta = {cal_brier - raw_brier:+.5f})")
    print()

    print("OVERALL LOG LOSS (lower is better):")
    # Clip to avoid log(0)
    raw_c = np.clip(raw, 1e-6, 1 - 1e-6)
    cal_c = np.clip(cal, 1e-6, 1 - 1e-6)
    raw_ll = log_loss(y, raw_c)
    cal_ll = log_loss(y, cal_c)
    print(f"  raw        : {raw_ll:.5f}")
    print(f"  calibrated : {cal_ll:.5f}   (delta = {cal_ll - raw_ll:+.5f})")
    print()

    # Sharpness — average |p - 0.5|. Higher = more confident predictions.
    raw_sharp = float(np.abs(raw - 0.5).mean())
    cal_sharp = float(np.abs(cal - 0.5).mean())
    print(f"SHARPNESS (mean |p - 0.5|):")
    print(f"  raw        : {raw_sharp:.4f}")
    print(f"  calibrated : {cal_sharp:.4f}   (ratio = {cal_sharp / max(raw_sharp, 1e-9):.2f}x)")
    print()

    # Decile-bin verdict
    rel_sorted = rel.sort_values("bin")
    raw_abs = rel_sorted["raw_err"].abs().mean()
    cal_abs = rel_sorted["cal_err"].abs().mean()
    print("DECILE-AVERAGED |error| (bin-level miscalibration):")
    print(f"  raw        : {raw_abs:.4f}")
    print(f"  calibrated : {cal_abs:.4f}   (ratio = {cal_abs / max(raw_abs, 1e-9):.2f}x)")
    print()

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    verdict_brier = cal_brier < raw_brier
    verdict_bin   = cal_abs < raw_abs
    if verdict_brier and verdict_bin:
        print("  Platt is CORRECTLY sharpening: better Brier AND better bin-error.")
        print("  The steep sigmoid reflects real under-confidence in Stage 2's raw.")
        print("  -> Action: widen the [0.48, 0.72] band to match the new calibrated distribution.")
    elif verdict_brier and not verdict_bin:
        print("  MIXED: better aggregate Brier but worse per-bin error.")
        print("  Platt may be trading bias in the middle for accuracy at the tails.")
        print("  -> Action: inspect the reliability table and decide by bin.")
    elif not verdict_brier and verdict_bin:
        print("  MIXED: worse Brier but better per-bin error.")
        print("  Platt sharpens bins but inflates losses when it's wrong.")
        print("  -> Action: consider a milder calibration (lower coefficient via regularization).")
    else:
        print("  Platt is OVER-FITTING: worse Brier AND worse bin-error.")
        print("  Raw probs are better calibrated than the Platt-sharpened version.")
        print("  -> Action: disable Stage 2 calibration or use a much weaker calibrator.")
    print()


if __name__ == "__main__":
    main()
