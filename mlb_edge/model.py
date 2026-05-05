"""
model.py
--------
Two-stage gradient-boosted model.

Stage 1 (F5 model):
    Inputs  : ONLY starting-pitcher gap features.
    Target  : home team leads after 5 innings (binary).
    Rationale: Lock in the SP signal before bullpen/offense variables have a
               chance to dilute it in Stage 2's feature space.

Stage 2 (Full game model):
    Inputs  : Stage 1 predicted probability + offense + bullpen + context.
    Target  : home team wins the game (binary).
    Rationale: Stage 1's output is a strong anchor feature. Because it is
               derived purely from SP data, Stage 2 cannot "unlearn" the SP
               effect even if it overfits to a noisy offensive stat.

Monotonic constraints enforce that, e.g., a bigger SP xERA gap in our favor
cannot decrease our predicted win probability. This is a formal guardrail
against overfitting-driven nonsense splits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

import xgboost as xgb
from sklearn.isotonic import IsotonicRegression  # kept for legacy saved-model loads
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from .config import (
    EARLY_STOPPING_ROUNDS,
    ENVIRONMENTAL_PARK_THRESHOLD,
    F5_MONOTONE,
    F5_OVERRIDE_ON_DISAGREEMENT,
    FULL_MONOTONE,
    XGB_PARAMS_F5,
    XGB_PARAMS_FULL,
)


# ---------------------------------------------------------------------------
# Calibration settings
# ---------------------------------------------------------------------------
# DISABLED BY DEFAULT — see `diagnose_calibrator.py` and the verdict below.
#
# The original design called for a monotonic calibration layer (isotonic or
# Platt/sigmoid) on Stage 2's raw output, on the theory that XGBoost is
# mis-calibrated near the tails and a correction would lift ROI.
#
# Empirical test (seasons 2024-2025, 3,580 honestly-OOF games from
# walk-forward training):
#
#   Metric           Raw        Platt (slope 13.2,     Platt (slope 0.87,
#                               last-15% in-sample     OOF inner-CV fit)
#                               holdout — leaky)
#   Brier            0.2466     0.2514 (+0.005)        0.2481 (+0.002)
#   Log loss         0.686      0.698 (+0.012)         0.689 (+0.003)
#   Decile |err|     0.0222     0.0594 (2.67x worse)   0.0394 (1.77x worse)
#   Sharpness        0.0481     0.1054 (2.19x)         0.0391 (0.81x)
#
# Raw beats both calibrators on every metric. The old "last-15%" fit leaked
# (Stage 2 had trained on those rows) producing an over-sharp slope. The
# new OOF inner-CV fit is honest but over-regresses — inner-CV boosters see
# less data than the production booster, so their raw probs are noisier,
# and Platt correctly pulls them toward the base rate — but that flattening
# doesn't generalize to the production booster's sharper raw probs.
#
# Stage 2's mis-calibration pattern on OOF data is a zigzag, not a smooth
# over/under-confidence, so a 2-parameter sigmoid cannot capture it. A richer
# calibrator (binned isotonic with smoothing, or per-bin shrinkage) could
# try, but the raw numbers already beat anything we've fit. Flip this off.
#
# Stage 1 is ALSO not calibrated — its raw probability is consumed by Stage 2
# as a feature, and a ranking-only signal is what Stage 2 wants there.
ENABLE_STAGE2_CALIBRATION: bool = False
CALIBRATION_HOLDOUT_FRAC: float = 0.15   # retained for legacy code paths
CALIBRATION_MIN_ROWS: int = 80           # skip calibration when holdout is too thin

log = logging.getLogger(__name__)


F5_FEATURES: List[str] = [
    "sp_xera_gap", "sp_xwoba_allowed_gap", "sp_fip_gap", "sp_siera_gap",
    "sp_k_bb_pct_gap", "sp_recent_form_gap", "sp_hardhit_gap", "sp_stamina_gap",
    # Tier-1 enrichments (rest days, velocity drop, handedness matchup).
    # Populated when the caller passes a Statcast frame into
    # build_game_row / _build_game_row. `train_stage1_f5` and
    # `_oof_f5_predictions` below filter this list to columns that actually
    # exist, so older cached parquets without these columns still train
    # cleanly — they just fall back to the original 8-feature SP model.
    "sp_rest_gap", "sp_velo_drop_gap", "sp_vs_lineup_gap",
]

FULL_FEATURES_EXTRA: List[str] = [
    "f5_model_output",
    "team_wrcplus_gap", "team_woba_gap", "team_bbk_gap", "team_hardhit_gap",
    "bullpen_siera_gap", "bullpen_fatigue_gap",
    # v11 bullpen rate stats — additional signal channels less noisy than xERA
    # in early-season samples, with prior-year team-aggregate shrinkage.
    "bullpen_xwoba_gap", "bullpen_k_pct_gap",
    "bullpen_bb_pct_gap", "bullpen_hardhit_gap",
    # v12 high-leverage bullpen — late-inning (7th+) reliever aggregate.
    # Catches the closer/setup gap the team-bullpen feature dilutes.
    "hl_bullpen_xera_gap", "hl_bullpen_xwoba_gap",
    # v13 home-plate umpire effects — K%/BB%/CS% deltas vs league average.
    # Ambient features (both teams face the same ump). Pitcher-friendly
    # umps boost K signal; hitter-friendly umps boost run environment.
    "ump_k_pct_delta", "ump_bb_pct_delta", "ump_cs_pct_delta",
    "park_runs_factor", "park_hr_factor",
    "home_ump_boost", "away_ump_boost",
    "home_catcher_penalty", "away_catcher_penalty",
    "home_sp_luck", "away_sp_luck",
    "is_divisional", "tz_diff", "is_opener", "is_quick_turnaround",
    # Lineup-aware offense (cache v5). Gaps are positive when the home
    # lineup rates higher. Kept alongside the team_* aggregates so XGBoost
    # can learn which signal is more predictive in which regime (small
    # samples / unusual lineups -> team aggregate wins; full lineup posted
    # + healthy sample -> lineup signal should contribute).
    "lineup_vs_sp_gap",
    "lineup_wrcplus_gap",
    "lineup_hardhit_gap",
    # Savant bat-tracking (cache v6). Player-level leaderboard aggregated
    # to team-level, competitive-swing-weighted. These capture *underlying*
    # quality of contact the wRC+/xwOBA aggregates smooth over — a team
    # swinging 75 mph with a 20% squared-up rate is more dangerous than its
    # early-season results suggest. Pulled from Savant CSV, refreshed daily.
    "team_bat_speed_gap",
    "team_squared_up_swing_gap",
    "team_blast_swing_gap",
    "team_batter_run_value_gap",
    "team_whiff_rate_gap",
    # Baseball-Reference team form (cache v7). Standings-derived gaps over
    # full season-to-date — complements the Savant contact quality metrics
    # with a raw "are they winning?" signal the booster can trade off
    # against bat-tracking on small samples.
    "team_win_pct_gap",
    "team_run_diff_pg_gap",
    "team_pythagorean_gap",
    # Starter-pitcher sample reliability (cache v8). Min(home, away)
    # n_pitches / 1500, clipped to [0, 1]. This is the training-level
    # response to the 2026-04-24 MIL-vs-Skenes failure: Woodruff was
    # returning from injury with ~64 IP and the booster had no way to know
    # the xERA gap was unreliable. With this feature in the tree, splits
    # like "sp_xera_gap >= 1.5 AND sp_sample_reliability < 0.5 ->
    # attenuate" are reachable.
    "sp_sample_reliability",
    # Comprehensive expansion (cache v9, 2026-04-24).
    # Defense — OAA / FRP / FRV gaps from Savant fielding leaderboards.
    "team_oaa_gap",
    "team_frp_gap",
    "team_frv_gap",
    # Weather — humidity affects ball carry; precip cancels/delays games and
    # depresses offense; wind direction relative to park (degrees, 0=CF) lets
    # the booster combine with wind_out for fly-ball physics; roof_type lets
    # it discount weather under domes/retractables.
    "humidity_pct",
    "precip_prob",
    "wind_dir_park",
    "home_roof_type",
    # Schedule context — day games behave differently (sun, shadows, smaller
    # crowds), and day-of-week tilts attendance/lineup rotation. Encoded
    # cyclically so Mon/Sun aren't artificially distant.
    "is_day_game",
    "dow_sin",
    "dow_cos",
    # SP times-third-through-order penalty (xwOBA(TTOP3+) - xwOBA(TTOP1)).
    # Positive home gap means home pitcher ages BETTER than away pitcher
    # (away SP wears down more) — a real edge late in games. Used by the
    # full-game model only since F5 wraps before TTOP3 typically lands.
    "sp_ttop3_penalty_gap",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _monotone_tuple(features: List[str], mono_map: Dict[str, int]) -> str:
    """XGBoost expects a parenthesized string like '(1,1,0,-1,0,...)'."""
    return "(" + ",".join(str(mono_map.get(f, 0)) for f in features) + ")"


def _xy_split(df: pd.DataFrame, feature_cols: List[str],
              target_col: str) -> Tuple[pd.DataFrame, pd.Series]:
    X = df[feature_cols].copy()
    y = df[target_col].astype(int)
    # XGBoost handles NaNs natively via default direction learning.
    return X, y


# ---------------------------------------------------------------------------
# Trainers
# ---------------------------------------------------------------------------
@dataclass
class TrainedModel:
    booster: xgb.XGBClassifier
    feature_cols: List[str]
    metadata: Dict
    # Optional probability calibrator fit on held-out Stage 2 predictions.
    # `None` means "no calibration applied" — predictions pass through raw.
    # Typed as object because we support both the current `_PlattCalibrator`
    # (sigmoid / logistic) and legacy `IsotonicRegression` objects pickled in
    # older model files. Both expose `.predict(raw_probs) -> calibrated_probs`,
    # which is all `_apply_calibration` needs.
    calibrator: Optional[object] = None


def _apply_calibration(stage: TrainedModel, raw_probs: np.ndarray) -> np.ndarray:
    """Apply the model's calibrator if present. Identity otherwise.

    Isotonic can produce values at {0.0, 1.0} on extrapolation; we clip to
    [eps, 1-eps] so downstream Kelly/EV math doesn't divide by zero.
    """
    if stage.calibrator is None:
        return raw_probs
    calibrated = stage.calibrator.predict(raw_probs)
    eps = 1e-4
    return np.clip(calibrated, eps, 1.0 - eps)


class _PlattCalibrator:
    """Platt (sigmoid) calibration: fit logistic regression on raw model probs.

    Replaces isotonic regression, which was collapsing predictions to a handful
    of discrete bin values when the held-out validation set was small or had
    narrow prob spread. Platt scaling is a smooth 2-parameter logistic — it
    cannot collapse to discrete steps, so every incoming raw prob maps to a
    unique calibrated prob.

    Exposes `.predict(raw_probs)` to match the IsotonicRegression API, so
    `_apply_calibration` doesn't need to special-case on type.
    """

    def __init__(self) -> None:
        # Near-zero regularization — we want the logistic fit driven by the
        # data, not shrunk toward the prior. n=hundreds to low-thousands of
        # held-out games is plenty for a 2-parameter fit.
        self._lr = LogisticRegression(C=1e6, solver="lbfgs")

    def fit(self, raw_probs: np.ndarray, y_true: np.ndarray) -> "_PlattCalibrator":
        x = np.asarray(raw_probs, dtype=float).reshape(-1, 1)
        y = np.asarray(y_true, dtype=int)
        self._lr.fit(x, y)
        return self

    def predict(self, raw_probs: np.ndarray) -> np.ndarray:
        x = np.asarray(raw_probs, dtype=float).reshape(-1, 1)
        return self._lr.predict_proba(x)[:, 1]


def _fit_calibrator(raw_probs: np.ndarray,
                    y_true: np.ndarray) -> Optional[_PlattCalibrator]:
    """Fit a Platt (sigmoid) calibrator. Returns None if the inputs are too
    thin or degenerate (all-one-class, all-identical probs)."""
    if len(raw_probs) < CALIBRATION_MIN_ROWS:
        return None
    y_arr = np.asarray(y_true)
    if len(np.unique(y_arr)) < 2:
        return None   # all wins or all losses -> nothing to calibrate against
    raw_arr = np.asarray(raw_probs, dtype=float)
    if np.unique(raw_arr).size < 2:
        return None   # constant input -> logistic fit degenerate
    try:
        return _PlattCalibrator().fit(raw_arr, y_arr)
    except Exception as e:
        log.warning("Calibration fit failed: %s", e)
        return None


def train_stage1_f5(train: pd.DataFrame,
                    valid: Optional[pd.DataFrame] = None) -> TrainedModel:
    """
    Fit the F5 model. Target column must be `home_f5_win` (home team leads
    after 5 innings, including ties broken in favor of home or dropped).
    """
    feats = [c for c in F5_FEATURES if c in train.columns]
    if len(feats) < 3:
        raise ValueError(f"Too few SP features available; have {feats}")

    params = {**XGB_PARAMS_F5, "monotone_constraints": _monotone_tuple(feats, F5_MONOTONE)}

    X_tr, y_tr = _xy_split(train, feats, "home_f5_win")
    eval_set = None
    if valid is not None and not valid.empty:
        X_va, y_va = _xy_split(valid, feats, "home_f5_win")
        eval_set = [(X_va, y_va)]
        # Early stopping only makes sense with an eval_set; otherwise XGBoost
        # raises in 3.x.
        params["early_stopping_rounds"] = EARLY_STOPPING_ROUNDS

    model = xgb.XGBClassifier(**params)
    model.fit(X_tr, y_tr, eval_set=eval_set, verbose=False)
    meta = {
        "n_train": len(X_tr),
        "n_valid": 0 if valid is None else len(valid),
        "train_auc": roc_auc_score(y_tr, model.predict_proba(X_tr)[:, 1]),
    }
    if eval_set:
        meta["valid_auc"] = roc_auc_score(y_va, model.predict_proba(X_va)[:, 1])
        meta["valid_logloss"] = log_loss(y_va, model.predict_proba(X_va)[:, 1])
    return TrainedModel(model, feats, meta)


def _oof_f5_predictions(train: pd.DataFrame, n_inner_splits: int = 4) -> np.ndarray:
    """
    Generate out-of-fold Stage 1 predictions for the training frame.

    The inner split is time-series so the Stage 1 used to score row i has only
    seen games strictly earlier than i. This avoids the in-sample leakage that
    arises from using a Stage 1 fitted on all `train` rows to score those same
    rows before Stage 2 training — which lets Stage 2 lean on a feature that
    is artificially well-calibrated on its own training targets.

    Rows that fall in the first inner-fold training window (never seen as a
    validation row) get NaN and should be dropped by the caller before fit.
    """
    train_sorted = train.sort_values("game_date").reset_index(drop=True)
    feats = [c for c in F5_FEATURES if c in train_sorted.columns]
    oof = np.full(len(train_sorted), np.nan)
    tscv = TimeSeriesSplit(n_splits=n_inner_splits)
    for tr_idx, va_idx in tscv.split(train_sorted):
        inner = train_stage1_f5(train_sorted.iloc[tr_idx])
        oof[va_idx] = inner.booster.predict_proba(
            train_sorted.iloc[va_idx][feats].values
        )[:, 1]
    train_sorted["f5_model_output"] = oof
    return train_sorted


def _oof_stage2_predictions(train_oof: pd.DataFrame,
                            feats: List[str],
                            params: dict,
                            n_inner_splits: int = 4
                            ) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate out-of-fold Stage 2 predictions for calibrator fitting.

    Mirrors `_oof_f5_predictions`: inner TimeSeriesSplit, each row's raw
    Stage 2 score comes from a booster that has NOT seen the row. The
    union of these OOF predictions (typically ~all-rows-minus-first-fold)
    is used to fit the Platt calibrator.

    Rationale: the previous "carve out last 15% of train_oof" approach fit
    the calibrator on IN-SAMPLE Stage 2 predictions (the main booster had
    trained on those same rows). Platt then learned to amplify in-sample
    confidence, producing a slope that over-sharpens at inference time.
    Empirically, the old fit had slope ≈ 13.2 and underperformed raw by
    +0.005 Brier / +0.012 log loss when re-validated on honestly OOF games.

    By using an inner-CV OOF fit instead, every calibration sample is a
    true out-of-fold prediction and the calibrator's slope reflects the
    real gap between raw confidence and empirical win rate.

    Returns (oof_probs, y_true) — only for rows that actually received an
    OOF prediction. Rows in the earliest inner-fold training window never
    act as validation so they have NaN and are dropped here.
    """
    train_sorted = train_oof.sort_values("game_date").reset_index(drop=True)
    X = train_sorted[feats].copy()
    y = train_sorted["home_win"].astype(int).to_numpy()
    oof = np.full(len(train_sorted), np.nan)
    tscv = TimeSeriesSplit(n_splits=n_inner_splits)
    for tr_idx, va_idx in tscv.split(train_sorted):
        inner = xgb.XGBClassifier(**params)
        inner.fit(X.iloc[tr_idx], y[tr_idx], verbose=False)
        oof[va_idx] = inner.predict_proba(X.iloc[va_idx].values)[:, 1]
    mask = ~np.isnan(oof)
    return oof[mask], y[mask]


