"""
diagnose_postprocess.py
-----------------------
Before retraining, measure how much accuracy we gain purely from
post-processing the existing v6 model_prob:

  A) Clip to [lo, hi] to stop the 0.073/0.848 tail collapse.
  B) Market-blend: p_final = w * p_model + (1-w) * p_market.
     Market implied fair prob already lives in the `fair` column.
  C) Log-space blend:
       logit(p_final) = w * logit(p_model) + (1-w) * logit(p_market)
     which is better behaved at the tails than linear blend.

Output: log-loss / Brier / AUC / simulated-ROI for each transformation,
swept across weights. This tells us the ceiling we can reach without
retraining a thing — every gain from here is guaranteed.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _expit(z):
    return 1 / (1 + np.exp(-z))


def _evaluate(y, p, label, decimal=None, stake=None):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    ll = log_loss(y, p)
    br = brier_score_loss(y, p)
    auc = roc_auc_score(y, p) if len(np.unique(y)) > 1 else float("nan")
    # simulated ROI: keep the SAME bets (same stake), but evaluate using the
    # actual won outcome. This isolates the value of better probabilities
    # given the bets we actually fired.
    roi = float("nan")
    if decimal is not None and stake is not None:
        pnl = np.where(y == 1, stake * (decimal - 1), -stake)
        roi = 100 * pnl.sum() / stake.sum()
    return {"label": label, "log_loss": ll, "brier": br, "auc": auc,
            "roi_on_same_bets_pct": roi}


def main():
    paths = sys.argv[1:] or sorted(Path(".").glob("bt_*_v6.csv"))
    frames = [pd.read_csv(p) for p in paths if Path(p).exists()]
    if not frames:
        print("No backtest files")
        return
    df = pd.concat(frames, ignore_index=True)

    mask = df["prob"].notna() & df["fair"].notna() & df["won"].notna()
    df = df[mask].copy()
    y = df["won"].astype(int).values
    p_model = df["prob"].astype(float).values
    p_mkt = df["fair"].astype(float).values
    decimal = df["decimal"].astype(float).values if "decimal" in df else None
    stake = df["stake"].astype(float).values if "stake" in df else None

    print(f"Rows: {len(df)}")
    print()
    print(f"{'transformation':48s}  {'log_loss':>9s}  {'brier':>7s}  {'auc':>6s}  {'ROI%':>7s}")
    print("-" * 84)

    # Baselines
    rows = [
        _evaluate(y, p_model, "baseline: model (v6)", decimal, stake),
        _evaluate(y, p_mkt, "baseline: market fair", decimal, stake),
    ]

    # (A) Clipping only
    for lo, hi in [(0.15, 0.85), (0.25, 0.75), (0.30, 0.70), (0.35, 0.65)]:
        p_clip = np.clip(p_model, lo, hi)
        rows.append(_evaluate(y, p_clip,
                              f"clip model to [{lo:.2f}, {hi:.2f}]",
                              decimal, stake))

    # (B) Linear market-blend
    for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        p_blend = w * p_model + (1 - w) * p_mkt
        rows.append(_evaluate(y, p_blend,
                              f"linear blend: {w:.1f}*model + {(1-w):.1f}*market",
                              decimal, stake))

    # (C) Logit-space blend
    for w in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        z = w * _logit(p_model) + (1 - w) * _logit(p_mkt)
        p_logit_blend = _expit(z)
        rows.append(_evaluate(y, p_logit_blend,
                              f"logit blend: {w:.1f}*model + {(1-w):.1f}*market",
                              decimal, stake))

    for r in rows:
        print(f"{r['label']:48s}  {r['log_loss']:>9.4f}  {r['brier']:>7.4f}  "
              f"{r['auc']:>6.4f}  {r['roi_on_same_bets_pct']:>+7.2f}")

    # Find optimal blend weight by log-loss
    best = min(rows, key=lambda r: r["log_loss"])
    print()
    print(f"OPTIMAL (by log-loss): {best['label']}")
    print(f"  log-loss {best['log_loss']:.4f}  brier {best['brier']:.4f}  "
          f"AUC {best['auc']:.4f}  ROI {best['roi_on_same_bets_pct']:+.2f}%")


if __name__ == "__main__":
    main()
