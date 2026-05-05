"""Phase 1, CP2 — train Stuff+ and Location+, persist + sniff-test.

Loads the dataset assembled at CP1, splits by year, trains two XGBoost
regressors on `delta_run_exp`, persists both + the league mean/sd JSON,
then runs sniff-test rankings.

Run:
    PYTHONIOENCODING=utf-8 python experiments/cp2_train_pitch_quality.py
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
import requests
import xgboost as xgb
from sklearn.metrics import r2_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.config import STUFF_PLUS_CFG
from mlb_edge.pitch_quality import (
    LOCATION_FEATURES_CATEGORICAL,
    LOCATION_FEATURES_NUMERIC,
    STUFF_FEATURES_CATEGORICAL,
    STUFF_FEATURES_NUMERIC,
    TARGET_COL,
    score_pitches,
    to_xgb_frame,
)


# --------------------------------------------------------------------------
# Training spec — kept minimal per the brief: "the question is does this
# signal exist, not is this the optimal model."
# --------------------------------------------------------------------------
HP = dict(
    n_estimators=1500,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    objective="reg:squarederror",
    tree_method="hist",
    enable_categorical=True,
    early_stopping_rounds=50,
    n_jobs=-1,
    random_state=20260502,
)


def _resolve_pitcher_names(pids):
    if not list(pids):
        return {}
    try:
        ids = ",".join(str(int(p)) for p in pids)
        r = requests.get("https://statsapi.mlb.com/api/v1/people",
                         params={"personIds": ids}, timeout=20)
        r.raise_for_status()
        return {p["id"]: p.get("fullName", "?") for p in r.json().get("people", [])}
    except Exception:
        return {}


def train_one(name: str, df_train: pd.DataFrame, df_val: pd.DataFrame,
              features: list[str]) -> tuple[xgb.XGBRegressor, dict]:
    log = logging.getLogger(__name__)
    log.info("[%s] train n=%s val n=%s feats=%s", name,
             f"{len(df_train):,}", f"{len(df_val):,}", len(features))

    X_tr = to_xgb_frame(df_train, features)
    X_va = to_xgb_frame(df_val, features)
    # Align categories — val set must use the same category levels as train,
    # otherwise unseen levels become NaN and rows silently degrade.
    cats: dict[str, list] = {}
    for c in X_tr.columns:
        if X_tr[c].dtype.name == "category":
            cats[c] = list(X_tr[c].cat.categories)
            X_va[c] = pd.Categorical(X_va[c], categories=cats[c])

    y_tr = df_train[TARGET_COL].astype("float32")
    y_va = df_val[TARGET_COL].astype("float32")

    t0 = time.time()
    model = xgb.XGBRegressor(**HP)
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    fit_secs = time.time() - t0

    # Held-out R^2 on the val set.
    pred_va = model.predict(X_va)
    r2_val = float(r2_score(y_va, pred_va))
    pred_tr = model.predict(X_tr)
    r2_tr = float(r2_score(y_tr, pred_tr))

    # League norm (mean, sd) computed on the TRAINING SET predictions.
    # This is the canonical fixed reference point for inference — the
    # 100/10 rescale must not drift if 2026 has a different mean.
    pred_mean = float(pred_tr.mean())
    pred_sd = float(pred_tr.std(ddof=0))

    log.info("[%s] fit_secs=%.1f best_iter=%s R^2 train=%.4f val=%.4f "
             "pred mean=%.5f sd=%.5f",
             name, fit_secs,
             getattr(model, "best_iteration", "?"), r2_tr, r2_val,
             pred_mean, pred_sd)

    return model, {
        "name": name,
        "n_train": len(df_train), "n_val": len(df_val),
        "features": features,
        "r2_train": r2_tr, "r2_val": r2_val,
        "best_iteration": int(getattr(model, "best_iteration", -1) or -1),
        "fit_seconds": fit_secs,
        "mean": pred_mean, "sd": pred_sd,
        "categories": cats,
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print("=" * 72)
    print("Phase 1 / CP2 — train Stuff+ and Location+")
    print("=" * 72)

    cache = Path(STUFF_PLUS_CFG["cache_dir"])
    df = pd.read_parquet(cache / "dataset.parquet")
    print(f"\nLoaded {len(df):,} pitches from {cache / 'dataset.parquet'}")

    train_yrs = STUFF_PLUS_CFG["train_years"]
    val_yr = STUFF_PLUS_CFG["validate_year"]
    test_yr = STUFF_PLUS_CFG["test_year"]
    df_tr = df[df["game_year"].isin(train_yrs)]
    df_va = df[df["game_year"] == val_yr]
    df_te = df[df["game_year"] == test_yr]
    print(f"  train={len(df_tr):,}  val={len(df_va):,}  test={len(df_te):,}")

    feats_stuff = STUFF_FEATURES_NUMERIC + STUFF_FEATURES_CATEGORICAL
    feats_loc = LOCATION_FEATURES_NUMERIC + LOCATION_FEATURES_CATEGORICAL

    print("\n" + "-" * 72)
    print("Training Stuff+...")
    print("-" * 72)
    stuff_model, stuff_meta = train_one("Stuff+", df_tr, df_va, feats_stuff)

    print("\n" + "-" * 72)
    print("Training Location+...")
    print("-" * 72)
    loc_model, loc_meta = train_one("Location+", df_tr, df_va, feats_loc)

    # -----------------------------------------------------------------
    # Persist artifacts
    # -----------------------------------------------------------------
    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)
    stuff_path = models_dir / "stuff_plus_v1.pkl"
    loc_path = models_dir / "location_plus_v1.pkl"
    norms_path = models_dir / "pitch_quality_norms_v1.json"

    joblib.dump(stuff_model, stuff_path)
    joblib.dump(loc_model, loc_path)
    rescale = STUFF_PLUS_CFG["rescale"]
    norms = {
        "version": "v1",
        "trained_at": pd.Timestamp.utcnow().isoformat(),
        "train_years": list(train_yrs),
        "validate_year": val_yr,
        "categories": stuff_meta["categories"],   # same shared cats both models
        "stuff_plus": {
            "mean": stuff_meta["mean"], "sd": stuff_meta["sd"],
            "center": rescale["center"], "scale": rescale["scale"],
            "r2_train": stuff_meta["r2_train"], "r2_val": stuff_meta["r2_val"],
            "best_iteration": stuff_meta["best_iteration"],
            "features": stuff_meta["features"],
        },
        "location_plus": {
            "mean": loc_meta["mean"], "sd": loc_meta["sd"],
            "center": rescale["center"], "scale": rescale["scale"],
            "r2_train": loc_meta["r2_train"], "r2_val": loc_meta["r2_val"],
            "best_iteration": loc_meta["best_iteration"],
            "features": loc_meta["features"],
        },
    }
    norms_path.write_text(json.dumps(norms, indent=2))
    print(f"\nWrote:")
    print(f"  {stuff_path}  ({stuff_path.stat().st_size/1e6:.1f} MB)")
    print(f"  {loc_path}    ({loc_path.stat().st_size/1e6:.1f} MB)")
    print(f"  {norms_path}  ({norms_path.stat().st_size:,} bytes)")

    # -----------------------------------------------------------------
    # Sniff tests on val + test
    # -----------------------------------------------------------------
    print("\n" + "=" * 72)
    print("HELD-OUT METRICS")
    print("=" * 72)
    print(f"  Stuff+    train R^2 = {stuff_meta['r2_train']:.4f}   "
          f"val R^2 = {stuff_meta['r2_val']:.4f}   "
          f"best_iter = {stuff_meta['best_iteration']}")
    print(f"  Location+ train R^2 = {loc_meta['r2_train']:.4f}   "
          f"val R^2 = {loc_meta['r2_val']:.4f}   "
          f"best_iter = {loc_meta['best_iteration']}")
    print("  reference: per-pitch run-value is mostly noise; ~0.04–0.06 R^2 "
          "is the Driveline range, anything ≥0.02 is signal.")

    # Score val + test pitches.
    print("\nScoring val (2025) and test (2026 YTD) sets...")
    df_va_s = score_pitches(df_va, stuff_model, loc_model, norms)
    df_te_s = score_pitches(df_te, stuff_model, loc_model, norms) if len(df_te) else pd.DataFrame()
    df_inf = pd.concat([df_va_s, df_te_s], ignore_index=True) if len(df_te_s) else df_va_s
    print(f"  scored {len(df_inf):,} pitches across val+test")

    # Distribution percentiles.
    print("\n" + "=" * 72)
    print("PITCH-LEVEL DISTRIBUTION (val + test)")
    print("=" * 72)
    pcts = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print("  Stuff+ percentiles    : " + "  ".join(
        f"p{p}={np.percentile(df_inf['stuff_plus'], p):.1f}" for p in pcts))
    print("  Location+ percentiles : " + "  ".join(
        f"p{p}={np.percentile(df_inf['location_plus'], p):.1f}" for p in pcts))
    print(f"  Stuff+    mean={df_inf['stuff_plus'].mean():.2f}  "
          f"sd={df_inf['stuff_plus'].std():.2f}  "
          f"skew={df_inf['stuff_plus'].skew():.2f}")
    print(f"  Location+ mean={df_inf['location_plus'].mean():.2f}  "
          f"sd={df_inf['location_plus'].std():.2f}  "
          f"skew={df_inf['location_plus'].skew():.2f}")

    # Per-pitcher aggregation: 2025 only for the sniff test (full-season
    # large sample) + filter to ≥500 pitches in val so we don't compare a
    # spot starter to deGrom.
    pp = (df_va_s.groupby("pitcher")
          .agg(n=("stuff_plus", "size"),
               stuff_plus=("stuff_plus", "mean"),
               location_plus=("location_plus", "mean"))
          .reset_index())
    pp = pp[pp["n"] >= 500].copy()
    pp = pp.sort_values("stuff_plus", ascending=False)
    print(f"\n  {len(pp)} pitchers with >=500 pitches in 2025 val set.")

    # Resolve names for the top-20 + bottom-20 + a few requested checks.
    target_ids = (list(pp.head(20)["pitcher"].astype(int)) +
                  list(pp.tail(20)["pitcher"].astype(int)))
    # Also resolve names for the headline names the brief asked about.
    requested_names = ["Skubal", "Sale", "Wheeler", "Glasnow", "Skenes", "deGrom"]
    name_map = _resolve_pitcher_names(target_ids)
    pp["name"] = pp["pitcher"].astype(int).map(name_map).fillna("?")
    # Fill names for ALL via a second batch (top-50 + bottom-50 + requested rows).
    extra = list(pp.head(50)["pitcher"].astype(int)) + list(pp.tail(50)["pitcher"].astype(int))
    name_map2 = _resolve_pitcher_names(list(set(extra)))
    name_map.update(name_map2)
    pp["name"] = pp["pitcher"].astype(int).map(name_map).fillna("?")

    print("\n" + "=" * 72)
    print("TOP 20 SPs BY 2025 STUFF+ (≥500 val pitches)")
    print("=" * 72)
    for _, r in pp.head(20).iterrows():
        print(f"  {r['name']:<28s} (id {int(r['pitcher'])})  "
              f"n={int(r['n']):>5}  Stuff+={r['stuff_plus']:6.2f}  "
              f"Location+={r['location_plus']:6.2f}")

    print("\n" + "=" * 72)
    print("BOTTOM 20 SPs BY 2025 STUFF+ (≥500 val pitches)")
    print("=" * 72)
    for _, r in pp.tail(20).iloc[::-1].iterrows():
        print(f"  {r['name']:<28s} (id {int(r['pitcher'])})  "
              f"n={int(r['n']):>5}  Stuff+={r['stuff_plus']:6.2f}  "
              f"Location+={r['location_plus']:6.2f}")

    # Where do the headline names land?
    print("\n" + "=" * 72)
    print("REQUESTED HEADLINE PITCHERS — landing position")
    print("=" * 72)
    pp_sorted = pp.reset_index(drop=True)
    pp_sorted["rank"] = pp_sorted.index + 1
    for query in requested_names:
        hits = pp_sorted[pp_sorted["name"].str.contains(query, case=False, na=False)]
        if hits.empty:
            print(f"  {query}: not found in 2025 val (≥500 pitches threshold)")
        else:
            for _, r in hits.iterrows():
                pct = 100.0 * (1 - r["rank"] / len(pp_sorted))
                print(f"  {r['name']:<28s} rank {int(r['rank'])}/"
                      f"{len(pp_sorted)}  (top {pct:.1f}%)  "
                      f"Stuff+={r['stuff_plus']:.2f}  "
                      f"Location+={r['location_plus']:.2f}")

    # Save the per-pitcher 2025 aggregate for downstream review.
    pp.to_csv(Path(STUFF_PLUS_CFG["cache_dir"]) / "stuff_plus_2025_per_sp.csv",
              index=False)
    print(f"\nSaved per-SP 2025 aggregates to "
          f"{Path(STUFF_PLUS_CFG['cache_dir']) / 'stuff_plus_2025_per_sp.csv'}")

    print("\nCP2 complete. Holding for review.")


if __name__ == "__main__":
    main()