def train_stage2_full(train: pd.DataFrame,
                      stage1: TrainedModel,
                      valid: Optional[pd.DataFrame] = None) -> TrainedModel:
    """
    Fit the full-game model.

    For training: `f5_model_output` is computed via an inner TimeSeriesSplit
    so each row's Stage 1 score comes from a model that has NOT seen that row.
    Using the in-sample Stage 1 predictions here was the old path and leaks —
    Stage 2's dominant feature ends up artificially calibrated on its own
    training labels, inflating fit quality and over-concentrating bets.

    For validation + inference: the main `stage1` model is used directly,
    which matches what `predict()` will do at live-scoring time.

    Calibration: when ENABLE_STAGE2_CALIBRATION is set, a Platt (sigmoid)
    calibrator is fit on held-out Stage 2 predictions:
      - Walk-forward case (`valid` provided): fit on the fold's valid slice,
        which is honestly future-dated relative to the training data.
      - Final-training case (no `valid`): fit on OOF Stage 2 predictions
        generated via inner TimeSeriesSplit across the WHOLE training set.
        The previous "last 15% chronological slice" approach leaked because
        the main Stage 2 booster had already trained on those rows, so the
        calibrator saw in-sample (over-confident) predictions and learned a
        too-steep slope that failed on real OOF games.

    The calibrator is stored on the returned TrainedModel and applied by
    `predict()` at inference time.
    """
    train_oof = _oof_f5_predictions(train)
    # Drop rows from the earliest inner-fold where no OOF score exists.
    train_oof = train_oof.dropna(subset=["f5_model_output"]).reset_index(drop=True)

    if valid is not None and not valid.empty:
        valid = valid.copy()
        valid["f5_model_output"] = stage1.booster.predict_proba(
            valid[stage1.feature_cols].values
        )[:, 1]

    feats = [c for c in FULL_FEATURES_EXTRA if c in train_oof.columns]

    # Belt-and-suspenders leakage guard (added 2026-05-02 after the
    # `home_f5_win` cache leak was traced). For each feature, compute
    # Pearson correlation with `home_win` on the train set; raise if any
    # exceeds the threshold. Catches the bug class where someone
    # accidentally lists a target-equivalent column in
    # FULL_FEATURES_EXTRA. See leakage_scrub_2026-05-02.md.
    LEAK_CORR_THRESHOLD = 0.40
    y_corr = train_oof["home_win"].astype(float)
    leakage_hits = []
    for c in feats:
        s = pd.to_numeric(train_oof[c], errors="coerce")
        v = s.notna() & y_corr.notna()
        if v.sum() < 100:
            continue
        rho = float(s[v].corr(y_corr[v]))
        if pd.notna(rho) and abs(rho) >= LEAK_CORR_THRESHOLD:
            leakage_hits.append((c, rho))
    if leakage_hits:
        msg = ("Leakage guard tripped in train_stage2_full: "
               + ", ".join(f"{c} (rho={r:+.3f})" for c, r in leakage_hits)
               + f" exceed |rho|>={LEAK_CORR_THRESHOLD}. Remove from "
               "FULL_FEATURES_EXTRA — likely target-equivalent column.")
        raise ValueError(msg)
    log.info("[stage2] leakage guard PASSED (|rho|<%.2f for all %d features)",
             LEAK_CORR_THRESHOLD, len(feats))

    params = {**XGB_PARAMS_FULL, "monotone_constraints": _monotone_tuple(feats, FULL_MONOTONE)}

    X_tr, y_tr = _xy_split(train_oof, feats, "home_win")
    eval_set = None
    if valid is not None and not valid.empty:
        X_va, y_va = _xy_split(valid, feats, "home_win")
        eval_set = [(X_va, y_va)]
        params["early_stopping_rounds"] = EARLY_STOPPING_ROUNDS

    model = xgb.XGBClassifier(**params)
    model.fit(X_tr, y_tr, eval_set=eval_set, verbose=False)
    meta = {
        "n_train": len(X_tr),
        "n_valid": 0 if valid is None else len(valid),
        "train_auc": roc_auc_score(y_tr, model.predict_proba(X_tr)[:, 1]),
    }
    if eval_set:
        meta["valid_auc"] = roc_auc_score(y_va, model.predict_proba(X_va)[:, 1])
        meta["valid_brier"] = brier_score_loss(y_va, model.predict_proba(X_va)[:, 1])
        meta["valid_logloss"] = log_loss(y_va, model.predict_proba(X_va)[:, 1])

    # Calibration fit ---------------------------------------------------
    calibrator: Optional[_PlattCalibrator] = None
    if ENABLE_STAGE2_CALIBRATION:
        # Capture the (raw_probs, y_true) pair we fit the calibrator on,
        # so we can compute telemetry Brier at the end.
        cal_raw: Optional[np.ndarray] = None
        cal_y:   Optional[np.ndarray] = None
        if eval_set is not None:
            # Walk-forward case: use the fold's valid slice, which is
            # honestly future-dated relative to train.
            cal_raw = model.predict_proba(X_va.values)[:, 1]
            cal_y   = y_va.values
            calibrator = _fit_calibrator(cal_raw, cal_y)
            cal_source = "valid"
            cal_n = len(cal_raw)
        else:
            # Final-training case: OOF Stage 2 predictions via inner
            # TimeSeriesSplit across the whole training set. Each sample
            # is a genuine out-of-fold prediction, so the calibrator's
            # slope reflects the real gap between raw confidence and
            # empirical win rate — not the in-sample-over-fit artifact
            # produced by the old last-15% slice method.
            #
            # Inner-CV boosters can't use early stopping (no inner valid
            # set of their own), so strip that param before passing in.
            inner_params = {k: v for k, v in params.items()
                            if k != "early_stopping_rounds"}
            cal_raw, cal_y = _oof_stage2_predictions(
                train_oof, feats, inner_params)
            if len(cal_raw) >= CALIBRATION_MIN_ROWS:
                calibrator = _fit_calibrator(cal_raw, cal_y)
                cal_source = "oof_inner_cv"
                cal_n = len(cal_raw)
            else:
                cal_source = "skipped_thin"
                cal_n = 0

        if calibrator is not None:
            meta["calibration"] = {"source": cal_source, "n": cal_n,
                                    "fitted": True}
            # Raw vs calibrated Brier on the calibration sample itself.
            try:
                cal_probs_ref = calibrator.predict(cal_raw)
                meta["calibration"]["raw_brier"] = float(
                    brier_score_loss(cal_y, cal_raw))
                meta["calibration"]["cal_brier"] = float(
                    brier_score_loss(cal_y, cal_probs_ref))
                # Surface the fitted slope so we notice if it ever drifts
                # back to the over-fit regime (slope >> 6).
                meta["calibration"]["slope"] = float(
                    calibrator._lr.coef_[0][0])
                meta["calibration"]["intercept"] = float(
                    calibrator._lr.intercept_[0])
            except Exception:
                pass
        else:
            meta["calibration"] = {"source": cal_source, "n": cal_n,
                                    "fitted": False}

    return TrainedModel(model, feats, meta, calibrator=calibrator)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def _apply_f5_override(out: pd.DataFrame) -> pd.DataFrame:
    """Apply the Stage-1-wins-on-disagreement rule.

    Overwrites `model_prob` with `f5_prob` for games where Stage 1 and Stage 2
    pick different sides, UNLESS the park is environmentally extreme enough
    (|park_runs_factor − 1| > ENVIRONMENTAL_PARK_THRESHOLD) to justify Stage 2's
    flip. Adds two diagnostic columns so consumers can audit the behavior:
      - `f5_override_applied` (bool): True when we swapped Stage 2 → Stage 1.
      - `model_prob_stage2` (float): original Stage 2 prob, preserved.
    """
    if not F5_OVERRIDE_ON_DISAGREEMENT:
        out["f5_override_applied"] = False
        out["model_prob_stage2"] = out["model_prob"]
        return out

    f5 = out["f5_prob"]
    m2 = out["model_prob"]
    park = out.get("park_runs_factor")
    # Missing park factor → treat as neutral (1.0) so the env exemption does
    # not accidentally kick in on games where the feature failed to load.
    park_vals = park.fillna(1.0) if park is not None else pd.Series(1.0, index=out.index)

    # Disagreement: f5 and stage2 on opposite sides of 0.5.
    disagree = (f5 >= 0.5) != (m2 >= 0.5)
    # Environmental exemption: park extreme enough to let Stage 2 through.
    env_exempt = (park_vals - 1.0).abs() > ENVIRONMENTAL_PARK_THRESHOLD
    override_mask = disagree & ~env_exempt

    out["model_prob_stage2"] = m2.copy()
    out["f5_override_applied"] = override_mask
    out.loc[override_mask, "model_prob"] = f5.loc[override_mask].values
    return out


