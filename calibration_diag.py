"""Calibration diagnostic: compare model predictions vs actual game results.

For each slate date in --dates (or default: 2026-04-25 .. 2026-04-27), this:
  1. Loads the audit CSV for the slate (per-game model predictions, tier)
  2. Pulls actual final scores from the MLB Stats API
  3. Joins on (away, home) team abbrevs and computes per-row hit/Brier/log-loss
  4. Prints overall + per-tier metrics for each date
  5. Prints a simple calibration table (predicted-prob bin -> empirical hit rate)

Two audit-CSV schemas are supported:
  - new (04-26, 04-27, 04-28): away,home,pick,pick_prob,...,tier,signals,notes
  - old (04-25): matchup ("AWAY@HOME"),...,home_model_prob,tier,...

Usage:
    python calibration_diag.py
    python calibration_diag.py --dates 2026-04-25 2026-04-26 2026-04-27
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from mlb_edge.stadiums import normalize_team

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def fetch_outcomes(d: date) -> pd.DataFrame:
    r = requests.get(
        SCHEDULE_URL,
        params={"sportId": 1, "date": d.isoformat(), "hydrate": "linescore"},
        timeout=20,
    )
    r.raise_for_status()
    rows = []
    for dd in r.json().get("dates", []):
        for g in dd.get("games", []):
            state = (g.get("status", {}) or {}).get("detailedState", "")
            if state not in ("Final", "Game Over", "Completed Early"):
                continue
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            try:
                hr = int(home.get("score", 0))
                ar = int(away.get("score", 0))
            except (TypeError, ValueError):
                continue
            rows.append({
                "home": normalize_team(home.get("team", {}).get("name", "")),
                "away": normalize_team(away.get("team", {}).get("name", "")),
                "home_R": hr,
                "away_R": ar,
                "run_diff": abs(hr - ar),
            })
    return pd.DataFrame(rows)


def load_audit_normalized(d: date) -> pd.DataFrame:
    """Return [away, home, p_home, tier, signals] for either audit schema."""
    p = Path(f"audit_{d.isoformat()}.csv")
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)

    if "home_model_prob" in df.columns and "matchup" in df.columns:
        # Old schema (04-25)
        m = df["matchup"].str.split("@", expand=True)
        df["away"] = m[0].apply(normalize_team)
        df["home"] = m[1].apply(normalize_team)
        df["p_home"] = pd.to_numeric(df["home_model_prob"], errors="coerce")
        df["signals"] = df.get("signals_fired", "")
        return df[["away", "home", "p_home", "tier", "signals"]].copy()

    # New schema (04-26+)
    df["away"] = df["away"].apply(normalize_team)
    df["home"] = df["home"].apply(normalize_team)
    df["pick"] = df["pick"].apply(normalize_team)
    pp = pd.to_numeric(df["pick_prob"], errors="coerce") / 100.0
    df["p_home"] = pp.where(df["pick"] == df["home"], 1.0 - pp)
    df["signals"] = df["signals"].fillna("")
    return df[["away", "home", "p_home", "tier", "signals"]].copy()


def per_row_metrics(joined: pd.DataFrame) -> pd.DataFrame:
    j = joined.copy()
    j["home_won"] = (j["home_R"] > j["away_R"]).astype(int)
    p = j["p_home"].clip(1e-6, 1 - 1e-6)
    j["brier"] = (p - j["home_won"]) ** 2
    j["log_loss"] = -(j["home_won"] * p.apply(math.log)
                      + (1 - j["home_won"]) * (1 - p).apply(math.log))
    j["pick_home"] = (p >= 0.5).astype(int)
    j["pick_correct"] = (j["pick_home"] == j["home_won"]).astype(int)
    j["pick_prob"] = p.where(p >= 0.5, 1 - p)
    return j


def summarize(j: pd.DataFrame, label: str) -> dict:
    if j.empty:
        return {"label": label, "n": 0}
    out = {
        "label": label, "n": len(j),
        "brier": j["brier"].mean(),
        "log_loss": j["log_loss"].mean(),
        "hit_rate": j["pick_correct"].mean(),
        "avg_pick_prob": j["pick_prob"].mean(),
    }
    return out


def calibration_table(j: pd.DataFrame) -> pd.DataFrame:
    """Reliability bins on the home-perspective probability."""
    if j.empty:
        return pd.DataFrame()
    bins = [0, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 1.0]
    labels = [f"{bins[i]:.2f}-{bins[i+1]:.2f}" for i in range(len(bins) - 1)]
    j2 = j.copy()
    j2["bin"] = pd.cut(j2["p_home"], bins=bins, labels=labels, include_lowest=True)
    g = j2.groupby("bin", observed=True).agg(
        n=("home_won", "size"),
        empirical=("home_won", "mean"),
        predicted=("p_home", "mean"),
        brier=("brier", "mean"),
    ).reset_index()
    g["gap_pp"] = (g["predicted"] - g["empirical"]) * 100
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=None)
    args = ap.parse_args()

    if args.dates:
        dates = [datetime.strptime(s, "%Y-%m-%d").date() for s in args.dates]
    else:
        dates = [date(2026, 4, 25), date(2026, 4, 26), date(2026, 4, 27)]

    summaries = []
    all_joined = []
    print("=" * 72)
    print(f"  CALIBRATION DIAGNOSTIC — {dates[0]} .. {dates[-1]}")
    print("=" * 72)
    for d in dates:
        a = load_audit_normalized(d)
        if a.empty:
            print(f"\n[{d}] no audit file, skipping")
            continue
        outs = fetch_outcomes(d)
        if outs.empty:
            print(f"\n[{d}] no completed outcomes from API, skipping")
            continue
        joined = a.merge(outs, on=["away", "home"], how="inner")
        if joined.empty:
            print(f"\n[{d}] audit/outcome merge empty (audit n={len(a)}, outcomes n={len(outs)})")
            continue
        j = per_row_metrics(joined)
        all_joined.append(j.assign(slate=d))
        s = summarize(j, str(d))
        summaries.append(s)
        print(f"\n[{d}] n={s['n']}  brier={s['brier']:.4f}  logloss={s['log_loss']:.4f}  "
              f"hit_rate={s['hit_rate']:.3f}  avg_pick_prob={s['avg_pick_prob']:.3f}")

        # Per-tier
        print(f"  per-tier:")
        for tier, sub in j.groupby("tier"):
            ss = summarize(sub, tier)
            print(f"    {tier:<10s} n={ss['n']:2d}  brier={ss['brier']:.4f}  "
                  f"logloss={ss['log_loss']:.4f}  hit_rate={ss['hit_rate']:.3f}  "
                  f"avg_pick_prob={ss['avg_pick_prob']:.3f}")

        # Per-game
        print("  per-game:")
        for _, r in j.iterrows():
            outcome = "WIN " if r["pick_correct"] else "LOSS"
            pick = r["home"] if r["p_home"] >= 0.5 else r["away"]
            print(f"    {outcome}  {r['away']} @ {r['home']}  "
                  f"pick={pick} p={r['pick_prob']:.3f}  "
                  f"final={r['away_R']}-{r['home_R']}  tier={r['tier']:<10s}  "
                  f"brier={r['brier']:.3f}")

    if not all_joined:
        return

    full = pd.concat(all_joined, ignore_index=True)
    print("\n" + "=" * 72)
    print(f"  POOLED ({len(full)} games)")
    print("=" * 72)
    s = summarize(full, "pooled")
    print(f"  brier={s['brier']:.4f}  logloss={s['log_loss']:.4f}  "
          f"hit_rate={s['hit_rate']:.3f}  avg_pick_prob={s['avg_pick_prob']:.3f}")

    # Reference baselines:
    #   coin flip => brier=0.25, logloss=0.6931
    #   well-calibrated MLB H2H ~ brier 0.23-0.24, logloss ~0.66
    print(f"  reference: coin-flip brier=0.2500 logloss=0.6931")
    print(f"             home-base 0.54 brier=0.2484 logloss=0.6883 (always pick home @ 0.54)")

    print("\n  per-tier (pooled):")
    for tier, sub in full.groupby("tier"):
        ss = summarize(sub, tier)
        print(f"    {tier:<10s} n={ss['n']:2d}  brier={ss['brier']:.4f}  "
              f"logloss={ss['log_loss']:.4f}  hit_rate={ss['hit_rate']:.3f}  "
              f"avg_pick_prob={ss['avg_pick_prob']:.3f}")

    print("\n  reliability bins (home-perspective prob):")
    print("  " + calibration_table(full).to_string(index=False))

    # Per-day delta vs. pooled mean (for drift detection)
    pooled_brier = s["brier"]
    print("\n  per-day vs pooled brier (drift indicator):")
    for ss in summaries:
        delta = ss["brier"] - pooled_brier
        flag = "  <-- worse" if delta > 0.04 else ("  <-- better" if delta < -0.04 else "")
        print(f"    {ss['label']}  brier={ss['brier']:.4f}  delta={delta:+.4f}{flag}")


if __name__ == "__main__":
    main()
