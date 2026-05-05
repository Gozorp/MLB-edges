"""Phase 1 — Pitch-quality features (Stuff+, Location+).

Two XGBoost regressors trained on `delta_run_exp` (per-pitch run value, negative
= pitcher-favorable). Disjoint feature sets:

  * Stuff+    — physical pitch attributes only (velocity, movement, release
                geometry, spin). NO location, NO count, NO outcome features.
  * Location+ — location + count + handedness only. NO physical attributes.

Why split: a 2026-05-01 retrospective showed bust-side SPs failed in two
distinct modes — half lost the strike zone (pure command failure, Stuff+ would
say they're fine), half got barreled (pure contact failure, Location+ would
say they're fine). A single Pitching+ blend loses the discrimination. We give
XGBoost the two scores as separate features and let it weight them per-game.

Output rescale (both metrics):
    score = center - (model_pred - league_mean) / league_sd * scale
where league_mean / league_sd are computed on the training set and persisted
in `models/stuff_plus_v1.json` next to the pickled model. Sign is flipped so
HIGHER score = better pitcher (because lower predicted run-value-allowed is
better). Default {center=100, scale=10} → roughly 80–120 league-wide range.

This module ships with CP1 (dataset assembly only). CP2 trains the model.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import STUFF_PLUS_CFG

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature allow-lists (LEAKAGE GUARD — additions to the Statcast schema
# can't sneak into training without an explicit edit here)
# ---------------------------------------------------------------------------
# Stuff+ inputs — physical attributes set BEFORE the pitch crosses the plate.
# Movement (pfx_x/z, ax/ay/az), release geometry, spin. Nothing about WHERE
# the pitch ended up or how the batter responded.
STUFF_FEATURES_NUMERIC = [
    "release_speed",       # mph at release
    "release_pos_x",       # release point — x (catcher's view, left/right)
    "release_pos_y",       # release point — y (60' from plate, depth)
    "release_pos_z",       # release point — z (height)
    "release_extension",   # how far down the mound the pitcher releases from
    "release_spin_rate",   # rpm
    "spin_axis",           # degrees, 0–360
    "pfx_x",               # horizontal movement, ft
    "pfx_z",               # induced vertical movement, ft
    "vx0",                 # velocity components at release (post-acceleration model)
    "vy0",
    "vz0",
    "ax",                  # acceleration components in flight
    "ay",
    "az",
]
STUFF_FEATURES_CATEGORICAL = [
    "pitch_type",          # FF, SI, SL, CU, CH, FC, KC, ST, FS — encoded
    "p_throws",            # L / R
    "stand",               # batter handedness — relevant for L/R movement read
]

# Location+ inputs — purely WHERE the pitch is and game-state context. No
# physical attributes from STUFF (pitchers should be evaluated only on what
# they put in the zone, given count + batter platoon).
LOCATION_FEATURES_NUMERIC = [
    "plate_x",             # horizontal, ft, 0 = middle
    "plate_z",             # vertical, ft, ground = 0
    "balls",
    "strikes",
    "sz_top",              # batter's top of zone
    "sz_bot",              # batter's bottom of zone
]
LOCATION_FEATURES_CATEGORICAL = [
    "p_throws",
    "stand",
    "pitch_type",          # included only because called-strike rates differ by
                           # pitch shape at the same zone location (e.g., a
                           # back-foot slider counts differently than a fastball
                           # at the same plate_x for a same-handed batter).
]

# Hard banlist — pasted here so a future code reader sees it explicitly even
# though the feature lists above are an allow-list. Anything outcome-side or
# downstream of the swing decision is excluded.
BANNED_OUTCOME_LEAKAGE = {
    "description", "events", "type",        # pitch outcome strings
    "hit_distance_sc", "launch_angle",      # batted-ball outcome
    "launch_speed", "hc_x", "hc_y",         # batted-ball outcome
    "estimated_woba_using_speedangle",      # post-contact xstat
    "estimated_ba_using_speedangle",
    "delta_run_exp", "delta_pitcher_run_exp",  # the TARGET — must stay out of X
    "post_away_score", "post_home_score",   # post-pitch state
    "post_bat_score", "post_fld_score",
    "des",                                  # the play-by-play sentence
    "iso_value", "babip_value", "launch_speed_angle",
}

# Target.
TARGET_COL = "delta_run_exp"


@dataclass(frozen=True)
class PitchDataset:
    """Container for the assembled pitch-level training data."""
    df: pd.DataFrame
    features_stuff: List[str]
    features_location: List[str]
    target: str
    year_counts: Dict[int, int]
    pitcher_counts: pd.Series
    pitch_type_counts: pd.Series
    filter_log: List[str]

    def write(self, out_dir: Path) -> None:
        """Persist to parquet + a metadata JSON."""
        out_dir.mkdir(parents=True, exist_ok=True)
        self.df.to_parquet(out_dir / "dataset.parquet", index=False)
        meta = {
            "n_rows": int(len(self.df)),
            "year_counts": {int(y): int(n) for y, n in self.year_counts.items()},
            "features_stuff": self.features_stuff,
            "features_location": self.features_location,
            "target": self.target,
            "filter_log": self.filter_log,
            "top10_pitchers_by_count": [
                {"pitcher_id": int(pid), "n_pitches": int(n)}
                for pid, n in self.pitcher_counts.head(10).items()
            ],
            "pitch_type_counts": {str(t): int(n) for t, n in self.pitch_type_counts.items()},
        }
        (out_dir / "dataset_meta.json").write_text(json.dumps(meta, indent=2))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _load_chunks(chunk_dir: str) -> pd.DataFrame:
    """Concatenate all statcast_chunk parquets. We need a superset of
    columns (allow-list + game_year + pitcher + game_date) — passing
    `columns=` to pd.read_parquet trims memory by ~5x.

    `game_date` was added back at CP4 prep (2026-05-02): the rolling-60d
    per-SP aggregator that build_pipeline calls at slate time needs a
    proper date filter, not just a year filter. Adds ~22 MB to the
    parquet (~3M rows × 8 bytes) — small price for the precision."""
    needed = set(STUFF_FEATURES_NUMERIC + STUFF_FEATURES_CATEGORICAL
                 + LOCATION_FEATURES_NUMERIC + LOCATION_FEATURES_CATEGORICAL
                 + [TARGET_COL, "game_year", "game_date",
                    "pitcher", "description"])
    files = sorted(glob.glob(os.path.join(chunk_dir, "*.parquet")))
    if not files:
        raise FileNotFoundError(f"No chunks at {chunk_dir}")
    log.info("Loading %d statcast chunks...", len(files))
    frames = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=list(needed))
        except Exception:
            # Older chunks may lack some recent columns — load what's there,
            # missing-fill at concat time.
            df = pd.read_parquet(f)
            for c in needed - set(df.columns):
                df[c] = np.nan
            df = df[list(needed)]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    # Normalize game_date — chunks have a mix of pd.Timestamp and str dtypes
    # which pyarrow can't unify when we write the parquet. Coerce to ISO
    # date string (YYYY-MM-DD); the rolling aggregator parses back to
    # Timestamp at slate-build time.
    if "game_date" in out.columns:
        out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce") \
            .dt.strftime("%Y-%m-%d")
    log.info("Loaded %s rows total", f"{len(out):,}")
    return out


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _apply_filters(df: pd.DataFrame, cfg: dict) -> Tuple[pd.DataFrame, List[str]]:
    """Sequence of filters with row counts logged so the reduction is
    auditable. Each filter prunes rows based on a single condition."""
    log_lines: List[str] = []
    n0 = len(df)
    log_lines.append(f"raw chunks                       : {n0:>10,}")

    # 1. Years in scope.
    yr_keep = set(cfg["train_years"]) | {cfg["validate_year"], cfg["test_year"]}
    df = df[df["game_year"].isin(yr_keep)].copy()
    log_lines.append(f"after year filter ({sorted(yr_keep)})    : {len(df):>10,}")

    # 2. Drop pitchouts / intentional balls / automatic balls (pitch-clock).
    if "description" in df.columns:
        bad = pd.Series([str(d).lower() in cfg["exclude_descriptions"]
                         for d in df["description"].fillna("")])
        df = df[~bad].copy()
        log_lines.append(f"after pitchout/IBB/auto-ball drop  : {len(df):>10,}")

    # 3. Position-player guard — drop pitches with absurdly low velocity.
    df = df[df["release_speed"].fillna(0) >= cfg["min_release_speed"]].copy()
    log_lines.append(f"after release_speed >= {cfg['min_release_speed']}      : {len(df):>10,}")

    # 4. Drop rows where the target is null (some Statcast rows have no
    # delta_run_exp — usually the first pitch of an event sequence or
    # malformed records). We can't train on these.
    df = df[df[TARGET_COL].notna()].copy()
    log_lines.append(f"after target-not-null              : {len(df):>10,}")

    # 5. Pitch-type sample filter. Compute counts on the TRAIN portion only
    # so we don't leak future data, then apply across the full set.
    train_mask = df["game_year"].isin(cfg["train_years"])
    pt_train_counts = df.loc[train_mask, "pitch_type"].value_counts(dropna=True)
    keep_types = pt_train_counts[pt_train_counts >= cfg["min_pitches_per_type"]].index
    df = df[df["pitch_type"].isin(keep_types)].copy()
    log_lines.append(f"after pitch-type >= {cfg['min_pitches_per_type']:,} (train) : {len(df):>10,}")
    log_lines.append(f"  kept pitch types                : {sorted(keep_types.tolist())}")

    # 6. Drop rows with any NaN in Stuff+ numeric features (XGBoost handles
    # NaN natively but for a regressor on physical attributes a missing
    # release_speed signals a bad row). Location+ NaNs are tolerated.
    stuff_complete = df[STUFF_FEATURES_NUMERIC].notna().all(axis=1)
    df = df[stuff_complete].copy()
    log_lines.append(f"after Stuff+ numeric complete      : {len(df):>10,}")

    # 7. Pitcher minimum sample. Same train-only basis.
    pit_train_counts = df.loc[df["game_year"].isin(cfg["train_years"]), "pitcher"].value_counts()
    keep_pids = pit_train_counts[pit_train_counts >= cfg["min_sp_pitches"]].index
    df = df[df["pitcher"].isin(keep_pids)].copy()
    log_lines.append(f"after pitcher >= {cfg['min_sp_pitches']} pitches (train): {len(df):>10,}")

    return df, log_lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def assert_no_leakage(features: List[str]) -> None:
    """Raise if any banned column slipped into the feature list. Belt-and-
    suspenders since the lists above are an allow-list — but a future edit
    that mistakenly adds an outcome feature should fail loud."""
    leaked = set(features) & BANNED_OUTCOME_LEAKAGE
    if leaked:
        raise ValueError(
            f"Pitch-quality feature list contains banned outcome leakage: "
            f"{sorted(leaked)}. Remove these from STUFF_FEATURES_* / "
            f"LOCATION_FEATURES_* in mlb_edge/pitch_quality.py."
        )
    if TARGET_COL in features:
        raise ValueError(f"Target column `{TARGET_COL}` cannot be a feature.")


def to_xgb_frame(df: pd.DataFrame, features: List[str]) -> pd.DataFrame:
    """Cast categorical columns to pandas `category` dtype so XGBoost
    `enable_categorical=True` can train on them natively. Numeric columns
    are passed through. The category set is taken from the input slice — at
    inference time the caller MUST pass the same `categories` list, so we
    persist them in the model artifact.
    """
    out = df[features].copy()
    cat_cols = [c for c in features if c in
                (STUFF_FEATURES_CATEGORICAL + LOCATION_FEATURES_CATEGORICAL)]
    for c in cat_cols:
        out[c] = out[c].astype("category")
    return out


def aggregate_per_sp_rolling(scored: pd.DataFrame,
                                slate_date,
                                window_days: int = 60,
                                min_pitches: int = 200) -> pd.DataFrame:
    """Roll-up per-pitch Stuff+/Location+ to per-SP-as-of-slate.

    For each pitcher, takes pitches with game_date in
    [slate_date - window_days, slate_date - 1 day] and computes the
    pitch-count-weighted mean. Pitchers under `min_pitches` in the window
    are returned with NaN scores; the caller (build_pipeline) substitutes
    league mean (= the rescale `center`, default 100) for those rows.

    Same window contract as `_build_sp_features`'s xera_gap rolling
    lookback. Honoring it keeps Stuff+ consistent with the rest of the
    SP_matchup family — no temporal leak (we never include the slate-day
    pitches themselves in the feature) and no stale cache (early-season
    slates trim shorter naturally because there's less data).
    """
    if scored.empty:
        return pd.DataFrame(columns=["pitcher", "n_pitches",
                                       "stuff_plus", "location_plus"])
    slate_date = pd.Timestamp(slate_date)
    cutoff_lo = slate_date - pd.Timedelta(days=window_days)
    cutoff_hi = slate_date - pd.Timedelta(days=1)
    g = scored.copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    g = g[(g["game_date"] >= cutoff_lo) & (g["game_date"] <= cutoff_hi)]
    out = (g.groupby("pitcher")
           .agg(n_pitches=("stuff_plus", "size"),
                stuff_plus=("stuff_plus", "mean"),
                location_plus=("location_plus", "mean"))
           .reset_index())
    # Mark thin samples with NaN — caller decides fallback policy.
    thin = out["n_pitches"] < min_pitches
    out.loc[thin, ["stuff_plus", "location_plus"]] = np.nan
    return out


def score_pitches(df: pd.DataFrame,
                   stuff_model, location_model,
                   norms: Dict[str, dict]) -> pd.DataFrame:
    """Apply trained models to a pitch-level DataFrame and return Stuff+ /
    Location+ per pitch. `norms` is the JSON loaded from
    `models/pitch_quality_norms_v1.json` and contains the league mean/sd
    used at training time (must NOT be recomputed at inference).

    Sign convention: HIGHER score = better. Lower predicted run value
    means a tougher pitch for hitters, so we negate the z-score.
    """
    out = df.copy()
    # Restore category dtypes that the persisted artifact specifies.
    cats = norms["categories"]
    for c, levels in cats.items():
        if c in out.columns:
            out[c] = pd.Categorical(out[c], categories=levels)

    Xs = out[stuff_model.feature_names_in_]
    Xl = out[location_model.feature_names_in_]
    pred_stuff = stuff_model.predict(Xs)
    pred_loc = location_model.predict(Xl)
    s = norms["stuff_plus"]
    L = norms["location_plus"]
    out["stuff_plus"] = s["center"] - (pred_stuff - s["mean"]) / s["sd"] * s["scale"]
    out["location_plus"] = L["center"] - (pred_loc - L["mean"]) / L["sd"] * L["scale"]
    return out


def build_dataset(cfg: Optional[dict] = None,
                   chunk_dir: str = "data/statcast_cache/statcast_chunk"
                   ) -> PitchDataset:
    """Assemble the filtered pitch-level dataset.

    Returns a `PitchDataset` with:
      - df: filtered DataFrame, keeps train/val/test split via `game_year`.
      - features_stuff / features_location: column lists with leakage-checked.
      - year_counts, pitcher_counts, pitch_type_counts: sanity counters.
      - filter_log: human-readable trail of pruning steps.
    """
    cfg = {**STUFF_PLUS_CFG, **(cfg or {})}

    # Static leakage check before we even touch data.
    assert_no_leakage(STUFF_FEATURES_NUMERIC + STUFF_FEATURES_CATEGORICAL)
    assert_no_leakage(LOCATION_FEATURES_NUMERIC + LOCATION_FEATURES_CATEGORICAL)

    raw = _load_chunks(chunk_dir)
    df, filter_log = _apply_filters(raw, cfg)

    year_counts = df["game_year"].value_counts().sort_index().to_dict()
    pitcher_counts = df["pitcher"].value_counts()
    pitch_type_counts = df["pitch_type"].value_counts()

    # Final feature lists — the union of categorical labels included as raw
    # strings; XGBoost will get one-hot or label-encoded at training time.
    features_stuff = STUFF_FEATURES_NUMERIC + STUFF_FEATURES_CATEGORICAL
    features_location = LOCATION_FEATURES_NUMERIC + LOCATION_FEATURES_CATEGORICAL

    return PitchDataset(
        df=df,
        features_stuff=features_stuff,
        features_location=features_location,
        target=TARGET_COL,
        year_counts=year_counts,
        pitcher_counts=pitcher_counts,
        pitch_type_counts=pitch_type_counts,
        filter_log=filter_log,
    )
