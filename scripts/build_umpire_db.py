"""
build_umpire_db.py
------------------
One-time / weekly: build the home-plate-umpire effects database used by
the v13 model. Two artifacts:

  1. data/umpire_assignments.parquet
        (game_pk, ump_id, ump_name) — pulled from MLB Stats API boxscore
        for every game in our statcast cache. ~30 min the first time
        (resumes from existing file on subsequent runs).

  2. data/umpire_effects.parquet
        (ump_id, ump_name, n_pitches, k_pct_delta, bb_pct_delta,
         called_strike_pct_delta) — per-umpire deltas vs league average
        with Bayesian shrinkage toward 0 (small-sample umps regress fully).

Usage:
    python scripts/build_umpire_db.py             # incremental
    python scripts/build_umpire_db.py --rebuild   # wipe and rebuild
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict

import pandas as pd
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from mlb_edge import data_ingestion as di

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOGS / "build_umpire_db.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("build_umpire_db")

ASSIGNMENTS_PATH = ROOT / "data" / "umpire_assignments.parquet"
EFFECTS_PATH = ROOT / "data" / "umpire_effects.parquet"

# Stable point — umpire effects stabilize fast (more pitches per game than
# any single hitter sees per season). 5000 pitches ≈ 25-30 games behind
# the plate, when called-strike rate becomes a reliable signal.
UMP_STABLE_PITCHES = 5000.0


def fetch_hp_umpire(game_pk: int) -> Dict:
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        for off in r.json().get("officials", []):
            role = off.get("officialType", "")
            if role == "Home Plate":
                return {
                    "game_pk": game_pk,
                    "ump_id": off["official"]["id"],
                    "ump_name": off["official"]["fullName"],
                }
    except Exception as e:
        log.debug("game_pk %d boxscore fetch failed: %s", game_pk, e)
    return {"game_pk": game_pk, "ump_id": None, "ump_name": None}


def build_assignments(game_pks: list[int], existing: pd.DataFrame) -> pd.DataFrame:
    """Pull HP umpire for every game_pk not already in `existing`."""
    have = set(existing["game_pk"]) if not existing.empty else set()
    todo = [pk for pk in game_pks if pk not in have]
    log.info("Fetching umpires for %d new games (%d already cached)",
             len(todo), len(have))
    if not todo:
        return existing

    rows = []
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_hp_umpire, pk): pk for pk in todo}
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            completed += 1
            if completed % 200 == 0:
                log.info("  %d/%d games fetched", completed, len(todo))
    new_df = pd.DataFrame(rows)
    new_df = new_df[new_df["ump_id"].notna()].copy()
    log.info("Pulled %d new umpire assignments (%d had no HP record)",
             len(new_df), len(rows) - len(new_df))
    if existing.empty:
        return new_df
    return pd.concat([existing, new_df], ignore_index=True)


def compute_effects(assignments: pd.DataFrame) -> pd.DataFrame:
    """Join umpire assignments with statcast pitches to compute per-umpire
    deltas in K%, BB%, and called-strike rate vs league average. Apply
    Bayesian shrinkage toward 0 by sample size.
    """
    log.info("Loading statcast 2023-2025 to compute umpire effects...")
    frames = []
    for season in (2023, 2024, 2025):
        sc = di.fetch_season_statcast(season)
        if sc.empty:
            continue
        sc["game_date"] = pd.to_datetime(sc["game_date"])
        sc = sc[sc["game_date"].dt.year == season]
        # Keep only columns we need to keep memory low
        keep = ["game_pk", "events", "description"]
        sc = sc[keep]
        frames.append(sc)
    sc = pd.concat(frames, ignore_index=True)
    log.info("Statcast pitches: %d", len(sc))

    # Join in umpire
    sc = sc.merge(assignments, on="game_pk", how="left")
    sc = sc.dropna(subset=["ump_id"]).copy()
    sc["ump_id"] = sc["ump_id"].astype(int)
    log.info("Pitches with known umpire: %d", len(sc))

    # League baselines (PA-level for K%/BB%, pitch-level for CS%)
    pa = sc[sc["events"].notna()]
    n_pa = len(pa)
    lg_k_rate = (pa["events"] == "strikeout").sum() / max(n_pa, 1)
    lg_bb_rate = (pa["events"] == "walk").sum() / max(n_pa, 1)
    lg_cs_rate = (sc["description"] == "called_strike").sum() / max(len(sc), 1)
    log.info("League rates: K=%.3f, BB=%.3f, CS=%.3f",
             lg_k_rate, lg_bb_rate, lg_cs_rate)

    # Per-umpire aggregates
    rows = []
    for ump_id, group in sc.groupby("ump_id"):
        ump_pa = group[group["events"].notna()]
        n_p = len(ump_pa)
        n_pitch = len(group)
        if n_p < 100:   # too few PAs to even compute, skip
            continue
        k_rate = (ump_pa["events"] == "strikeout").sum() / n_p
        bb_rate = (ump_pa["events"] == "walk").sum() / n_p
        cs_rate = (group["description"] == "called_strike").sum() / n_pitch

        # Shrinkage toward league mean — weight = min(n_pitch / stable, 1)
        w = min(n_pitch / UMP_STABLE_PITCHES, 1.0)
        k_rate_s = w * k_rate + (1 - w) * lg_k_rate
        bb_rate_s = w * bb_rate + (1 - w) * lg_bb_rate
        cs_rate_s = w * cs_rate + (1 - w) * lg_cs_rate

        # Effect deltas vs league (in percentage points × 100)
        rows.append({
            "ump_id": int(ump_id),
            "ump_name": group["ump_name"].iloc[0],
            "n_pitches": int(n_pitch),
            "n_pa": int(n_p),
            "k_pct_delta": (k_rate_s - lg_k_rate) * 100.0,
            "bb_pct_delta": (bb_rate_s - lg_bb_rate) * 100.0,
            "cs_pct_delta": (cs_rate_s - lg_cs_rate) * 100.0,
        })

    eff = pd.DataFrame(rows).sort_values("n_pitches", ascending=False)
    log.info("Computed effects for %d umpires (median n_pitches=%d, "
             "max k_pct_delta=%+.2fpp)",
             len(eff), int(eff["n_pitches"].median()),
             eff["k_pct_delta"].max())
    return eff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="Wipe existing umpire DB and rebuild from scratch.")
    args = ap.parse_args()

    if args.rebuild:
        for p in (ASSIGNMENTS_PATH, EFFECTS_PATH):
            if p.exists():
                p.unlink()
                log.info("wiped %s", p.name)

    # Step 1: gather game_pks from existing statcast
    log.info("=" * 60)
    log.info("STEP 1: collecting game_pks from statcast cache")
    log.info("=" * 60)
    all_pks = set()
    for season in (2023, 2024, 2025, 2026):
        try:
            sc = di.fetch_season_statcast(season) if season < 2026 else di.fetch_ytd_statcast(pd.Timestamp.today().date())
            if sc.empty:
                continue
            all_pks.update(sc["game_pk"].dropna().astype(int).tolist())
        except Exception as e:
            log.warning("season %d statcast load failed: %s", season, e)
    log.info("Total unique game_pks: %d", len(all_pks))

    # Step 2: fetch HP umpire for each
    log.info("=" * 60)
    log.info("STEP 2: fetching HP umpire assignments")
    log.info("=" * 60)
    existing = (pd.read_parquet(ASSIGNMENTS_PATH)
                if ASSIGNMENTS_PATH.exists() else pd.DataFrame())
    assignments = build_assignments(sorted(all_pks), existing)
    assignments.to_parquet(ASSIGNMENTS_PATH, index=False)
    log.info("Saved %s (%d rows)", ASSIGNMENTS_PATH.name, len(assignments))

    # Step 3: compute per-umpire effects
    log.info("=" * 60)
    log.info("STEP 3: computing per-umpire K%%/BB%%/CS%% effects")
    log.info("=" * 60)
    effects = compute_effects(assignments)
    effects.to_parquet(EFFECTS_PATH, index=False)
    log.info("Saved %s (%d umpires)", EFFECTS_PATH.name, len(effects))

    print()
    print("Top 5 K-friendly umpires (most strikeouts vs league):")
    print(effects.nlargest(5, "k_pct_delta")[
        ["ump_name", "n_pitches", "k_pct_delta", "cs_pct_delta"]
    ].to_string(index=False))
    print()
    print("Top 5 BB-friendly umpires (most walks vs league):")
    print(effects.nlargest(5, "bb_pct_delta")[
        ["ump_name", "n_pitches", "bb_pct_delta", "cs_pct_delta"]
    ].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
