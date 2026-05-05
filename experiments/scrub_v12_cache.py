"""Leakage scrub for v12 feature caches (2026-05-02).

Splits each `features_<year>_full_1_v12.parquet` into two files:
  - features_<year>_full_1_v12.parquet  (features only — `home_win` and
    `home_f5_win` removed from this file)
  - targets_<year>_v12.parquet          (sidecar with [game_id, home_win,
    home_f5_win] for the trainers to merge at training time)

Backs up the originals as `features_<year>_full_1_v12.original.parquet`
so a future contributor can revert with `mv .original.parquet
features_<year>_full_1_v12.parquet`.

Why: `home_f5_win` (the actual first-5-innings outcome) was inline in
the features cache and correlated with `home_win` at +0.637. Production
stage-2 trains from an explicit feature list (`FULL_FEATURES_EXTRA` in
mlb_edge.model) so it didn't pick up the leak — but my CP4 first-pass
experiment treated the full cache as features and the model built itself
around the leaked column (gain 71.5 vs next at 5.5). Hygiene fix so future
naive trainers can't repeat the mistake.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

CACHE_DIR = Path("data/feature_cache")
TARGETS = ["home_win", "home_f5_win"]
ID_COL = "game_id"


def main():
    years = (2023, 2024, 2025)
    for yr in years:
        path = CACHE_DIR / f"features_{yr}_full_1_v12.parquet"
        if not path.exists():
            print(f"  {yr}: cache not found, skipping")
            continue
        df = pd.read_parquet(path)
        n0 = len(df)
        print(f"  {yr}: {n0:,} rows × {len(df.columns)} cols")

        # Verify targets are present.
        missing = [c for c in TARGETS if c not in df.columns]
        if missing:
            print(f"    SKIP: missing target column(s) {missing}")
            continue

        # Back up original.
        backup = path.with_suffix(".original.parquet")
        if not backup.exists():
            df.to_parquet(backup, index=False)
            print(f"    backed up to {backup.name}")
        else:
            print(f"    backup already exists at {backup.name} — not overwriting")

        # Write sidecar.
        sidecar_path = CACHE_DIR / f"targets_{yr}_v12.parquet"
        df[[ID_COL] + TARGETS].to_parquet(sidecar_path, index=False)
        print(f"    wrote sidecar {sidecar_path.name} "
              f"({sidecar_path.stat().st_size:,} bytes)")

        # Drop targets from the features parquet.
        df_clean = df.drop(columns=TARGETS)
        df_clean.to_parquet(path, index=False)
        print(f"    scrubbed features parquet: "
              f"{len(df_clean.columns)} cols (was {len(df.columns)})")

        # Sanity: confirm targets are gone, features are intact.
        check = pd.read_parquet(path)
        leak_back = [c for c in TARGETS if c in check.columns]
        assert not leak_back, f"scrub failed: targets still present {leak_back}"
        assert len(check) == n0, f"row-count drift {len(check)} vs {n0}"
        print(f"    verified: targets removed, {len(check):,} rows preserved")

    # Drop a marker file next to the caches.
    marker = CACHE_DIR / "LEAKAGE_SCRUBBED.md"
    marker.write_text(_marker_text())
    print(f"\nWrote marker: {marker}")


def _marker_text() -> str:
    return (
        "# v12 feature cache — leakage scrub log\n\n"
        "_Date: 2026-05-02_\n\n"
        "## What was scrubbed\n\n"
        "Two target columns were moved out of the features parquet to a sidecar:\n\n"
        "  - `home_win`        — full-game target. Was inline in `features_<year>_full_1_v12.parquet`.\n"
        "  - `home_f5_win`     — first-5-innings target (binary outcome). Was the same.\n\n"
        "## Why\n\n"
        "`home_f5_win` correlates with `home_win` at +0.637 across the pooled "
        "2023-2025 cache. A naive trainer (\"use every numeric column as a "
        "feature\") built an XGBoost model whose top feature by gain was "
        "`home_f5_win` at 71.5 — next feature was 5.5 — meaning the tree was "
        "essentially predicting `home_win` from the actual first-5-innings "
        "outcome. Pure target leakage.\n\n"
        "## What was unchanged\n\n"
        "Production stage-2 (`models/latest.pkl`) trains from an explicit "
        "feature list (`FULL_FEATURES_EXTRA` in `mlb_edge.model`). It NEVER "
        "included `home_f5_win` as a feature. The shipped model is unaffected.\n\n"
        "The scrub is hygiene — it ensures future experiments can't repeat "
        "the leak by accident.\n\n"
        "## Files now on disk per year (e.g., 2025)\n\n"
        "  - `features_2025_full_1_v12.parquet`     features only, no targets\n"
        "  - `targets_2025_v12.parquet`             [game_id, home_win, home_f5_win]\n"
        "  - `features_2025_full_1_v12.original.parquet`  pre-scrub backup (intact)\n\n"
        "## Trainer changes required\n\n"
        "Both `train_stage1_f5` and `train_stage2_full` (in `mlb_edge.model`) "
        "expect the target columns inline. They have been updated to merge "
        "the sidecar on `game_id` before calling `_xy_split(...)`. The cache "
        "loader helper `mlb_edge.build_pipeline.build_historical_frame` was "
        "also updated to do the merge transparently.\n\n"
        "## To revert (emergency only)\n\n"
        "    mv features_<year>_full_1_v12.original.parquet features_<year>_full_1_v12.parquet\n"
        "    rm targets_<year>_v12.parquet\n\n"
        "And revert the trainer changes in `mlb_edge/model.py` and "
        "`mlb_edge/build_pipeline.py`.\n"
    )


if __name__ == "__main__":
    sys.exit(main())
