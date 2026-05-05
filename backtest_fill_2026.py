"""
backtest_fill_2026.py
---------------------
2026-only out-of-sample test of whether the MLB-Stats-API fill improves
the model's predictions. Older seasons are excluded — the fill only matters
in a regime where YTD Statcast is thin, which is a current-season-only
problem.

Method
------
For every completed 2026 game through yesterday:
  1. Load the normal feature frame (point-in-time Statcast, NaN where thin).
  2. Apply `fill_one_game` to patch NaN sides from MLB Stats API season-
     prior totals (2025 → 2024 → league average).
  3. Score BOTH frames through the production model (`models/latest.pkl`).
  4. Compare raw vs filled on the same games against the actual outcome.

Leakage guard
-------------
The fallback uses 2025 / 2024 / 2023 season totals — all strictly prior to
any 2026 game — so the fill doesn't peek at the game's outcome or at games
played later in 2026. The model itself (`models/latest.pkl`) is trained on
2024-2025 data, so 2026 is genuinely out-of-sample for it.

Metrics
-------
  - Brier score (lower is better)
  - Log loss   (lower is better)
  - Accuracy   (higher is better)
  - Breakdown on the SUBSET of games that actually got patched, since
    that's where the fill can make a difference — games with complete
    Statcast data score identically through both paths.
  - Per-feature NaN coverage before/after

Usage
-----
    python backtest_fill_2026.py                    # default: thru yesterday
    python backtest_fill_2026.py --through 2026-04-15
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from mlb_edge import build_pipeline as bp
from mlb_edge import data_ingestion as di
from mlb_edge import fallback_stats as fb
from mlb_edge import model as md
from mlb_edge import point_in_time as pit
from fill_slate import fill_one_game


# Columns the fill targets. Matches fill_slate.SP_GAP_SPECS etc.
FILLABLE_COLS = [
    "sp_xera_gap", "sp_xwoba_allowed_gap", "sp_fip_gap", "sp_siera_gap",
    "sp_k_bb_pct_gap", "sp_recent_form_gap", "sp_hardhit_gap",
    "sp_stamina_gap",
    "team_wrcplus_gap", "team_woba_gap", "team_hardhit_gap", "team_bbk_gap",
    "bullpen_siera_gap",
    "home_sp_luck", "away_sp_luck",
]

PITCH_LEVEL_ONLY = ["sp_velo_drop_gap", "sp_vs_lineup_gap", "sp_rest_gap"]


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    p_c = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "brier":    float(brier_score_loss(y, p)),
        "logloss":  float(log_loss(y, p_c)),
        "accuracy": float(accuracy_score(y, (p >= 0.5).astype(int))),
        "sharpness": float(np.abs(p - 0.5).mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", default=None,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    help="Last game-date to include. Default: yesterday.")
    ap.add_argument("--model_path", default="models/latest.pkl")
    ap.add_argument("--audit", default="backtest_fill_2026_audit.csv")
    ap.add_argument("--predictions", default="backtest_fill_2026_preds.csv")
    args = ap.parse_args()

    through = args.through or (date.today() - timedelta(days=1))
    print(f"Backtesting fill on 2026 games through {through}")
    print(f"Model: {args.model_path}")
    print()

    # -----------------------------------------------------------------
    # 1. Load the normal historical frame for 2026 (point-in-time).
    # -----------------------------------------------------------------
    print("Loading 2026 historical feature frame...")
    raw_frame = bp.build_historical_frame(2026, through=through)
    if raw_frame.empty:
        print("No 2026 games available. Abort.")
        return
    raw_frame = raw_frame.dropna(subset=["home_win"]).copy()
    raw_frame["game_date"] = pd.to_datetime(raw_frame["game_date"])
    raw_frame = raw_frame[raw_frame["game_date"].dt.date <= through].reset_index(drop=True)
    print(f"  {len(raw_frame)} completed games in window")
    print()

    # -----------------------------------------------------------------
    # 2. Reload the same Statcast frame the pipeline used, for per-game
    #    point-in-time lookups (pitcher_as_of / team_batting_as_of).
    # -----------------------------------------------------------------
    print("Loading 2026 YTD Statcast for point-in-time fill lookups...")
    sc = di.fetch_ytd_statcast(through)
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    # Restrict to 2026 rows only — build_historical_frame already does this
    # but fetch_ytd_statcast may bleed a few late-2025 rows depending on
    # the source cutoff.
    sc = sc[sc["game_date"].dt.year == 2026].copy()
    print(f"  Statcast rows: {len(sc)}")
    starters_by_team = pit.infer_starters_by_team(sc)
    print()

    # -----------------------------------------------------------------
    # 3. Per-game fill loop.
    # -----------------------------------------------------------------
    print("Applying fill layer per game (cached MLB API calls)...")
    filled_frame = raw_frame.copy()
    audit_rows = []
    for idx, row in raw_frame.iterrows():
        gpk = int(row["game_id"])
        game_date = pd.Timestamp(row["game_date"])

        # Look up SP IDs from the Statcast frame. `get_game_starters` returns
        # the first pitcher each team used in inning 1 of that game —
        # equivalent to what build_slate_frame would hit for a live slate.
        starters = pit.get_game_starters(sc, gpk)
        home_sp_id = starters.get("home_sp")
        away_sp_id = starters.get("away_sp")

        patches, audit = fill_one_game(
            row=row,
            sc=sc,
            starters_by_team=starters_by_team,
            home_sp_id=home_sp_id,
            away_sp_id=away_sp_id,
            game_date=game_date,
        )
        for col, val in patches.items():
            filled_frame.at[idx, col] = val
        audit["game_date"] = game_date.date()
        audit["home_win"] = int(row["home_win"])
        audit_rows.append(audit)

        if (idx + 1) % 50 == 0:
            print(f"  {idx + 1}/{len(raw_frame)} games processed")
    print(f"  Done. {len(raw_frame)} games processed")
    print()

    audit_df = pd.DataFrame(audit_rows)
    patched_mask = audit_df["n_filled"] > 0
    n_patched = int(patched_mask.sum())
    print(f"Games with at least one patched feature: "
          f"{n_patched} / {len(audit_df)} ({100*n_patched/len(audit_df):.1f}%)")
    print()

    audit_df.to_csv(args.audit, index=False)
    print(f"Wrote audit to {args.audit}")

    # -----------------------------------------------------------------
    # 4. Score both frames through the model.
    # -----------------------------------------------------------------
    print("Loading production model...")
    stage1, stage2 = md.load(args.model_path)

    print("Scoring raw frame...")
    raw_preds = md.predict(stage1, stage2, raw_frame)
    print("Scoring filled frame...")
    fill_preds = md.predict(stage1, stage2, filled_frame)

    y = raw_frame["home_win"].astype(int).to_numpy()
    raw_prob = raw_preds["model_prob"].to_numpy(dtype=float)
    fill_prob = fill_preds["model_prob"].to_numpy(dtype=float)
    # F5 for completeness — the anchor probability.
    raw_f5 = raw_preds["f5_prob"].to_numpy(dtype=float)
    fill_f5 = fill_preds["f5_prob"].to_numpy(dtype=float)
    # F5 label is nullable (ties = push). Keep only where we have a label.
    f5_y = raw_frame["home_f5_win"]
    f5_mask = f5_y.notna().to_numpy()

    # Persist per-game preds for further auditing.
    out_preds = raw_frame[["game_id", "game_date", "home_team", "away_team",
                           "home_win", "home_f5_win"]].copy()
    out_preds["raw_prob"] = raw_prob
    out_preds["fill_prob"] = fill_prob
    out_preds["raw_f5"] = raw_f5
    out_preds["fill_f5"] = fill_f5
    out_preds["patched"] = patched_mask.to_numpy()
    out_preds["delta_prob"] = fill_prob - raw_prob
    out_preds.to_csv(args.predictions, index=False)
    print(f"Wrote per-game predictions to {args.predictions}")
    print()

    # -----------------------------------------------------------------
    # 5. Metrics tables.
    # -----------------------------------------------------------------
    print("=" * 78)
    print("OVERALL METRICS -- full-game probability (Stage 2 + F5 override)")
    print("=" * 78)
    raw_m = _metrics(y, raw_prob)
    fill_m = _metrics(y, fill_prob)
    print(f"  {'metric':<10s} {'raw':>10s} {'filled':>10s} {'delta':>10s}  direction")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10}  ---------")
    for k in ("brier", "logloss", "accuracy", "sharpness"):
        d = fill_m[k] - raw_m[k]
        # brier/logloss: lower = better, so delta < 0 means filled wins
        # accuracy/sharpness: higher = better, delta > 0 means filled wins
        if k in ("brier", "logloss"):
            arrow = "filled better" if d < 0 else "raw better" if d > 0 else "tie"
        else:
            arrow = "filled better" if d > 0 else "raw better" if d < 0 else "tie"
        print(f"  {k:<10s} {raw_m[k]:>10.5f} {fill_m[k]:>10.5f} "
              f"{d:+10.5f}  {arrow}")
    print()

    print("=" * 78)
    print("STAGE 1 (F5) METRICS")
    print("=" * 78)
    if f5_mask.sum() > 0:
        y_f5 = f5_y[f5_mask].astype(int).to_numpy()
        raw_f5_m = _metrics(y_f5, raw_f5[f5_mask])
        fill_f5_m = _metrics(y_f5, fill_f5[f5_mask])
        print(f"  {'metric':<10s} {'raw':>10s} {'filled':>10s} {'delta':>10s}  direction")
        print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10}  ---------")
        for k in ("brier", "logloss", "accuracy", "sharpness"):
            d = fill_f5_m[k] - raw_f5_m[k]
            if k in ("brier", "logloss"):
                arrow = "filled better" if d < 0 else "raw better" if d > 0 else "tie"
            else:
                arrow = "filled better" if d > 0 else "raw better" if d < 0 else "tie"
            print(f"  {k:<10s} {raw_f5_m[k]:>10.5f} {fill_f5_m[k]:>10.5f} "
                  f"{d:+10.5f}  {arrow}")
    else:
        print("  No labeled F5 outcomes in window.")
    print()

    # Subset where the fill actually changed something — everywhere else
    # raw and filled produce identical predictions so including them dilutes
    # the signal.
    pm = patched_mask.to_numpy()
    if pm.sum() > 0:
        print("=" * 78)
        print(f"PATCHED-SUBSET METRICS  (n = {int(pm.sum())} games)")
        print("=" * 78)
        raw_sub = _metrics(y[pm], raw_prob[pm])
        fill_sub = _metrics(y[pm], fill_prob[pm])
        print(f"  {'metric':<10s} {'raw':>10s} {'filled':>10s} {'delta':>10s}  direction")
        print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10}  ---------")
        for k in ("brier", "logloss", "accuracy", "sharpness"):
            d = fill_sub[k] - raw_sub[k]
            if k in ("brier", "logloss"):
                arrow = "filled better" if d < 0 else "raw better" if d > 0 else "tie"
            else:
                arrow = "filled better" if d > 0 else "raw better" if d < 0 else "tie"
            print(f"  {k:<10s} {raw_sub[k]:>10.5f} {fill_sub[k]:>10.5f} "
                  f"{d:+10.5f}  {arrow}")
        print()

        # Pick-flip summary: on which patched games did the two disagree?
        raw_pick = (raw_prob[pm] >= 0.5).astype(int)
        fill_pick = (fill_prob[pm] >= 0.5).astype(int)
        flipped = raw_pick != fill_pick
        n_flipped = int(flipped.sum())
        if n_flipped > 0:
            y_sub = y[pm]
            raw_correct = int((raw_pick[flipped] == y_sub[flipped]).sum())
            fill_correct = int((fill_pick[flipped] == y_sub[flipped]).sum())
            print(f"  Picks that FLIPPED between raw and filled: {n_flipped}")
            print(f"    raw pick was correct:    {raw_correct} / {n_flipped}")
            print(f"    filled pick was correct: {fill_correct} / {n_flipped}")
            if fill_correct > raw_correct:
                print(f"    -> Fill flipped in the right direction "
                      f"on {fill_correct - raw_correct} more games.")
            elif fill_correct < raw_correct:
                print(f"    -> Fill flipped in the wrong direction "
                      f"on {raw_correct - fill_correct} more games.")
            else:
                print(f"    -> Flips were a wash on accuracy.")
        else:
            print("  No picks flipped — fill shifted probabilities "
                  "but not which side the model picked.")
    print()

    # -----------------------------------------------------------------
    # 6. NaN coverage (pure diagnostic, not a metric).
    # -----------------------------------------------------------------
    print("=" * 78)
    print("NaN COVERAGE (before vs after fill)")
    print("=" * 78)
    print(f"  {'feature':<24s} {'before':>8s}  {'after':>8s}  {'filled':>8s}")
    print(f"  {'-'*24} {'-'*8}  {'-'*8}  {'-'*8}")
    for c in FILLABLE_COLS:
        b = int(raw_frame[c].isna().sum()) if c in raw_frame.columns else 0
        a = int(filled_frame[c].isna().sum()) if c in filled_frame.columns else 0
        print(f"  {c:<24s} {b:>8d}  {a:>8d}  {b - a:>8d}")
    print()
    print("UN-FILLABLE (pitch-level Statcast only, no API substitute):")
    for c in PITCH_LEVEL_ONLY:
        if c in raw_frame.columns:
            n = int(raw_frame[c].isna().sum())
            print(f"  {c:<24s} {n:>8d} NaN still")
    print()

    # -----------------------------------------------------------------
    # 7. Final verdict.
    # -----------------------------------------------------------------
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    # Decision: fill wins iff overall Brier improves (lower) AND accuracy
    # doesn't get worse. A slight acc regression at roughly equal Brier is
    # fine since accuracy is a noisier metric.
    brier_delta = fill_m["brier"] - raw_m["brier"]
    acc_delta = fill_m["accuracy"] - raw_m["accuracy"]
    if brier_delta < -0.0005 and acc_delta >= -0.005:
        print("  KEEP FILL: Brier improved materially and accuracy didn't regress.")
    elif brier_delta < -0.0005 and acc_delta < -0.005:
        print("  MIXED: Brier improved but accuracy dropped meaningfully.")
        print("  Investigate patched-subset flip rate before committing.")
    elif abs(brier_delta) <= 0.0005:
        print("  WASH: Brier shift within noise. Fill is cost-neutral — "
              "safe to ship since it only matters on thin-data games.")
    else:
        print("  DROP FILL: Brier regressed. Raw NaN-tolerant XGBoost defaults "
              "beat the API fallback on this sample.")
    print()


if __name__ == "__main__":
    main()
