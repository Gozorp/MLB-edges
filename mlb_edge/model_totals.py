"""
model_totals.py
---------------
Regression model for predicting total runs scored in an MLB game.

Two-stage architecture mirroring the moneyline version but for runs:

  Stage 1 (F5 runs):  Predicts total runs in innings 1-5 from SP features.
  Stage 2 (Full-game runs): Predicts total runs in innings 1-9 using Stage 1
                             output + bullpen + context.

Why regression instead of classification:
  Totals markets ask "over/under X runs?" where X varies by game. The binary
  answer depends on both our prediction AND the line, which the market sets.
  Predicting the continuous run total lets us compute an "edge" against any
  line the book posts (Over if predicted > line, Under if predicted < line).

Why two stages (same as moneyline):
  Pitcher quality dominates F5 run prevention. Bullpen and late-game hitting
  dominate runs 6-9. Isolating the SP signal in Stage 1 prevents the full-game
  model from washing it out with correlated offensive stats.

Targets:
  home_f5_runs = home_f5_score + away_f5_score   (Stage 1 target)
  total_runs   = home_score + away_score         (Stage 2 target)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from .config import XGB_PARAMS_F5, XGB_PARAMS_FULL
from .model import F5_FEATURES, FULL_FEATURES_EXTRA, time_series_cv

log = logging.getLogger(__name__)


# Regression hyperparameters — start from the classification ones but switch
# objective. Regression typically benefits from slightly lower learning rate
# and more rounds since run counts have more entropy than win/loss flags.
XGB_PARAMS_TOTALS_F5 = {
    **XGB_PARAMS_F5,
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "learning_rate": 0.03,
    "n_estimators": 800,
}

XGB_PARAMS_TOTALS_FULL = {
    **XGB_PARAMS_FULL,
    "objective": "reg:squarederror",
    "eval_metric": "rmse",
    "learning_rate": 0.025,
    "n_estimators": 700,
}


@dataclass
class TrainedTotalsModel:
    booster: xgb.XGBRegressor
    feature_cols: List[str]
    metadata: Dict


# ---------------------------------------------------------------------------
# Stage 1 — F5 total runs
# ---------------------------------------------------------------------------
def train_stage1_totals(train: pd.DataFrame) -> TrainedTotalsModel:
    """Target: f5_total_runs (home_f5_score + away_f5_score)."""
    feats = [c for c in F5_FEATURES if c in train.columns]
    if len(feats) < 3:
        raise ValueError(f"Too few SP features; have {feats}")

    # Build target if not present
    if "f5_total_runs" not in train.columns:
        if ("home_f5_score" not in train.columns
                or "away_f5_score" not in train.columns):
            raise ValueError("train missing home_f5_score/away_f5_score — "
                             "feature cache may be stale; rebuild")
        train = train.copy()
        train["f5_total_runs"] = (train["home_f5_score"].astype(float)
                                  + train["away_f5_score"].astype(float))

    X = train[feats].copy()
    y = train["f5_total_runs"].astype(float)

    # Drop rows with missing target (rain-shortened, etc.)
    valid = y.notna()
    X, y = X[valid], y[valid]

    model = xgb.XGBRegressor(**XGB_PARAMS_TOTALS_F5)
    model.fit(X, y, verbose=False)

    pred = model.predict(X)
    meta = {
        "n_train":     len(X),
        "train_mae":   float(mean_absolute_error(y, pred)),
        "train_rmse":  float(np.sqrt(mean_squared_error(y, pred))),
        "target_mean": float(y.mean()),
        "target_std":  float(y.std()),
    }
    return TrainedTotalsModel(model, feats, meta)


# ---------------------------------------------------------------------------
# Stage 2 — Full game total runs
# ---------------------------------------------------------------------------
def _oof_stage1_totals(train: pd.DataFrame, n_inner: int = 4) -> pd.DataFrame:
    """Out-of-fold F5 predictions for Stage 2 training (leak prevention)."""
    tr = train.sort_values("game_date").reset_index(drop=True).copy()
    if "f5_total_runs" not in tr.columns:
        tr["f5_total_runs"] = (tr["home_f5_score"].astype(float)
                               + tr["away_f5_score"].astype(float))
    feats = [c for c in F5_FEATURES if c in tr.columns]
    oof = np.full(len(tr), np.nan)
    tscv = TimeSeriesSplit(n_splits=n_inner)
    for tr_idx, va_idx in tscv.split(tr):
        inner = train_stage1_totals(tr.iloc[tr_idx])
        oof[va_idx] = inner.booster.predict(tr.iloc[va_idx][feats].values)
    tr["f5_runs_pred"] = oof
    return tr


def train_stage2_totals(train: pd.DataFrame,
                        stage1: TrainedTotalsModel) -> TrainedTotalsModel:
    """Target: total_runs (home_score + away_score full game)."""
    tr_oof = _oof_stage1_totals(train)
    tr_oof = tr_oof.dropna(subset=["f5_runs_pred"]).reset_index(drop=True)

    # Build total_runs target
    if "total_runs" not in tr_oof.columns:
        tr_oof["total_runs"] = (tr_oof["home_score"].astype(float)
                                + tr_oof["away_score"].astype(float))

    # Stage 2 features: include Stage 1 output explicitly. We pull from
    # FULL_FEATURES_EXTRA but skip `f5_model_output` (that's the moneyline
    # Stage-1 hook; here the equivalent is `f5_runs_pred`).
    feats = ["f5_runs_pred"] + [
        c for c in FULL_FEATURES_EXTRA
        if c in tr_oof.columns and c != "f5_model_output"
    ]
    feats = list(dict.fromkeys(feats))  # dedupe, preserve order

    X = tr_oof[feats].copy()
    y = tr_oof["total_runs"].astype(float)

    valid = y.notna()
    X, y = X[valid], y[valid]

    model = xgb.XGBRegressor(**XGB_PARAMS_TOTALS_FULL)
    model.fit(X, y, verbose=False)

    pred = model.predict(X)
    meta = {
        "n_train":     len(X),
        "train_mae":   float(mean_absolute_error(y, pred)),
        "train_rmse":  float(np.sqrt(mean_squared_error(y, pred))),
        "target_mean": float(y.mean()),
        "target_std":  float(y.std()),
    }
    return TrainedTotalsModel(model, feats, meta)


# ---------------------------------------------------------------------------
# Walk-forward prediction
# ---------------------------------------------------------------------------
def walkforward_totals_predict(df: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    """
    Walk-forward fit both stages, return valid-fold predictions with columns:
      f5_runs_pred, total_runs_pred
    """
    # Ensure target columns exist
    df = df.copy()
    if "f5_total_runs" not in df.columns:
        df["f5_total_runs"] = (df["home_f5_score"].astype(float)
                               + df["away_f5_score"].astype(float))
    if "total_runs" not in df.columns:
        df["total_runs"] = (df["home_score"].astype(float)
                            + df["away_score"].astype(float))

    out = []
    for i, (tr, va) in enumerate(time_series_cv(df, n_splits=n_splits)):
        log.info("Totals Fold %d: train %d, valid %d", i, len(tr), len(va))
        try:
            m1 = train_stage1_totals(tr)
            m2 = train_stage2_totals(tr, m1)
        except Exception as e:
            log.error("Totals fold %d failed: %s", i, e)
            continue

        va_pred = va.copy()
        # Stage 1 prediction on valid fold
        m1_feats = [c for c in F5_FEATURES if c in va_pred.columns]
        va_pred["f5_runs_pred"] = m1.booster.predict(va_pred[m1_feats].values)
        # Stage 2 prediction
        va_pred["total_runs_pred"] = m2.booster.predict(
            va_pred[m2.feature_cols].values
        )
        va_pred["fold"] = i
        out.append(va_pred)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_totals(stage1: TrainedTotalsModel, stage2: TrainedTotalsModel,
                path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"stage1": stage1, "stage2": stage2}, path)


def load_totals(path: str) -> Tuple[TrainedTotalsModel, TrainedTotalsModel]:
    bundle = joblib.load(path)
    return bundle["stage1"], bundle["stage2"]
