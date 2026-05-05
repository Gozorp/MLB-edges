"""Phase 1, Checkpoint 1 — Pitch dataset assembly.

Loads multi-year Statcast chunks, applies the filter pipeline, persists the
filtered dataset to `data/pitch_quality/dataset.parquet`, and prints a sanity
report (row counts per year, top-10 SPs by pitch volume, pitch-type
distribution, leakage assertion result).

NOT training. CP2 will train on this output.

Run:
    python experiments/cp1_pitch_dataset.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import requests

# Ensure mlb_edge is importable when run from a clean cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.pitch_quality import (
    assert_no_leakage,
    build_dataset,
    BANNED_OUTCOME_LEAKAGE,
    LOCATION_FEATURES_CATEGORICAL,
    LOCATION_FEATURES_NUMERIC,
    STUFF_FEATURES_CATEGORICAL,
    STUFF_FEATURES_NUMERIC,
    TARGET_COL,
)
from mlb_edge.config import STUFF_PLUS_CFG


def _resolve_pitcher_names(pids: list[int]) -> dict[int, str]:
    """Best-effort name lookup via MLB Stats API. Used only for the human-
    readable sanity report; not part of training."""
    if not pids:
        return {}
    try:
        ids = ",".join(str(p) for p in pids)
        r = requests.get("https://statsapi.mlb.com/api/v1/people",
                         params={"personIds": ids}, timeout=20)
        r.raise_for_status()
        return {p["id"]: p.get("fullName", "?") for p in r.json().get("people", [])}
    except Exception:
        return {}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print("=" * 72)
    print("Phase 1 / CP1 — pitch dataset assembly")
    print("=" * 72)

    # Belt-and-suspenders leakage check before assembly. (build_dataset does
    # this internally but we surface it here so a future reader sees the
    # contract loud.)
    assert_no_leakage(STUFF_FEATURES_NUMERIC + STUFF_FEATURES_CATEGORICAL)
    assert_no_leakage(LOCATION_FEATURES_NUMERIC + LOCATION_FEATURES_CATEGORICAL)
    print(f"\nLeakage guard PASSED — feature allow-lists do not include any "
          f"of {len(BANNED_OUTCOME_LEAKAGE)} banned outcome columns.")

    print(f"\nConfig:")
    for k, v in STUFF_PLUS_CFG.items():
        print(f"  {k}: {v}")

    # Build.
    print("\nBuilding dataset...")
    ds = build_dataset()

    print("\n" + "=" * 72)
    print("FILTER TRAIL")
    print("=" * 72)
    for line in ds.filter_log:
        print(f"  {line}")

    print("\n" + "=" * 72)
    print("YEAR COUNTS")
    print("=" * 72)
    train_yrs = STUFF_PLUS_CFG["train_years"]
    val_yr = STUFF_PLUS_CFG["validate_year"]
    test_yr = STUFF_PLUS_CFG["test_year"]
    train_n = sum(ds.year_counts.get(y, 0) for y in train_yrs)
    val_n = ds.year_counts.get(val_yr, 0)
    test_n = ds.year_counts.get(test_yr, 0)
    for y in sorted(ds.year_counts):
        tag = ("train" if y in train_yrs else
               "val  " if y == val_yr else
               "test " if y == test_yr else "?    ")
        print(f"  {tag} {y}: {ds.year_counts[y]:>10,} pitches")
    print(f"  {'TRAIN total':<20s}: {train_n:>10,} pitches")
    print(f"  {'VAL total':<20s}: {val_n:>10,} pitches")
    print(f"  {'TEST total':<20s}: {test_n:>10,} pitches")

    print("\n" + "=" * 72)
    print("PITCH-TYPE DISTRIBUTION (full filtered set)")
    print("=" * 72)
    total = int(ds.pitch_type_counts.sum())
    for pt, n in ds.pitch_type_counts.items():
        pct = 100.0 * n / total if total else 0
        print(f"  {pt:<6s} {n:>10,}  ({pct:5.2f}%)")

    print("\n" + "=" * 72)
    print("TOP 10 PITCHERS BY PITCH COUNT (filtered set)")
    print("=" * 72)
    top10 = ds.pitcher_counts.head(10)
    names = _resolve_pitcher_names(list(top10.index))
    for pid, n in top10.items():
        nm = names.get(int(pid), "?")
        print(f"  {nm:<28s} (id {pid}): {n:>8,} pitches")

    # Missing-rate per Stuff+ numeric feature (post-filter; should be ~0
    # because we required Stuff+ numeric complete).
    print("\n" + "=" * 72)
    print("MISSING-RATE PER STUFF+ FEATURE (post-filter, expect ~0%)")
    print("=" * 72)
    for c in STUFF_FEATURES_NUMERIC:
        miss = ds.df[c].isna().mean() if c in ds.df.columns else float("nan")
        print(f"  {c:<22s}: {100*miss:>6.3f}% missing")

    print("\n" + "=" * 72)
    print("MISSING-RATE PER LOCATION+ FEATURE (NaN-tolerated)")
    print("=" * 72)
    for c in LOCATION_FEATURES_NUMERIC:
        miss = ds.df[c].isna().mean() if c in ds.df.columns else float("nan")
        print(f"  {c:<22s}: {100*miss:>6.3f}% missing")

    print("\n" + "=" * 72)
    print("FEATURE LISTS (final, for CP2 training)")
    print("=" * 72)
    print(f"  Stuff+    ({len(ds.features_stuff)} features): {ds.features_stuff}")
    print(f"  Location+ ({len(ds.features_location)} features): {ds.features_location}")
    print(f"  Target     : {ds.target}")

    # Persist.
    out_dir = Path(STUFF_PLUS_CFG["cache_dir"])
    print(f"\nWriting dataset to {out_dir}...")
    ds.write(out_dir)
    parquet_size_mb = (out_dir / "dataset.parquet").stat().st_size / 1e6
    print(f"  dataset.parquet  : {parquet_size_mb:,.1f} MB")
    print(f"  dataset_meta.json: {(out_dir / 'dataset_meta.json').stat().st_size:,} bytes")

    print("\n" + "=" * 72)
    print("CP1 SANITY ECHO")
    print("=" * 72)
    print(f"  total filtered rows : {len(ds.df):,}")
    print(f"  pitch types kept    : {len(ds.pitch_type_counts)}")
    print(f"  pitchers ≥ {STUFF_PLUS_CFG['min_sp_pitches']} train pitches : "
          f"{(ds.pitcher_counts >= STUFF_PLUS_CFG['min_sp_pitches']).sum()}")
    print(f"  Stuff+ feature dim  : {len(ds.features_stuff)} "
          f"({len(STUFF_FEATURES_NUMERIC)} numeric + "
          f"{len(STUFF_FEATURES_CATEGORICAL)} categorical)")
    print(f"  Location+ feature dim: {len(ds.features_location)} "
          f"({len(LOCATION_FEATURES_NUMERIC)} numeric + "
          f"{len(LOCATION_FEATURES_CATEGORICAL)} categorical)")
    print(f"\nCP1 complete. Ready for CP2 review.")


if __name__ == "__main__":
    main()
