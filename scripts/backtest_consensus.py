"""
backtest_consensus.py
---------------------
Backtest the two-model consensus rule: main XGBoost + signal-meta LR.
Across all available historical caches, compute:

  - hit rate when BOTH models pick the same side
  - hit rate when they DISAGREE (split by who picks home vs away)
  - hit rate of "bet only when consensus" filter
  - hit rate of "bet sig-meta when |diff| > X" filter

This is in-sample (both models trained on these data) so absolute numbers
are inflated. The RELATIVE ordering (consensus vs disagreement zones) is
the meaningful signal.
"""
from __future__ import annotations
import sys, os
from pathlib import Path
import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from mlb_edge.model import predict as mlb_predict
from scripts.train_signal_meta import vote_to_int


def main() -> int:
    print("Loading caches…")
    cache_dir = ROOT / "data" / "feature_cache"
    frames = []
    for season in (2023, 2024, 2025):
        for ver in ("v12", "v11", "v10", "v9"):
            p = cache_dir / f"features_{season}_full_1_{ver}.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                df["__season"] = season
                frames.append(df)
                print(f"  loaded {p.name}: {len(df)} games")
                break
    train = pd.concat(frames, ignore_index=True)
    train = train.dropna(subset=["home_win"]).reset_index(drop=True)
    print(f"  total: {len(train)} games\n")

    print("Loading models…")
    main_models = joblib.load(ROOT / "models" / "latest.pkl")
    meta = joblib.load(ROOT / "models" / "signal_meta.pkl")

    # Score with main model (XGBoost two-stage)
    print("Scoring with main XGBoost…")
    pred = mlb_predict(main_models["stage1"], main_models["stage2"], train)
    main_p = pred["model_prob"].values

    # Score with signal-meta
    print("Scoring with signal-meta…")
    rows = []
    for _, r in train.iterrows():
        v = vote_to_int(r)
        rows.append([v["F1"], v["F2"], v["F3"], v["F4"], v["F5"]])
    sig_X = pd.DataFrame(rows, columns=["F1","F2","F3","F4","F5"]).values
    sig_p = meta["model"].predict_proba(sig_X)[:, 1]

    # Build summary frame
    train["main_p"] = main_p
    train["sig_p"] = sig_p
    train["main_pick_home"] = train["main_p"] >= 0.5
    train["sig_pick_home"] = train["sig_p"] >= 0.5
    train["consensus"] = (train["main_pick_home"] == train["sig_pick_home"])
    train["abs_diff"] = (train["main_p"] - train["sig_p"]).abs()
    train["main_correct"] = (train["main_pick_home"] == (train["home_win"] == 1))
    train["sig_correct"] = (train["sig_pick_home"] == (train["home_win"] == 1))

    print()
    print("=" * 76)
    print("  BACKTEST RESULTS — Main XGBoost vs Signal-meta consensus")
    print("=" * 76)
    print()

    n_total = len(train)
    print(f"  Total games:                       {n_total}")
    print(f"  Main alone hit rate:               {train['main_correct'].mean():.1%}")
    print(f"  Sig-meta alone hit rate:           {train['sig_correct'].mean():.1%}")
    print()

    # Consensus zone
    consensus = train[train["consensus"]]
    print(f"  Games where models AGREE:          {len(consensus)} ({len(consensus)/n_total:.0%})")
    print(f"    Hit rate:                        {consensus['main_correct'].mean():.1%}")
    print()

    # Disagreement zone
    disagree = train[~train["consensus"]]
    main_in_dis = disagree["main_correct"].mean()
    sig_in_dis = disagree["sig_correct"].mean()
    print(f"  Games where models DISAGREE:       {len(disagree)} ({len(disagree)/n_total:.0%})")
    print(f"    Main right:                      {main_in_dis:.1%}")
    print(f"    Sig-meta right:                  {sig_in_dis:.1%}")
    print(f"    Better choice:                   {'sig-meta' if sig_in_dis > main_in_dis else 'main'}")
    print()

    # By disagreement magnitude
    print("  Hit rate by disagreement magnitude (|main - sig|):")
    print(f"  {'Range':<14} {'N':>6} {'Main':>7} {'Sig':>7}  Better")
    bands = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 1.0)]
    for lo, hi in bands:
        sub = train[(train["abs_diff"] >= lo) & (train["abs_diff"] < hi)]
        if len(sub) == 0: continue
        m = sub["main_correct"].mean()
        s = sub["sig_correct"].mean()
        better = "sig" if s > m + 0.01 else ("main" if m > s + 0.01 else "tie")
        print(f"    {lo:.2f}-{hi:.2f}     {len(sub):>6} {m:>6.1%} {s:>6.1%}  {better}")

    # Specifically: when |diff| is HUGE, who wins?
    print()
    print("  ─" * 35)
    print("  CONSENSUS RULE — bet only when both models agree:")
    print(f"    Volume drop:      {n_total} → {len(consensus)} ({len(consensus)/n_total:.0%})")
    print(f"    Hit rate uplift:  {train['main_correct'].mean():.1%} → {consensus['main_correct'].mean():.1%}")
    print(f"    Δ:                {(consensus['main_correct'].mean() - train['main_correct'].mean())*100:+.1f}pp")
    print()
    print("  HYBRID RULE — main when consensus, sig-meta when disagree:")
    hybrid_correct = (
        (train["consensus"] & train["main_correct"]) |
        (~train["consensus"] & train["sig_correct"])
    ).mean()
    print(f"    Hit rate:         {hybrid_correct:.1%}")
    print(f"    Δ vs main alone:  {(hybrid_correct - train['main_correct'].mean())*100:+.1f}pp")

    return 0


if __name__ == "__main__":
    sys.exit(main())