def predict(stage1: TrainedModel, stage2: TrainedModel,
            games: pd.DataFrame) -> pd.DataFrame:
    """Return home-team win probabilities (+ F5 probability) for a slate.

    Stage 2's raw output is passed through its calibrator (if fit) so the
    `model_prob` column is in the same probability space as the training
    targets. We also expose `model_prob_raw` for diagnostics — useful when
    comparing calibration ROI uplift in backtests.

    After calibration, the F5-override rule is applied: when Stage 1 and
    Stage 2 disagree on the pick side, `model_prob` is replaced with `f5_prob`
    unless the park is environmentally extreme. See config.F5_OVERRIDE_ON_DISAGREEMENT
    for the full rationale.
    """
    out = games.copy()
    # Stage 1 is intentionally NOT calibrated — Stage 2 consumes it as a
    # feature and a ranking-only signal is what it wants.
    out["f5_prob"] = stage1.booster.predict_proba(
        games[stage1.feature_cols].values
    )[:, 1]
    enriched = out.copy()
    enriched["f5_model_output"] = out["f5_prob"]
    raw = stage2.booster.predict_proba(
        enriched[stage2.feature_cols].values
    )[:, 1]
    out["model_prob_raw"] = raw
    out["model_prob"] = _apply_calibration(stage2, raw)
    out = _apply_f5_override(out)
    return out


