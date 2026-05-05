"""
tools/fit_conviction.py
-----------------------
Fit a LearnedConvictionModel on the historical bet ledgers
(``bt_2023.csv`` + ``bt_2024.csv``) and evaluate it on a holdout
(``bt_2025.csv``).  Promote (save to ``models/conviction.json``) only
if the holdout log-loss beats the heuristic-tier baseline.

Baseline = "stake same multiplier per row regardless of features",
chosen to match the heuristic system: every PLATINUM and DIAMOND row
in the historical data was a real bet, so the fair comparison is
"learned LR vs constant per-tier multiplier".

Usage
=====
    python tools/fit_conviction.py             # fit + report + promote
    python tools/fit_conviction.py --dry-run   # fit + report, no save
    python tools/fit_conviction.py --force-promote
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mlb_edge.learned_conviction import LearnedConvictionModel, _extract_features
from mlb_edge.calibration import log_loss, brier_score

log = logging.getLogger("fit_conviction")


def _load_csv_rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    df = pd.read_csv(path)
    return df.to_dict(orient="records")


def main(argv: List[str] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="fit_conviction")
    p.add_argument("--l2", type=float, default=1.0,
                   help="L2 regularization strength (default: 1.0)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-promote", action="store_true")
    p.add_argument("--out", default="models/conviction.json")
    args = p.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]

    # Train: 2023 + 2024.  Holdout: 2025.
    train_rows = _load_csv_rows(repo / "bt_2023.csv") + _load_csv_rows(repo / "bt_2024.csv")
    test_rows  = _load_csv_rows(repo / "bt_2025.csv")
    log.info("train n=%d  test n=%d", len(train_rows), len(test_rows))
    if len(train_rows) < 30:
        raise SystemExit("not enough training data — populate bt_2023/2024 first")

    # Fit
    model = LearnedConvictionModel().fit(train_rows, l2_strength=args.l2,
                                          test_rows=test_rows or None)

    print("\n=== TRAINED MODEL ===")
    print(f"  n_train         = {model.n_train}")
    print(f"  train log_loss  = {model.train_log_loss:.4f}")
    print(f"  train accuracy  = {model.train_accuracy:.3f}")
    if model.test_log_loss is not None:
        print(f"  TEST  log_loss  = {model.test_log_loss:.4f}")
        print(f"  TEST  accuracy  = {model.test_accuracy:.3f}")

    print("\n=== feature coefficients (standardized) ===")
    for name, c in zip(model.feature_names, model.coef_):
        sign = "+" if c >= 0 else "-"
        print(f"  {name:18s}  {sign}{abs(c):.4f}")
    print(f"  intercept           {model.intercept_:+.4f}")

    # ---- Heuristic baseline: predict P(win) via uniform-per-tier hit rate ----
    # Compute training-set hit rate per tier; use it as the prediction for
    # every test row of that tier.  This is the "no learning" alternative.
    if test_rows:
        tier_hit = {}
        for t in set(r["tier"] for r in train_rows):
            n = sum(1 for r in train_rows if r["tier"] == t)
            w = sum(1 for r in train_rows if r["tier"] == t and str(r["won"]).lower()=="true")
            tier_hit[t] = (w / n) if n else 0.5
        baseline_p = np.array([tier_hit.get(r["tier"], 0.5) for r in test_rows])
        baseline_y = np.array([1 if str(r["won"]).lower()=="true" else 0 for r in test_rows])
        baseline_ll = log_loss(baseline_p, baseline_y)
        baseline_brier = brier_score(baseline_p, baseline_y)
        print(f"\n=== HEURISTIC BASELINE (per-tier hit rate from train) ===")
        print(f"  test log_loss  = {baseline_ll:.4f}")
        print(f"  test brier     = {baseline_brier:.4f}")

        # Compute learned model's brier on test
        lr_p = np.array([model.predict_win_prob(r) for r in test_rows])
        lr_brier = brier_score(lr_p, baseline_y)
        print(f"\n=== LEARNED MODEL (logistic regression) ===")
        print(f"  test log_loss  = {model.test_log_loss:.4f}")
        print(f"  test brier     = {lr_brier:.4f}")

        improved = (model.test_log_loss < baseline_ll) and (lr_brier < baseline_brier)
        print(f"\nVerdict: {'IMPROVES' if improved else 'REGRESSES'} vs heuristic")
    else:
        print("\nNo test holdout — skipping baseline comparison")
        improved = True

    if args.dry_run:
        print("\n--dry-run set; not writing models/conviction.json")
        return 0
    if not improved and not args.force_promote:
        print("\nMetrics regressed — refusing to promote.")
        print("Re-run with --force-promote to override (debugging only).")
        return 1

    out_path = repo / args.out
    model.save(out_path)
    print(f"\nDONE — wrote {out_path}")
    print("Set USE_LEARNED_CONVICTION=True in mlb_edge/config.py to enable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
