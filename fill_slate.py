"""
fill_slate.py
-------------
Patch the NaN signal-absences in today's slate with MLB Stats API fallbacks.

Early in the season, `point_in_time.pitcher_as_of` returns all-NaN for any
SP with fewer than ~100 tracked pitches YTD, and `team_batting_as_of` is
likewise thin for teams with small 2026 samples. That leaves the model
handling the missing values via its default-direction split — blind to which
side actually has the stronger staff / lineup.

This script:
  1. Builds the slate the normal way (Statcast-derived YTD stats).
  2. Fetches the MLB schedule separately to recover home_sp_id / away_sp_id.
  3. For every NaN sp_*_gap or team_*_gap feature, figures out which SIDE
     (home vs away) caused the NaN by re-calling pitcher_as_of /
     team_batting_as_of and checking which returned NaN.
  4. For each NaN side, calls the corresponding `fallback_stats` function to
     pull a season-prior value from MLB Stats API.
  5. Recomputes the gap as (home_val - away_val) using the real value where
     present and the fallback value where the real was NaN.
  6. Writes an enriched slate Parquet + a human-readable audit CSV showing
     which features on which games got filled and from which source.

Usage:
    python fill_slate.py --date 2026-04-23 --out data/slate_filled.parquet

After running, re-run `analyze_slate.py` with a loader that prefers the
enriched parquet (or just re-run normally; predict will consume the same
columns — you'll want a small --slate_path arg to analyze_slate to point at
the filled version).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from mlb_edge import build_pipeline as bp
from mlb_edge import data_ingestion as di
from mlb_edge import point_in_time as pit
from mlb_edge import fallback_stats as fb
from mlb_edge.stadiums import normalize_team


# Gap feature -> (sp_* / team_* field name on each side, direction).
# direction "inv" means gap = away - home (lower-is-better stats); "fwd"
# means gap = home - away (higher-is-better). These must match the signs
# in build_pipeline._build_game_row.
SP_GAP_SPECS: List[Tuple[str, str, str]] = [
    # (gap_column_name,       underlying_sp_field,      direction)
    ("sp_xera_gap",           "sp_xera",                "inv"),
    ("sp_xwoba_allowed_gap",  "sp_xwoba_allowed",       "inv"),
    ("sp_fip_gap",            "sp_fip",                 "inv"),
    ("sp_siera_gap",          "sp_siera",               "inv"),
    ("sp_k_bb_pct_gap",       "sp_k_bb_pct",            "fwd"),
    ("sp_recent_form_gap",    "sp_recent_xfip",         "inv"),
    ("sp_hardhit_gap",        "sp_hardhit_pct_allowed", "inv"),
    ("sp_stamina_gap",        "sp_ip_per_start",        "fwd"),
]

TEAM_GAP_SPECS: List[Tuple[str, str, str]] = [
    ("team_wrcplus_gap",      "team_wrc_plus",          "fwd"),
    ("team_woba_gap",         "team_xwoba",             "fwd"),
    ("team_hardhit_gap",      "team_hardhit_pct",       "fwd"),
]
# team_bbk_gap is (bb_home - bb_away) - (k_home - k_away), handled specially.

BULLPEN_GAP_SPECS: List[Tuple[str, str, str]] = [
    ("bullpen_siera_gap",     "bullpen_xera",           "inv"),
]


def _gap(home_val: float, away_val: float, direction: str) -> float:
    """Mirror build_pipeline's fwd/inv lambdas."""
    if pd.isna(home_val) or pd.isna(away_val):
        return np.nan
    return (home_val - away_val) if direction == "fwd" else (away_val - home_val)


def _pitcher_dict_is_thin(d: Dict[str, float]) -> bool:
    """A `pitcher_as_of` dict is 'thin' when every metric we care about is
    NaN (the < min_pitches path returns _nan_pitcher_dict)."""
    for k in ("sp_xera", "sp_xwoba_allowed", "sp_fip"):
        v = d.get(k)
        if pd.notna(v):
            return False
    return True


def _team_dict_is_thin(d: Dict[str, float]) -> bool:
    for k in ("team_wrc_plus", "team_xwoba"):
        v = d.get(k)
        if pd.notna(v):
            return False
    return True


def _bullpen_dict_is_thin(d: Dict[str, float]) -> bool:
    return pd.isna(d.get("bullpen_xera"))


