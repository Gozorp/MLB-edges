"""
verify_accuracy.py
------------------
Grade our model against ACTUAL outcomes from the backtest frame + compare to
the market (which is the best "external source" aggregator available — every
public MLB prediction site is trying to beat the same Pinnacle/DraftKings
close.)

We compute:
  1. Discrimination     : ROC AUC on home-team win probability
  2. Calibration        : bucket model_prob into 10 deciles; plot observed
                          win rate per bucket. A well-calibrated model has
                          observed = predicted within each bucket.
  3. Sharpness vs Market: log-loss of the model vs log-loss of the no-vig
                          market implied probability. The market is a VERY
                          strong baseline — beating it is hard.
  4. Betting ROI        : bankroll simulation from the CSV (already in the
                          file), printed alongside win rate.
  5. Spot checks        : top 5 highest-conviction bets and their outcomes.

Run:
    python verify_accuracy.py bt_2025_v6.csv
    python verify_accuracy.py bt_2024_v6.csv bt_2025_v6.csv   # concatenate
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def _load(paths):
    frames = []
    for p in paths:
        if not Path(p).exists():
            print(f"  WARN: {p} missing, skipping")
            continue
        df = pd.read_csv(p)
        df["source_file"] = Path(p).name
        frames.append(df)
    if not frames:
        raise SystemExit("No backtest files found.")
    return pd.concat(frames, ignore_index=True)


def _american_to_decimal(p):
    p = float(p)
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / (-p))


def _american_to_implied(p):
    """No-vig-unaware implied probability from American odds. Useful for
    comparison, not for true fair prob (that needs pairing with the other
    side to remove vig)."""
    p = float(p)
    return (-p) / (-p + 100) if p < 0 else 100.0 / (p + 100.0)


def report(df: pd.DataFrame, label: str) -> None:
    """
    The backtest CSV is the RECORD OF BETS PLACED — one row per bet the
    engine fired, not one row per game. So `prob` = the model's fair
    probability for the SIDE we bet (home or away), `fair` = market-implied
    fair on that same side, and `won` = whether the bet cashed.

    Rather than trying to reconstruct home-team probability across all games
    (we don't have the full frame here), we grade on BETTING perspective:
      * Calibration: bucket `prob` into deciles, check mean(won) per bucket.
      * Model vs market: log-loss(won ~ prob) vs log-loss(won ~ fair).
      * ROI & win-rate: straight from stake/pnl columns.
      * Spot checks: top-5 highest-edge bets, with actual outcome.
    """
    print(f"\n{'='*72}")
    print(f"  {label}   (n = {len(df)})")
    print('=' * 72)

    have = set(df.columns)
    has_prob = "prob" in have
    has_won = "won" in have
    has_fair = "fair" in have

    if has_prob and has_won:
        mask = df["prob"].notna() & df["won"].notna()
        if mask.any():
            y = df.loc[mask, "won"].astype(int).values
            p = df.loc[mask, "prob"].astype(float).clip(1e-4, 1 - 1e-4).values
            # AUC only meaningful when we have both classes; skip otherwise
            if len(np.unique(y)) > 1:
                auc = roc_auc_score(y, p)
                print(f"\n[1] Discrimination on bets placed "
                      f"(AUC of won ~ model_prob)")
                print(f"    AUC      = {auc:.4f}  "
                      f"(>0.50 means model's stronger picks win more often)")
            brier = brier_score_loss(y, p)
            ll = log_loss(y, p)
            print(f"    Brier    = {brier:.4f}")
            print(f"    Log loss = {ll:.4f}")

    # Calibration
    if has_prob and has_won and mask.sum() > 60:
        d = df.loc[mask, ["prob", "won"]].copy()
        # qcut with duplicates="drop" falls back gracefully when many
        # identical edges force bucket collapse.
        try:
            d["bucket"] = pd.qcut(d["prob"], 10, duplicates="drop")
        except ValueError:
            d["bucket"] = pd.cut(d["prob"], 10)
        grp = d.groupby("bucket", observed=True).agg(
            predicted=("prob", "mean"),
            observed=("won", "mean"),
            n=("won", "size"),
        )
        print(f"\n[2] Calibration (model says X%, observed win rate)")
        print(f"    {'bucket mean':>14}  {'observed':>10}  {'n':>6}"
              f"  {'gap (pp)':>10}")
        for _, r in grp.iterrows():
            gap_pp = (r["observed"] - r["predicted"]) * 100
            flag = "  *" if abs(gap_pp) > 5 else ""
            print(f"    {r['predicted']:>14.3f}  {r['observed']:>10.3f}"
                  f"  {int(r['n']):>6}  {gap_pp:>+9.1f}pp{flag}")
        max_gap = (grp["observed"] - grp["predicted"]).abs().max() * 100
        print(f"    Largest miscalibration: {max_gap:.1f}pp "
              f"({'good' if max_gap < 5 else 'CALIBRATION LAYER NEEDED'})")

    # Model vs market
    if has_prob and has_won and has_fair:
        mask = df["prob"].notna() & df["won"].notna() & df["fair"].notna()
        if mask.any():
            y = df.loc[mask, "won"].astype(int).values
            p_model = df.loc[mask, "prob"].astype(float).clip(1e-4, 1 - 1e-4).values
            p_mkt = df.loc[mask, "fair"].astype(float).clip(1e-4, 1 - 1e-4).values
            m_ll = log_loss(y, p_mkt)
            mdl_ll = log_loss(y, p_model)
            b_mkt = brier_score_loss(y, p_mkt)
            b_mdl = brier_score_loss(y, p_model)
            print(f"\n[3] Model vs Market (only on bets we placed — "
                  f"market is the tough baseline)")
            print(f"    Market  log-loss = {m_ll:.4f}   Brier = {b_mkt:.4f}")
            print(f"    Model   log-loss = {mdl_ll:.4f}   Brier = {b_mdl:.4f}")
            print(f"    Edge (log-loss)  = {m_ll - mdl_ll:+.4f} "
                  f"({'MODEL BETTER' if mdl_ll < m_ll else 'MARKET BETTER'})")

    # ROI
    if {"stake", "pnl"}.issubset(df.columns):
        stake = df["stake"].sum()
        pnl = df["pnl"].sum()
        roi = 100 * pnl / stake if stake else float("nan")
        wr = df["won"].mean() * 100 if "won" in df.columns else float("nan")
        print(f"\n[4] Betting performance")
        print(f"    Bets placed     = {len(df):,}")
        print(f"    Total stake     = {stake:,.2f}")
        print(f"    Total PnL       = {pnl:+,.2f}")
        print(f"    ROI             = {roi:+.2f}%")
        print(f"    Win rate        = {wr:.1f}%")

    # Tier-level breakdown if available
    if "tier" in df.columns:
        t = (df.assign(won_i=df["won"].astype(int))
               .groupby("tier")
               .agg(bets=("won_i", "size"),
                    win_rate=("won_i", "mean"),
                    stake=("stake", "sum"),
                    pnl=("pnl", "sum")))
        t["roi_pct"] = 100 * t["pnl"] / t["stake"]
        print(f"\n[4b] Per-tier breakdown")
        with pd.option_context("display.float_format", lambda x: f"{x:.3f}"):
            print(t.to_string())

    # Spot checks: top-5 highest-edge bets
    if "edge_pp" in df.columns:
        d = df.copy()
        d["abs_edge"] = d["edge_pp"].abs()
        d = d.sort_values("abs_edge", ascending=False).head(5)
        cols = [c for c in ("game_date", "team", "side", "prob", "fair",
                            "edge_pp", "decimal", "won", "pnl", "tier",
                            "source_file") if c in d.columns]
        print(f"\n[5] Top-5 highest-edge bets (these diverge most from market)")
        with pd.option_context("display.max_colwidth", 18,
                               "display.width", 160,
                               "display.float_format", lambda x: f"{x:.3f}"):
            print(d[cols].to_string(index=False))


def main():
    paths = sys.argv[1:]
    if not paths:
        paths = sorted(Path(".").glob("bt_*_v6.csv"))
        print(f"No args; auto-found: {[str(p) for p in paths]}")

    for p in paths:
        df = _load([p])
        report(df, f"FILE: {p}")

    if len(paths) > 1:
        df_all = _load(paths)
        report(df_all, "POOLED across all files")


if __name__ == "__main__":
    main()
