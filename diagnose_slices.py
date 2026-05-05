"""
diagnose_slices.py
------------------
For each slice of the v6 backtest, compare our model's log-loss to market's.
If there's ANY subset where we beat market consistently, we should specialize
there. If not, the market is the model.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss


def _ll(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return log_loss(y, p)


def slice_report(df, slice_col, slice_name):
    if slice_col not in df.columns:
        return
    print(f"\n=== By {slice_name} ===")
    print(f"{'slice':20s}  {'n':>4s}  {'mkt_ll':>7s}  {'mdl_ll':>7s}  "
          f"{'edge':>7s}  {'wr':>6s}  {'roi%':>7s}")
    for val, sub in df.groupby(slice_col):
        if len(sub) < 20:
            continue
        y = sub["won"].astype(int).values
        pm = sub["prob"].astype(float).values
        pk = sub["fair"].astype(float).values
        mll = _ll(y, pk)
        mdll = _ll(y, pm)
        wr = y.mean() * 100
        if {"stake", "pnl"}.issubset(sub.columns):
            roi = 100 * sub["pnl"].sum() / sub["stake"].sum()
        else:
            roi = float("nan")
        print(f"{str(val):20s}  {len(sub):>4d}  {mll:>7.4f}  {mdll:>7.4f}  "
              f"{mll - mdll:>+7.4f}  {wr:>5.1f}%  {roi:>+6.2f}%")


def main():
    paths = sys.argv[1:] or sorted(Path(".").glob("bt_*_v6.csv"))
    frames = [pd.read_csv(p) for p in paths if Path(p).exists()]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["prob"].notna() & df["fair"].notna() & df["won"].notna()].copy()

    # Derived slices
    df["side"] = df["side"].astype(str)
    df["edge_bucket"] = pd.cut(df["edge_pp"].abs(),
                                bins=[0, 5, 10, 20, 100],
                                labels=["0-5pp", "5-10pp", "10-20pp", "20pp+"])
    df["month"] = pd.to_datetime(df["game_date"]).dt.month.astype(str) + "_" + \
                  pd.to_datetime(df["game_date"]).dt.month_name().str[:3]
    df["prob_bucket"] = pd.cut(df["prob"],
                                bins=[0, 0.3, 0.45, 0.55, 0.7, 1.0],
                                labels=["<30", "30-45", "45-55", "55-70", ">70"])
    df["fair_bucket"] = pd.cut(df["fair"],
                                bins=[0, 0.3, 0.45, 0.55, 0.7, 1.0],
                                labels=["<30", "30-45", "45-55", "55-70", ">70"])

    print(f"Overall n = {len(df)}")
    y = df["won"].astype(int).values
    print(f"Overall: market ll = {_ll(y, df['fair'].values):.4f}, "
          f"model ll = {_ll(y, df['prob'].values):.4f}, "
          f"edge = {_ll(y, df['fair'].values) - _ll(y, df['prob'].values):+.4f}")

    slice_report(df, "tier", "tier")
    slice_report(df, "side", "side (home/away)")
    slice_report(df, "edge_bucket", "edge size")
    slice_report(df, "source_file" if "source_file" in df else None, "season")
    slice_report(df, "month", "month")
    slice_report(df, "prob_bucket", "our model prob bucket")
    slice_report(df, "fair_bucket", "market fair bucket")


if __name__ == "__main__":
    main()