def fill_one_game(row: pd.Series,
                  sc: pd.DataFrame,
                  starters_by_team: Dict[str, set],
                  home_sp_id: Optional[int],
                  away_sp_id: Optional[int],
                  game_date: pd.Timestamp,
                  home_sp_name: str = "?",
                  away_sp_name: str = "?",
                  ) -> Tuple[Dict[str, float], Dict]:
    """Patch NaN gap features on a single game row using MLB Stats API
    season-prior fallbacks.

    Pure function — does not mutate `row`. Returns:
      patches:  {gap_col: new_val, ...} for each NaN that was filled
      audit:    per-game source info (which side fell back, to which season)

    Callers:
      - fill_slate.py main() — iterates the slate, applies patches in-place.
      - backtest_fill_2026.py — iterates historical games, applies patches
        to a copy of the frame, then scores raw vs filled through the model.

    Safety note: `game_date` is used ONLY to pass to pit.pitcher_as_of /
    team_batting_as_of (point-in-time on Statcast). The MLB Stats API
    fallback uses prior-season totals (2025/2024/2023), so the fill does
    NOT leak the game's own outcome or future games' data.

    The filled feature values are full-strength (not blended toward raw,
    since raw is NaN for these fields). If the downstream model is
    over-confident on pure-fallback predictions — as the 2026 backtest
    showed — the correction is to blend at the PROBABILITY level
    (see predict_blended.py / backtest_fill_2026.py shrinkage curve),
    not the feature level.
    """
    home = row["home_team"]
    away = row["away_team"]

    home_sp = pit.pitcher_as_of(sc, home_sp_id, game_date) if home_sp_id else {}
    away_sp = pit.pitcher_as_of(sc, away_sp_id, game_date) if away_sp_id else {}
    home_off = pit.team_batting_as_of(sc, home, game_date)
    away_off = pit.team_batting_as_of(sc, away, game_date)
    home_bp = pit.bullpen_as_of(sc, home, game_date, starters_by_team)
    away_bp = pit.bullpen_as_of(sc, away, game_date, starters_by_team)

    home_sp_src, away_sp_src = "statcast", "statcast"
    if _pitcher_dict_is_thin(home_sp) and home_sp_id:
        fallback = fb.pitcher_fallback(home_sp_id)
        home_sp_src = fallback.pop("_source", "fallback")
        home_sp = {**home_sp, **fallback}
    if _pitcher_dict_is_thin(away_sp) and away_sp_id:
        fallback = fb.pitcher_fallback(away_sp_id)
        away_sp_src = fallback.pop("_source", "fallback")
        away_sp = {**away_sp, **fallback}

    home_off_src, away_off_src = "statcast", "statcast"
    if _team_dict_is_thin(home_off):
        fallback = fb.team_offense_fallback(home)
        home_off_src = fallback.pop("_source", "fallback")
        home_off = {**home_off, **fallback}
    if _team_dict_is_thin(away_off):
        fallback = fb.team_offense_fallback(away)
        away_off_src = fallback.pop("_source", "fallback")
        away_off = {**away_off, **fallback}

    home_bp_src, away_bp_src = "statcast", "statcast"
    if _bullpen_dict_is_thin(home_bp):
        fallback = fb.bullpen_fallback(home)
        home_bp_src = fallback.pop("_source", "fallback")
        home_bp = {**home_bp, **fallback}
    if _bullpen_dict_is_thin(away_bp):
        fallback = fb.bullpen_fallback(away)
        away_bp_src = fallback.pop("_source", "fallback")
        away_bp = {**away_bp, **fallback}

    patches: Dict[str, float] = {}
    filled_cols: List[str] = []

    for gap_col, field, direction in SP_GAP_SPECS:
        if pd.isna(row.get(gap_col)):
            new_val = _gap(home_sp.get(field), away_sp.get(field), direction)
            if pd.notna(new_val):
                patches[gap_col] = new_val
                filled_cols.append(gap_col)

    for gap_col, field, direction in TEAM_GAP_SPECS:
        if pd.isna(row.get(gap_col)):
            new_val = _gap(home_off.get(field), away_off.get(field), direction)
            if pd.notna(new_val):
                patches[gap_col] = new_val
                filled_cols.append(gap_col)

    if pd.isna(row.get("team_bbk_gap")):
        bb_gap = _gap(home_off.get("team_bb_pct"),
                      away_off.get("team_bb_pct"), "fwd")
        k_gap = _gap(home_off.get("team_k_pct"),
                     away_off.get("team_k_pct"), "fwd")
        if pd.notna(bb_gap) and pd.notna(k_gap):
            patches["team_bbk_gap"] = bb_gap - k_gap
            filled_cols.append("team_bbk_gap")

    for gap_col, field, direction in BULLPEN_GAP_SPECS:
        if pd.isna(row.get(gap_col)):
            new_val = _gap(home_bp.get(field), away_bp.get(field), direction)
            if pd.notna(new_val):
                patches[gap_col] = new_val
                filled_cols.append(gap_col)

    if pd.isna(row.get("home_sp_luck")):
        v = home_sp.get("sp_era_xera_gap")
        if pd.notna(v):
            patches["home_sp_luck"] = v
            filled_cols.append("home_sp_luck")
    if pd.isna(row.get("away_sp_luck")):
        v = away_sp.get("sp_era_xera_gap")
        if pd.notna(v):
            patches["away_sp_luck"] = v
            filled_cols.append("away_sp_luck")

    audit = {
        "game_id":      int(row["game_id"]) if "game_id" in row else None,
        "away":         away,
        "home":         home,
        "home_sp_id":   home_sp_id,
        "home_sp_name": home_sp_name,
        "home_sp_src":  home_sp_src,
        "away_sp_id":   away_sp_id,
        "away_sp_name": away_sp_name,
        "away_sp_src":  away_sp_src,
        "home_off_src": home_off_src,
        "away_off_src": away_off_src,
        "home_bp_src":  home_bp_src,
        "away_bp_src":  away_bp_src,
        "n_filled":     len(filled_cols),
        "filled_cols":  ",".join(filled_cols) if filled_cols else "-",
    }
    return patches, audit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True,
                    type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--out", default="data/slate_filled.parquet",
                    help="Enriched slate written here.")
    ap.add_argument("--audit", default="slate_fill_audit.csv",
                    help="Per-game fill audit (what got replaced, from where).")
    args = ap.parse_args()

    # -----------------------------------------------------------------
    # 1. Build the slate the normal way.
    # -----------------------------------------------------------------
    print(f"Building slate for {args.date} via the normal pipeline...")
    games = bp.build_slate_frame(args.date)
    if games.empty:
        print("No slate. Abort.")
        return
    print(f"  slate has {len(games)} games")

    # -----------------------------------------------------------------
    # 2. Pull schedule separately to get probable-pitcher IDs.
    # -----------------------------------------------------------------
    schedule = di.fetch_schedule_mlb_api(args.date)
    # Map game_pk -> (home_sp_id, away_sp_id, home_sp_name, away_sp_name)
    sched_by_pk: Dict[int, Dict] = {g["game_pk"]: g for g in schedule}

    # Pull the SAME Statcast frame build_slate_frame used, so we can
    # re-interrogate pitcher_as_of / team_batting_as_of per game.
    sc = di.fetch_ytd_statcast(args.date - timedelta(days=1))
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    starters_by_team = pit.infer_starters_by_team(sc)

    # -----------------------------------------------------------------
    # 3. Per-game audit loop.
    # -----------------------------------------------------------------
    audit_rows: List[Dict] = []
    filled = games.copy()

    for idx, row in games.iterrows():
        gpk = int(row["game_id"])
        meta = sched_by_pk.get(gpk, {})
        patches, audit = fill_one_game(
            row=row,
            sc=sc,
            starters_by_team=starters_by_team,
            home_sp_id=meta.get("home_sp_id"),
            away_sp_id=meta.get("away_sp_id"),
            game_date=pd.Timestamp(args.date),
            home_sp_name=meta.get("home_sp_name", "?"),
            away_sp_name=meta.get("away_sp_name", "?"),
        )
        for col, val in patches.items():
            filled.at[idx, col] = val
        audit_rows.append(audit)

    # -----------------------------------------------------------------
    # 4. Persist.
    # -----------------------------------------------------------------
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    filled.to_parquet(args.out, index=False)
    print(f"  wrote enriched slate to {args.out}")

    audit_df = pd.DataFrame(audit_rows)
    audit_df.to_csv(args.audit, index=False)
    print(f"  wrote audit to {args.audit}")
    print()

    # -----------------------------------------------------------------
    # 5. Human-readable summary.
    # -----------------------------------------------------------------
    print("=" * 82)
    print("FILL AUDIT")
    print("=" * 82)
    display_cols = ["away", "home", "home_sp_src", "away_sp_src",
                    "home_off_src", "away_off_src", "home_bp_src",
                    "away_bp_src", "n_filled"]
    print(audit_df[display_cols].to_string(index=False))
    print()

    # Before/after NaN counts for the columns we try to fill
    all_gaps = [s[0] for s in SP_GAP_SPECS] \
             + [s[0] for s in TEAM_GAP_SPECS] \
             + ["team_bbk_gap"] \
             + [s[0] for s in BULLPEN_GAP_SPECS] \
             + ["home_sp_luck", "away_sp_luck"]
    before_na = games[all_gaps].isna().sum()
    after_na = filled[all_gaps].isna().sum()

    print("=" * 82)
    print("NaN COUNTS (per feature, across slate)")
    print("=" * 82)
    print(f"  {'feature':<24s} {'before':>8s}  {'after':>8s}  filled")
    print(f"  {'-'*24} {'-'*8}  {'-'*8}  ------")
    for c in all_gaps:
        b, a = int(before_na[c]), int(after_na[c])
        # ASCII-only tag; Windows console's default cp1252 codec cannot encode
        # unicode check-marks and the print crashes mid-report.
        tag = "fix" if b > a else " - " if b == 0 else "!!!"
        print(f"  {c:<24s} {b:>8d}  {a:>8d}  {tag}  (filled {b - a})")
    print()

    # Features we know the fallback can't touch (Statcast-only pitch-level).
    pitch_only = ["sp_velo_drop_gap", "sp_vs_lineup_gap", "sp_rest_gap"]
    avail = [c for c in pitch_only if c in games.columns]
    if avail:
        print("UN-FILLABLE (pitch-level Statcast signals, no season-stat fallback):")
        for c in avail:
            n = int(games[c].isna().sum())
            print(f"  {c:<24s} {n:>8d} NaN still")
        print()


if __name__ == "__main__":
    main()
