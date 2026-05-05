"""
diagnose_filters.py
-------------------
The slice analysis found the model DOES beat market in specific zones:
  * Edge 5-10pp            -> +0.0086 log-loss, +8.58% ROI (260 bets)
  * Side = away            -> +7.28% ROI (139 bets)
  * Market fair 0.55-0.70  -> +4.34% ROI (77 bets)
  * Model prob 0.45-0.55   -> +5.02% ROI (334 bets)

And the killer zones we should NEVER bet:
  * Edge 20pp+             -> -36.34% ROI (46 bets) — false extreme-edge signals
  * Market fair <0.30      -> -55.51% ROI (29 bets) — big underdogs
  * Side = home            -> -5.11% ROI (511 bets, most of our volume)

This script tries combined filters and ranks them.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import numpy as np


def run_strategy(df, mask, label):
    sub = df[mask]
    if len(sub) < 5:
        return None
    stake = sub["stake"].sum()
    pnl = sub["pnl"].sum()
    roi = 100 * pnl / stake if stake else float("nan")
    wr = sub["won"].mean() * 100
    return {"label": label, "bets": len(sub), "stake": stake, "pnl": pnl,
            "roi_pct": roi, "win_rate_pct": wr}


def main():
    paths = sys.argv[1:] or sorted(Path(".").glob("bt_*_v6.csv"))
    frames = [pd.read_csv(p) for p in paths if Path(p).exists()]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["prob"].notna() & df["fair"].notna() & df["won"].notna()].copy()

    print(f"Pooled n = {len(df)}  | Baseline ROI = "
          f"{100*df['pnl'].sum()/df['stake'].sum():.2f}%  "
          f"Win rate = {df['won'].mean()*100:.1f}%")
    print()

    strategies = [
        # Singles
        ("edge in [5,10]",              df["edge_pp"].abs().between(5, 10)),
        ("edge in [3,10]",              df["edge_pp"].abs().between(3, 10)),
        ("edge in [5,15]",              df["edge_pp"].abs().between(5, 15)),
        ("away side only",              df["side"] == "away"),
        ("market fair >= 0.45",         df["fair"] >= 0.45),
        ("market fair >= 0.40",         df["fair"] >= 0.40),
        ("model prob 0.45-0.55",        df["prob"].between(0.45, 0.55)),
        ("NOT edge 20pp+",              df["edge_pp"].abs() <= 20),
        ("NOT market fair <0.30",       df["fair"] >= 0.30),

        # Combos
        ("edge[5,10] + away",
         df["edge_pp"].abs().between(5, 10) & (df["side"] == "away")),
        ("edge[5,10] + fair>=0.40",
         df["edge_pp"].abs().between(5, 10) & (df["fair"] >= 0.40)),
        ("edge[5,10] + fair>=0.45",
         df["edge_pp"].abs().between(5, 10) & (df["fair"] >= 0.45)),
        ("edge[3,10] + fair>=0.40",
         df["edge_pp"].abs().between(3, 10) & (df["fair"] >= 0.40)),
        ("edge[5,10] + NOT fair<0.30",
         df["edge_pp"].abs().between(5, 10) & (df["fair"] >= 0.30)),
        ("edge[5,10] + prob[0.45,0.65]",
         df["edge_pp"].abs().between(5, 10) &
         df["prob"].between(0.45, 0.65)),
        ("tri-filter: edge[5,10] + fair>=0.40 + prob[0.45,0.65]",
         df["edge_pp"].abs().between(5, 10) &
         (df["fair"] >= 0.40) &
         df["prob"].between(0.45, 0.65)),

        # Per-tier
        ("DIAMOND + edge[5,10]",
         (df["tier"] == "DIAMOND") & df["edge_pp"].abs().between(5, 10)),
        ("PLATINUM + edge[5,10]",
         (df["tier"] == "PLATINUM") & df["edge_pp"].abs().between(5, 10)),
    ]

    results = []
    for label, mask in strategies:
        r = run_strategy(df, mask, label)
        if r:
            results.append(r)

    results.sort(key=lambda r: -r["roi_pct"])
    print(f"{'strategy':56s}  {'bets':>5s}  {'wr':>5s}  {'ROI':>8s}")
    print("-" * 85)
    for r in results:
        print(f"{r['label']:56s}  {r['bets']:>5d}  {r['win_rate_pct']:>4.1f}%  "
              f"{r['roi_pct']:>+7.2f}%")

    # Per-season validation on the best combo so we don't overfit to pooled
    best = results[0]
    print()
    print(f"=== Per-season ROI for best strategy: '{best['label']}' ===")
    for season in ["bt_2023_v6.csv", "bt_2024_v6.csv", "bt_2025_v6.csv"]:
        if "source_file" in df.columns:
            sub = df[df["source_file"] == season]
        else:
            # recompute by reloading
            p = Path(season)
            if not p.exists():
                continue
            sub = pd.read_csv(p)
            sub = sub[sub["prob"].notna() & sub["fair"].notna() & sub["won"].notna()]
        # apply the same filter logic
        # crude re-application: eval the label string (okay since it's our own)
        if "edge[5,10]" in best["label"] and "fair>=0.40" in best["label"]:
            mask = (sub["edge_pp"].abs().between(5, 10) &
                    (sub["fair"] >= 0.40))
        elif "edge[5,10] + away" in best["label"]:
            mask = (sub["edge_pp"].abs().between(5, 10) &
                    (sub["side"] == "away"))
        elif "away side only" in best["label"]:
            mask = sub["side"] == "away"
        elif best["label"].startswith("edge in [5,10]"):
            mask = sub["edge_pp"].abs().between(5, 10)
        else:
            mask = pd.Series(True, index=sub.index)
        cut = sub[mask]
        if len(cut) == 0:
            print(f"  {season}: no bets match")
            continue
        roi = 100 * cut["pnl"].sum() / cut["stake"].sum()
        wr = cut["won"].mean() * 100
        print(f"  {season}: {len(cut):>3d} bets  wr {wr:.1f}%  ROI {roi:+.2f}%")


if __name__ == "__main__":
    main()
