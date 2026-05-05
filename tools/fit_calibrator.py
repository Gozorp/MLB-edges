"""
tools/fit_calibrator.py
-----------------------
Fit a Stage-2 probability calibrator on the production booster's raw
predictions vs known historical outcomes.  No retrain required — we
load the existing model bundle, run inference on a holdout, fit the
calibrator on the (raw_prob, y_true) pairs, attach it to Stage 2, and
write a NEW model bundle.

The model_registry.save() hook auto-archives the displaced bundle, so
rolling back is a single command if the calibrated model underperforms:

    python -m mlb_edge.model_registry rollback <previous-id>

Usage
=====
    # Fit on the bundled backtest CSVs, evaluate, and (if better) promote.
    python tools/fit_calibrator.py

    # Dry run — fit + report, do not save the bundle.
    python tools/fit_calibrator.py --dry-run

    # Force-promote even if metrics regress (for debugging only).
    python tools/fit_calibrator.py --force-promote
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

# Make the script runnable as `python tools/fit_calibrator.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlb_edge import calibration as cal
from mlb_edge import model as md

log = logging.getLogger("fit_calibrator")


# ---------------------------------------------------------------------------
# Holdout assembly — uses the bundled backtest CSVs that already contain
# (raw_prob, y_true) pairs.  We DON'T re-run inference because (a) the
# backtest CSVs are walk-forward holdout outputs (no leakage), and (b)
# re-running would require the full historical feature pipeline.
# ---------------------------------------------------------------------------
HOLDOUT_FILES = [
    # (file, raw_prob_col, y_true_col, label)
    # backtest_fill_2026_preds.csv has columns:
    #   game_id, game_date, home_team, away_team, home_win,
    #   home_f5_win, raw_prob, fill_prob, raw_f5, fill_f5, ...
    # `raw_prob` is the booster's home-win probability; `home_win` is the truth.
    ("backtest_fill_2026_preds.csv", "raw_prob", "home_win", "2026 fill"),
]


def load_holdout(repo_root: Path) -> pd.DataFrame:
    frames = []
    for fname, prob_col, y_col, label in HOLDOUT_FILES:
        path = repo_root / fname
        if not path.exists():
            log.warning("holdout file missing: %s", path)
            continue
        df = pd.read_csv(path)
        if prob_col not in df.columns or y_col not in df.columns:
            log.warning("%s missing column %s or %s", fname, prob_col, y_col)
            continue
        sub = df[[prob_col, y_col]].rename(
            columns={prob_col: "raw_prob", y_col: "y_true"}
        )
        sub["source"] = label
        frames.append(sub)
        log.info("loaded %s: %d rows", fname, len(sub))
    if not frames:
        raise SystemExit("no holdout data found — populate backtest CSVs first")
    out = pd.concat(frames, ignore_index=True)
    # Drop NaN
    out = out.dropna(subset=["raw_prob", "y_true"]).copy()
    out["y_true"] = out["y_true"].astype(int)
    return out


# ---------------------------------------------------------------------------
# Train/test split — fit calibrator on first 70% (chronological), eval on last 30%
# ---------------------------------------------------------------------------
def split_chronological(df: pd.DataFrame, frac: float = 0.7
                        ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    n = len(df)
    cut = int(n * frac)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


# ---------------------------------------------------------------------------
# Reliability table printer
# ---------------------------------------------------------------------------
def print_reliability(label: str, probs, y_true, n_bins: int = 10) -> None:
    rep = cal.reliability_table(probs, y_true, n_bins=n_bins)
    print(f"\n=== reliability — {label} (n={len(probs)}) ===")
    print(f"  {'bin':>11}  {'n':>4}  {'predicted':>10}  {'empirical':>10}  {'gap':>7}")
    for c, e, n in zip(rep["centers"], rep["empirical"], rep["n"]):
        gap = e - c
        print(f"  [{c-0.05:.2f}-{c+0.05:.2f}]  {n:>4}  {c:>10.3f}  {e:>10.3f}  {gap:>+7.3f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: List[str] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="fit_calibrator")
    p.add_argument("--n-bins", type=int, default=10)
    p.add_argument("--prior-alpha", type=float, default=20.0)
    p.add_argument("--dry-run", action="store_true",
                   help="Fit + report, do not promote.")
    p.add_argument("--force-promote", action="store_true",
                   help="Save the calibrated bundle even if metrics regress.")
    p.add_argument("--model-path", default="models/latest.pkl")
    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    df = load_holdout(repo_root)
    log.info("holdout total: %d rows", len(df))

    # Split chronologically — calibrator only sees the early portion.
    train, test = split_chronological(df, frac=0.7)
    log.info("split: %d train / %d test", len(train), len(test))

    # Fit
    calibrator = cal.BinnedIsotonicCalibrator(n_bins=args.n_bins,
                                              prior_alpha=args.prior_alpha)
    calibrator.fit(train["raw_prob"].values, train["y_true"].values)

    # Apply to test
    test_raw = test["raw_prob"].values
    test_y   = test["y_true"].values
    test_cal = calibrator.predict(test_raw)

    raw_brier = cal.brier_score(test_raw, test_y)
    cal_brier = cal.brier_score(test_cal, test_y)
    raw_ll    = cal.log_loss(test_raw, test_y)
    cal_ll    = cal.log_loss(test_cal, test_y)

    print("\n=== HOLDOUT METRICS (test = last 30% chronologically) ===")
    print(f"  Brier:  raw={raw_brier:.4f}  cal={cal_brier:.4f}  "
          f"delta={cal_brier - raw_brier:+.4f}  "
          f"({100*(cal_brier-raw_brier)/raw_brier:+.2f}%)")
    print(f"  LogL:   raw={raw_ll:.4f}  cal={cal_ll:.4f}  "
          f"delta={cal_ll - raw_ll:+.4f}  "
          f"({100*(cal_ll-raw_ll)/raw_ll:+.2f}%)")

    print_reliability("RAW   on test", test_raw, test_y)
    print_reliability("CALIB on test", test_cal, test_y)

    improved = (cal_brier < raw_brier) and (cal_ll < raw_ll)
    print(f"\nVerdict: {'IMPROVES' if improved else 'REGRESSES'} on holdout")

    if args.dry_run:
        print("\n--dry-run set — not modifying models/")
        return 0
    if not improved and not args.force_promote:
        print("\nMetrics regressed — refusing to promote.  "
              "Re-run with --force-promote to override (debugging only).")
        return 1

    # Attach calibrator to Stage 2 and save a NEW bundle.  The
    # model.save() hook will auto-archive the existing latest.pkl and
    # register the new version.
    print("\nLoading current bundle from %s..." % args.model_path)
    s1, s2 = md.load(args.model_path)
    s2.calibrator = calibrator
    print("Calibrator attached to Stage 2.  Saving...")
    md.save(s1, s2, args.model_path,
            label="v12+binned-isotonic-cal",
            metrics={"n_train": int(calibrator.fitted_n),
                     "walk_forward_roi": None,
                     "hit_rate": None,
                     "brier_raw": raw_brier,
                     "brier_cal": cal_brier,
                     "logloss_raw": raw_ll,
                     "logloss_cal": cal_ll})
    print("\nDONE — new active model has the calibrator attached.")
    print("Roll back with:")
    print("  python -m mlb_edge.model_registry list")
    print("  python -m mlb_edge.model_registry rollback <previous-id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