# ---------------------------------------------------------------------------
# Time-series CV wrapper
# ---------------------------------------------------------------------------
def time_series_cv(df: pd.DataFrame, n_splits: int = 5) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Split by `game_date` so the validation set is always future-dated relative
    to training. Essential for a sports model: random CV leaks information.
    """
    df = df.sort_values("game_date").reset_index(drop=True)
    tscv = TimeSeriesSplit(n_splits=n_splits)
    out = []
    for tr_idx, va_idx in tscv.split(df):
        out.append((df.iloc[tr_idx].reset_index(drop=True),
                    df.iloc[va_idx].reset_index(drop=True)))
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save(stage1: TrainedModel, stage2: TrainedModel, path: str,
         label: Optional[str] = None,
         metrics: Optional[Dict[str, Any]] = None,
         skip_archive: bool = False) -> None:
    """Persist a trained two-stage bundle.

    Auto-archives the file currently at ``path`` (typically ``models/latest.pkl``)
    into ``models/archive/`` before overwriting, and registers BOTH the
    displaced version and the new one in ``models/registry.json``.

    Pass ``skip_archive=True`` to bypass the registry write — useful only
    for unit tests or when the caller explicitly wants no audit trail.

    Optional ``label`` and ``metrics`` are stored on the new version's
    manifest entry so list/compare CLIs can surface them.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not skip_archive:
        try:
            from . import model_registry
            # Archive the displaced version first (no-op if `path` doesn't
            # exist yet — first-time save).
            model_registry.archive_existing(
                path, label="auto-archived",
                notes="displaced by model.save",
            )
        except Exception as e:
            # Never let registry bookkeeping block a successful train.
            log = logging.getLogger(__name__)
            log.warning("model_registry archive skipped: %s", e)
    joblib.dump({"stage1": stage1, "stage2": stage2}, path)
    if not skip_archive:
        try:
            from . import model_registry
            model_registry.register_active(path, label=label, metrics=metrics)
        except Exception as e:
            log = logging.getLogger(__name__)
            log.warning("model_registry register skipped: %s", e)


def load(path: str) -> Tuple[TrainedModel, TrainedModel]:
    bundle = joblib.load(path)
    return bundle["stage1"], bundle["stage2"]


# ---------------------------------------------------------------------------
# Feature importance helpers
# ---------------------------------------------------------------------------
def importance_table(model: TrainedModel, kind: str = "gain") -> pd.DataFrame:
    """Pretty feature-importance table ordered descending."""
    raw = model.booster.get_booster().get_score(importance_type=kind)
    rows = [{"feature": f, "importance": raw.get(f, 0.0)} for f in model.feature_cols]
    return pd.DataFrame(rows).sort_values("importance", ascending=False).reset_index(drop=True)
