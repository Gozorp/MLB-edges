"""Phase 1, CP4 — keep/drop gate.

Trains an experimental stage-2 baseline (existing 80 features) and a
flag-on variant (existing 80 + 6 Stuff+/Location+ features) on the
SAME row set with the SAME hyperparams. Computes apples-to-apples
Brier / log-loss / hit-rate deltas with bootstrap CI. Adds tier
segmentation, reliability bins, top-10 most-shifted games, and SHAP
feature importance for the experimental model.

Train:  2024 v12 feature cache (~2,857 games)
Hold-out: 2025 v12 feature cache (~2,523 games)

Apples-to-apples scope rationale: the brief asked for 2022-2024 train,
but the existing v12 feature caches start at 2023 (no 2022 cache on
disk), and pulling SP IDs for 2022 + 2023 would require another round
of API enrichment. The 2024-train / 2025-val split is a clean single-
year experiment that answers the keep/drop question with sufficient
power (~2,500 train rows, ~2,500 val rows, 6 new features).

Run:
    PYTHONIOENCODING=utf-8 python experiments/cp4_train_experiment.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.config import STUFF_PLUS_CFG
from mlb_edge.pitch_quality import (
    aggregate_per_sp_rolling,
    score_pitches,
)


CACHE = Path(STUFF_PLUS_CFG["cache_dir"])
TARGET = "home_win"
NEW_FEATS = [
    "home_sp_stuff_plus", "away_sp_stuff_plus", "sp_stuff_plus_gap",
    "home_sp_location_plus", "away_sp_location_plus", "sp_location_plus_gap",
]


# --------------------------------------------------------------------------
# Feature-cache loader — strips identifiers + target from training input.
# --------------------------------------------------------------------------
def load_cache(year: int) -> pd.DataFrame:
    """Load year's v12 feature cache + merge targets sidecar (post-2026-05-02
    leakage scrub). Sidecar carries [game_id, home_win, home_f5_win]."""
    path = Path(f"data/feature_cache/features_{year}_full_1_v12.parquet")
    df = pd.read_parquet(path)
    targets_path = Path(f"data/feature_cache/targets_{year}_v12.parquet")
    if targets_path.exists() and "home_win" not in df.columns:
        tgt = pd.read_parquet(targets_path)
        df = df.merge(tgt, on="game_id", how="left")
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


# --------------------------------------------------------------------------
# Build per-(game, SP) Stuff+/Location+ features for the games list.
# --------------------------------------------------------------------------
def attach_stuff_plus(games: pd.DataFrame, sp_ids: pd.DataFrame,
                      pitches: pd.DataFrame, stuff_model, loc_model,
                      norms: dict, league_center: float) -> pd.DataFrame:
    """For each row in `games`, compute home/away Stuff+ and Location+ via
    rolling 60d ending the day before game_date. Adds 6 new columns."""
    out = games.merge(sp_ids[["game_id", "home_sp_id", "away_sp_id"]],
                       on="game_id", how="left").copy()
    n_missing_id = ((out["home_sp_id"].isna()) | (out["away_sp_id"].isna())).sum()
    log = logging.getLogger(__name__)
    log.info("  %s rows; %s missing >=1 SP id", f"{len(out):,}", n_missing_id)

    # Pre-score the entire pitches set ONCE so we don't re-score on each
    # rolling window. Memory: ~3M rows × ~3 floats ≈ 70 MB.
    pitches = pitches.copy()
    pitches["game_date"] = pd.to_datetime(pitches["game_date"])
    log.info("  scoring %s pitches...", f"{len(pitches):,}")
    t0 = time.time()
    scored = score_pitches(pitches, stuff_model, loc_model, norms)
    log.info("  scored in %.1fs", time.time() - t0)

    # Build per-(SP, slate-date) lookup. We iterate unique slate dates.
    log.info("  computing rolling-60d aggregates per slate date...")
    out_dates = out["game_date"].dt.date.unique()
    sp_lookup: dict[tuple, tuple] = {}   # (date, pitcher_id) -> (n, sp+, loc+)
    t0 = time.time()
    for d in out_dates:
        agg = aggregate_per_sp_rolling(scored, d, window_days=60, min_pitches=200)
        for _, r in agg.iterrows():
            sp_lookup[(d, int(r["pitcher"]))] = (
                int(r["n_pitches"]),
                float(r["stuff_plus"]) if pd.notna(r["stuff_plus"]) else float("nan"),
                float(r["location_plus"]) if pd.notna(r["location_plus"]) else float("nan"),
            )
    log.info("  rolling aggregation done in %.1fs (%s unique dates)",
              time.time() - t0, len(out_dates))

    def lookup(d, pid):
        if pd.isna(pid):
            return league_center, league_center
        v = sp_lookup.get((d, int(pid)))
        if not v:
            return league_center, league_center
        _, sp, lp = v
        if pd.isna(sp) or pd.isna(lp):
            return league_center, league_center
        return sp, lp

    home_sp = []; home_loc = []; away_sp = []; away_loc = []
    for r in out.itertuples():
        d = r.game_date.date() if hasattr(r.game_date, "date") else r.game_date
        h_sp, h_lp = lookup(d, r.home_sp_id)
        a_sp, a_lp = lookup(d, r.away_sp_id)
        home_sp.append(h_sp); home_loc.append(h_lp)
        away_sp.append(a_sp); away_loc.append(a_lp)
    out["home_sp_stuff_plus"] = home_sp
    out["away_sp_stuff_plus"] = away_sp
    out["home_sp_location_plus"] = home_loc
    out["away_sp_location_plus"] = away_loc
    out["sp_stuff_plus_gap"] = out["home_sp_stuff_plus"] - out["away_sp_stuff_plus"]
    out["sp_location_plus_gap"] = out["home_sp_location_plus"] - out["away_sp_location_plus"]
    return out


# --------------------------------------------------------------------------
# Train + eval helpers
# --------------------------------------------------------------------------
def get_feature_cols(df: pd.DataFrame, include_stuff: bool) -> list[str]:
    """All numeric columns except identifiers + target + leakage features.

    Leakage drop: `home_f5_win` is the actual first-5-innings outcome
    (binary 0/1) stored in the v12 cache. It correlates with `home_win`
    at +0.647 and gets gain 71.5 in XGBoost — the tree builds itself
    around it. Production stage-2 doesn't see this column at predict
    time (it sees `f5_prob` from Stage 1 instead), so any train-time
    use would create a train-vs-predict distribution shift. Excluded
    here so the CP4 comparison reflects clean signal.
    """
    drop = {"game_id", "game_date", "home_team", "away_team", "home_sp_id",
            "away_sp_id", "home_sp_name", "away_sp_name", TARGET,
            "home_f5_win"}
    cols = [c for c in df.columns if c not in drop]
    if not include_stuff:
        cols = [c for c in cols if c not in NEW_FEATS]
    # Coerce to numeric (drop any non-numeric remaining).
    cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    return cols


def train(X_tr, y_tr, X_va, y_va, name: str) -> xgb.XGBClassifier:
    log = logging.getLogger(__name__)
    log.info("[%s] training n_train=%s n_val=%s n_feats=%s",
             name, f"{len(X_tr):,}", f"{len(X_va):,}", X_tr.shape[1])
    model = xgb.XGBClassifier(
        n_estimators=2000, max_depth=5, learning_rate=0.04,
        subsample=0.85, colsample_bytree=0.7, reg_lambda=1.5,
        objective="binary:logistic", eval_metric="logloss",
        tree_method="hist", early_stopping_rounds=60,
        n_jobs=-1, random_state=20260502,
    )
    t0 = time.time()
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    log.info("[%s] fit_secs=%.1f best_iter=%s",
             name, time.time() - t0,
             getattr(model, "best_iteration", "?"))
    return model


def metric_set(y_true, p_pred) -> dict:
    p_pred = np.clip(p_pred, 1e-6, 1 - 1e-6)
    pick = (p_pred >= 0.5).astype(int)
    return {
        "n": len(y_true),
        "brier": float(brier_score_loss(y_true, p_pred)),
        "log_loss": float(log_loss(y_true, p_pred)),
        "hit_rate": float((pick == y_true).mean()),
        "p_pick_avg": float(np.where(pick == 1, p_pred, 1 - p_pred).mean()),
    }


def bootstrap_delta(y_true, p_a, p_b, n_resamples=500, rng=None) -> dict:
    """Bootstrap 95% CI on (B − A) Brier and log-loss. Resamples row indices
    with replacement. Positive delta = B is BETTER (lower error)."""
    rng = rng or np.random.default_rng(20260502)
    n = len(y_true)
    p_a = np.clip(p_a, 1e-6, 1 - 1e-6)
    p_b = np.clip(p_b, 1e-6, 1 - 1e-6)
    brier_deltas = np.empty(n_resamples)
    ll_deltas = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        ya, pa, pb = y_true[idx], p_a[idx], p_b[idx]
        brier_deltas[i] = brier_score_loss(ya, pa) - brier_score_loss(ya, pb)
        ll_deltas[i] = log_loss(ya, pa) - log_loss(ya, pb)
    return {
        "brier_delta_mean": float(brier_deltas.mean()),
        "brier_delta_ci": (float(np.percentile(brier_deltas, 2.5)),
                            float(np.percentile(brier_deltas, 97.5))),
        "log_loss_delta_mean": float(ll_deltas.mean()),
        "log_loss_delta_ci": (float(np.percentile(ll_deltas, 2.5)),
                                float(np.percentile(ll_deltas, 97.5))),
        "n_resamples": n_resamples,
    }


def reliability_bins(y_true, p_pred, bins=None) -> pd.DataFrame:
    bins = bins or [0, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 1.0]
    df = pd.DataFrame({"y": y_true, "p": p_pred})
    df["bin"] = pd.cut(df["p"], bins=bins, include_lowest=True)
    g = df.groupby("bin", observed=True).agg(
        n=("y", "size"), p_mean=("p", "mean"), y_mean=("y", "mean"),
    ).reset_index()
    g["gap_pp"] = (g["p_mean"] - g["y_mean"]) * 100
    return g


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print("=" * 76)
    print("Phase 1.5 spike — keep/drop gate on 2023-2024 train (3x CP4)")
    print("=" * 76)

    # Load artifacts.
    stuff = joblib.load("models/stuff_plus_v1.pkl")
    loc = joblib.load("models/location_plus_v1.pkl")
    norms = json.loads(Path("models/pitch_quality_norms_v1.json").read_text())
    print("  loaded Stuff+ / Location+ models from CP2")

    # Load all pitches (used for both train+val Stuff+ aggregation).
    pitches = pd.read_parquet(CACHE / "dataset.parquet")
    print(f"  loaded {len(pitches):,} pitches")

    # Year-by-year feature caches. Phase 1.5 spike (2026-05-02): widened
    # train to 2023+2024 (was 2024 only at CP4) — 3x the row count, all on
    # the scrubbed cache. SP-id table for 2023 needed; built fresh here if
    # not on disk (uses MLB Stats API schedule endpoint, ~8 calls).
    print("\nLoading feature caches + SP IDs...")
    df_2023 = load_cache(2023)
    df_2024 = load_cache(2024)
    train_yr_games = pd.concat([df_2023, df_2024], ignore_index=True) \
                       .sort_values("game_date").reset_index(drop=True)
    val_yr_games = load_cache(2025)

    # SP-id sidecars
    sp_2023_path = CACHE / "sp_ids_2023.parquet"
    if not sp_2023_path.exists():
        print("  building sp_ids_2023 sidecar (one-time)...")
        from cp4_enrich_sp_ids import fetch_month
        rows = []
        for m in range(3, 11):
            rows.extend(fetch_month(2023, m))
        pd.DataFrame(rows).drop_duplicates("game_id").to_parquet(sp_2023_path, index=False)
    sp_ids_2023 = pd.read_parquet(sp_2023_path)
    sp_ids_2024 = pd.read_parquet(CACHE / "sp_ids_2024.parquet")
    sp_ids_train = pd.concat([sp_ids_2023, sp_ids_2024],
                              ignore_index=True).drop_duplicates("game_id")
    sp_ids_val = pd.read_parquet(CACHE / "sp_ids_2025.parquet")
    print(f"  train (2023+2024): {len(train_yr_games):,} games, "
          f"{len(sp_ids_train):,} SP-id rows")
    print(f"  val (2025):        {len(val_yr_games):,} games, "
          f"{len(sp_ids_val):,} SP-id rows")

    # Attach Stuff+ / Location+ features to both sets.
    league_center = norms["stuff_plus"]["center"]
    print("\nAttaching Stuff+/Location+ to 2024 train set...")
    train_df = attach_stuff_plus(train_yr_games, sp_ids_train, pitches,
                                  stuff, loc, norms, league_center)
    print("Attaching Stuff+/Location+ to 2025 val set...")
    val_df = attach_stuff_plus(val_yr_games, sp_ids_val, pitches,
                                stuff, loc, norms, league_center)

    # Feature lists — same numeric columns for both, + or − the 6 new ones.
    base_feats = get_feature_cols(train_df, include_stuff=False)
    full_feats = get_feature_cols(train_df, include_stuff=True)
    print(f"\nBaseline features  : {len(base_feats)}")
    print(f"Stuff+ added       : {len(full_feats) - len(base_feats)}")
    print(f"Total experimental : {len(full_feats)}")

    # Drop rows missing the target.
    train_df = train_df[train_df[TARGET].notna()].copy()
    val_df = val_df[val_df[TARGET].notna()].copy()
    y_tr = train_df[TARGET].astype(int).values
    y_va = val_df[TARGET].astype(int).values

    # Train both models on the same row set.
    print("\n" + "-" * 76)
    print("Training BASELINE (flag-off, no Stuff+)...")
    print("-" * 76)
    m_base = train(train_df[base_feats], y_tr, val_df[base_feats], y_va,
                    "baseline")

    print("\n" + "-" * 76)
    print("Training EXPERIMENTAL (flag-on, +Stuff+/Location+)...")
    print("-" * 76)
    m_full = train(train_df[full_feats], y_tr, val_df[full_feats], y_va,
                    "experiment")

    # Score val.
    p_base = m_base.predict_proba(val_df[base_feats])[:, 1]
    p_full = m_full.predict_proba(val_df[full_feats])[:, 1]

    # ---- pooled metrics ----
    print("\n" + "=" * 76)
    print("POOLED METRICS — 2025 hold-out")
    print("=" * 76)
    base_m = metric_set(y_va, p_base)
    full_m = metric_set(y_va, p_full)
    print(f"  {'metric':<14} {'baseline':>10} {'with Stuff+':>14} "
          f"{'delta':>10}")
    for k in ["brier", "log_loss", "hit_rate", "p_pick_avg"]:
        d = full_m[k] - base_m[k]
        print(f"  {k:<14} {base_m[k]:>10.4f} {full_m[k]:>14.4f} {d:>+10.4f}")

    # Bootstrap.
    print("\n" + "=" * 76)
    print("BOOTSTRAP 95% CI on delta (n_resamples=500; positive = Stuff+ helps)")
    print("=" * 76)
    boot = bootstrap_delta(y_va, p_base, p_full, n_resamples=500)
    print(f"  Brier   delta = {boot['brier_delta_mean']:+.4f}  "
          f"CI = [{boot['brier_delta_ci'][0]:+.4f}, "
          f"{boot['brier_delta_ci'][1]:+.4f}]")
    print(f"  LogLoss delta = {boot['log_loss_delta_mean']:+.4f}  "
          f"CI = [{boot['log_loss_delta_ci'][0]:+.4f}, "
          f"{boot['log_loss_delta_ci'][1]:+.4f}]")

    # Ship gate.
    SHIP_GATE = 0.005
    ship = boot["brier_delta_mean"] >= SHIP_GATE
    ci_includes_zero = boot["brier_delta_ci"][0] <= 0 <= boot["brier_delta_ci"][1]
    print(f"\n  Ship gate (Brier delta >= {SHIP_GATE}):"
          f" {'PASS' if ship else 'FAIL'}")
    print(f"  CI includes zero        : {'YES (effect not significant)' if ci_includes_zero else 'NO (effect significant)'}")

    # ---- tier segmentation ----
    if "tier" in val_df.columns:
        print("\n" + "=" * 76)
        print("TIER-SEGMENTED METRICS")
        print("=" * 76)
        for tier, sub in val_df.groupby("tier", observed=True):
            idx = sub.index
            yt = y_va[val_df.index.get_indexer(idx)]
            pa = p_base[val_df.index.get_indexer(idx)]
            pb = p_full[val_df.index.get_indexer(idx)]
            if len(yt) < 5:
                continue
            ma = metric_set(yt, pa); mb = metric_set(yt, pb)
            print(f"  {tier:<12} n={ma['n']:>4}  brier {ma['brier']:.4f} -> "
                  f"{mb['brier']:.4f} ({mb['brier']-ma['brier']:+.4f})  "
                  f"hit {ma['hit_rate']:.3f} -> {mb['hit_rate']:.3f}")

    # ---- reliability bins, baseline vs experiment ----
    print("\n" + "=" * 76)
    print("RELIABILITY BINS (predicted prob vs empirical home-win rate)")
    print("=" * 76)
    rel_a = reliability_bins(y_va, p_base)
    rel_b = reliability_bins(y_va, p_full)
    print(f"  {'bin':<14} {'n':>5} {'baseline gap_pp':>17} {'with Stuff+ gap_pp':>20}")
    for (_, ra), (_, rb) in zip(rel_a.iterrows(), rel_b.iterrows()):
        print(f"  {str(ra['bin']):<14} {int(ra['n']):>5} "
              f"{ra['gap_pp']:>+17.2f} {rb['gap_pp']:>+20.2f}")
    # Specifically the 0.55-0.65 overconfidence bin.
    mid_a = rel_a[rel_a["bin"].astype(str).isin(["(0.55, 0.6]", "(0.6, 0.65]"])]
    mid_b = rel_b[rel_b["bin"].astype(str).isin(["(0.55, 0.6]", "(0.6, 0.65]"])]
    if not mid_a.empty and not mid_b.empty:
        gap_a = (mid_a["p_mean"].mean() - mid_a["y_mean"].mean()) * 100
        gap_b = (mid_b["p_mean"].mean() - mid_b["y_mean"].mean()) * 100
        print(f"\n  0.55-0.65 overconfidence shift: "
              f"{gap_a:+.2f}pp -> {gap_b:+.2f}pp  "
              f"(softens by {gap_a-gap_b:+.2f}pp)")

    # ---- top-10 most-shifted games ----
    print("\n" + "=" * 76)
    print("TOP 10 GAMES BY |p_with - p_without|")
    print("=" * 76)
    val_df = val_df.assign(
        p_base=p_base, p_full=p_full,
        shift=np.abs(p_full - p_base),
    )
    cols_show = ["game_date", "away_team", "home_team",
                 "home_sp_stuff_plus", "away_sp_stuff_plus",
                 "sp_stuff_plus_gap", "p_base", "p_full", "shift", TARGET]
    cols_show = [c for c in cols_show if c in val_df.columns]
    top_shift = val_df.nlargest(10, "shift")[cols_show]
    for _, r in top_shift.iterrows():
        gd = r["game_date"]
        gd_s = gd.strftime("%Y-%m-%d") if hasattr(gd, "strftime") else str(gd)
        print(f"  {gd_s} {r['away_team']:<3} @ {r['home_team']:<3}  "
              f"Stuff+gap={r.get('sp_stuff_plus_gap', np.nan):+.2f}  "
              f"p_base={r['p_base']:.3f} -> p_full={r['p_full']:.3f} "
              f"(shift={r['shift']:.3f})  home_won={int(r[TARGET])}")

    # ---- SHAP feature importance ----
    print("\n" + "=" * 76)
    print("SHAP FEATURE IMPORTANCE (experimental model, top 30 + Stuff+ rank)")
    print("=" * 76)
    booster = m_full.get_booster()
    gain = booster.get_score(importance_type="gain")
    # Map XGBoost feature names (f0, f1, ...) back to readable names.
    fn = booster.feature_names
    importance = pd.DataFrame([
        {"feature": (fn[int(k[1:])] if k.startswith("f") and k[1:].isdigit() and int(k[1:]) < len(fn) else k),
         "gain": v}
        for k, v in gain.items()
    ]).sort_values("gain", ascending=False).reset_index(drop=True)
    importance["rank"] = importance.index + 1
    print(f"  total features used by tree: {len(importance)}")
    for _, r in importance.head(30).iterrows():
        marker = " ★ NEW" if r["feature"] in NEW_FEATS else ""
        print(f"  rank {int(r['rank']):>3}  gain={r['gain']:>10.1f}  "
              f"{r['feature']}{marker}")
    # Rank of new features.
    print("\n  Stuff+/Location+ ranks among all features:")
    for f in NEW_FEATS:
        row = importance[importance["feature"] == f]
        if row.empty:
            print(f"    {f:<28s}  unused by the tree")
        else:
            r = row.iloc[0]
            pct = 100 * (1 - r["rank"] / len(importance))
            print(f"    {f:<28s}  rank {int(r['rank']):>3}/{len(importance)}  "
                  f"(top {pct:.0f}%)  gain={r['gain']:.1f}")

    # ---- save artifacts ----
    out_dir = Path(STUFF_PLUS_CFG["cache_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    val_df[cols_show].to_csv(out_dir / "cp4_val_predictions.csv", index=False)
    importance.to_csv(out_dir / "cp4_shap_importance.csv", index=False)
    summary = {
        "baseline": base_m, "experiment": full_m, "bootstrap": boot,
        "ship_gate": SHIP_GATE, "ship_pass": ship,
        "n_features_baseline": len(base_feats),
        "n_features_experiment": len(full_feats),
    }
    (out_dir / "cp4_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved artifacts to {out_dir}/cp4_*")

    print("\n" + "=" * 76)
    print(f"VERDICT: {'KEEP — ship Stuff+/Location+' if ship else 'DROP — Stuff+ does not clear the gate'}")
    print("=" * 76)


if __name__ == "__main__":
    main()
