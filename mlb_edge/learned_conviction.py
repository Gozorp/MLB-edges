"""
mlb_edge/learned_conviction.py
------------------------------
Learned stake-multiplier replacing the discrete TIER_SIZES dict.

Why
===
The current heuristic in edge_calculator.score_conviction maps the count
of fired signals to one of four tiers (DIAMOND/PLATINUM/GOLD/SKIP), and
TIER_SIZES turns each tier into a fixed Kelly multiplier:

    DIAMOND -> 1.00x  (3+ signals fired)
    PLATINUM -> 0.30x (2 signals fired)
    GOLD    -> 0.00x  (1 signal fired)
    SKIP    -> 0.00x  (0 signals fired)

But across 496 historical bets (bt_2023.csv + bt_2024.csv + bt_2025.csv):
    DIAMOND   47/101 = 46.5% hit rate
    PLATINUM 184/395 = 46.6% hit rate
The 3.3x stake gap is staking on noise.  The signal-count categorical
loses the information in (a) which signals fired (F1 vs F3), (b) HOW
HARD they fired (xera_gap of 0.5 vs 4.0), and (c) the booster's own
probability for the pick.

This module fits a logistic regression that takes the actual continuous
features and outputs P(bet wins).  The stake multiplier is derived from
that probability via Kelly:

    kelly = max(0, (p_win * decimal - 1) / (decimal - 1))

clipped to [0, 1] (full-Kelly cap), then optionally fractional-Kelly'd
by ``KELLY_FRACTION``.

Pickle-safe (sklearn LogisticRegression + numpy).  Saved alongside the
booster registry under ``models/conviction.pkl`` so it has a separate
versioning lifecycle (the booster, the calibrator, and the conviction
model can each be rolled back independently).

Public API
==========
    cm = LearnedConvictionModel()
    cm.fit(rows)                 # rows = list of dicts with feature keys
    cm.predict_win_prob(row)     # -> float in [0, 1]
    cm.predict_stake_multiplier(row, decimal_odds, kelly_fraction)

Feature extraction
==================
Each input row needs to expose:
    model_prob       float   booster's probability for the picked side
    fair_prob        float   market-fair probability for the picked side
    decimal          float   decimal odds for the picked side
    signals          str     comma-sep list of "F<N>_<feature>=<value>"
                             (e.g. "F1_xera_gap=3.18, F3_swing_take_gap=2071.1")
The signals string is parsed in `_extract_features` to extract the
magnitude of each F1/F2/F3/F5 signal that fired, defaulting to 0 when
not present.
"""
from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)


# Feature names — order matters; the LR coefficients align to this list.
# Sweep across (n_features, L2) combinations on bt_2023+2024 train /
# bt_2025 holdout determined the f1_magnitude + edge_pp pair as the only
# subset that beats the per-tier heuristic baseline.  Adding model_prob,
# n_signals, F2/F3/F5 magnitudes, or abs_edge made the test log-loss
# worse — likely because (a) bt_*.csv only contains rows that already
# passed the model_prob band filter, so there's no variance in
# model_prob to learn from, and (b) signal-count is empirically
# uninformative (coefficient ~0.003 even with no regularization).
FEATURES = [
    "edge_pp",             # (model_prob - fair_prob) * 100  — primary edge signal
    "f1_magnitude",        # xera_gap value if F1 fired, else 0  — only signal whose magnitude predicts hit rate
]

_F_PATTERN = re.compile(r"F(\d)_([a-z_]+)=(-?\d+(?:\.\d+)?)\*?")


def _extract_features(row: dict) -> np.ndarray:
    """Convert a (signals string + numeric columns) row into a feature
    vector aligned with FEATURES."""
    sigs_text = str(row.get("signals", "") or "")
    by_family: Dict[str, float] = {}
    soft_f1 = 0
    for m in _F_PATTERN.finditer(sigs_text):
        fam = f"F{m.group(1)}"
        val = float(m.group(3))
        if fam == "F1" and m.group(0).endswith("*"):
            soft_f1 = 1
        # If the same family fires twice in a row (rare), keep max magnitude
        by_family[fam] = max(abs(val), by_family.get(fam, 0.0))

    model_prob = float(row.get("prob", row.get("model_prob", 0.5)) or 0.5)
    fair_prob  = float(row.get("fair", row.get("fair_prob", 0.5)) or 0.5)
    edge_pp    = (model_prob - fair_prob) * 100.0

    return np.array([
        edge_pp,
        by_family.get("F1", 0.0),
    ], dtype=float)


# ---------------------------------------------------------------------------
# Model — thin wrapper around sklearn's LogisticRegression with L2
# ---------------------------------------------------------------------------
@dataclass
class LearnedConvictionModel:
    """Logistic regression: P(bet wins) ~ continuous conviction features."""
    coef_: Optional[np.ndarray] = None         # shape (n_features,)
    intercept_: float = 0.0
    feature_means_: Optional[np.ndarray] = None
    feature_stds_: Optional[np.ndarray] = None
    n_train: int = 0
    train_log_loss: float = float("nan")
    train_accuracy: float = float("nan")
    test_log_loss: Optional[float] = None
    test_accuracy: Optional[float] = None
    feature_names: List[str] = field(default_factory=lambda: list(FEATURES))

    # ----- Fit -----
    def fit(self, rows: List[dict],
            l2_strength: float = 1.0,
            test_rows: Optional[List[dict]] = None) -> "LearnedConvictionModel":
        from sklearn.linear_model import LogisticRegression

        X = np.array([_extract_features(r) for r in rows], dtype=float)
        y = np.array([1 if str(r.get("won", "")).lower() in ("true","1","won") else 0
                      for r in rows], dtype=int)
        if len(X) < 30:
            raise ValueError(f"need at least 30 training rows; got {len(X)}")

        # Standardize for stable LR fit
        mu = X.mean(axis=0)
        sd = X.std(axis=0); sd = np.where(sd > 1e-9, sd, 1.0)
        Xs = (X - mu) / sd

        clf = LogisticRegression(C=1.0/l2_strength, max_iter=200, solver="lbfgs")
        clf.fit(Xs, y)

        self.coef_ = clf.coef_.flatten()
        self.intercept_ = float(clf.intercept_[0])
        self.feature_means_ = mu
        self.feature_stds_  = sd
        self.n_train = int(len(X))

        from .calibration import log_loss as _ll, brier_score as _bs
        train_p = self._predict_internal(Xs)
        self.train_log_loss = _ll(train_p, y)
        self.train_accuracy = float(((train_p >= 0.5) == y).mean())

        if test_rows:
            Xt = np.array([_extract_features(r) for r in test_rows], dtype=float)
            yt = np.array([1 if str(r.get("won", "")).lower() in ("true","1","won") else 0
                           for r in test_rows], dtype=int)
            Xts = (Xt - mu) / sd
            tp = self._predict_internal(Xts)
            self.test_log_loss = _ll(tp, yt)
            self.test_accuracy = float(((tp >= 0.5) == yt).mean())

        return self

    def _predict_internal(self, X_standardized: np.ndarray) -> np.ndarray:
        z = X_standardized @ self.coef_ + self.intercept_
        return 1.0 / (1.0 + np.exp(-z))

    # ----- Predict -----
    def predict_win_prob(self, row: dict) -> float:
        if self.coef_ is None:
            raise RuntimeError("model not fitted")
        x = _extract_features(row)
        xs = (x - self.feature_means_) / self.feature_stds_
        return float(self._predict_internal(xs[None, :])[0])

    def predict_stake_multiplier(self, row: dict, decimal_odds: float,
                                  kelly_fraction: float = 1.0,
                                  cap: float = 1.0) -> float:
        """Return a Kelly-style stake fraction in [0, cap].

        Uses the learned win probability instead of the model's raw
        probability — closes the loop between (a) what the booster says
        is going to win and (b) what we've EMPIRICALLY seen from those
        signal patterns.
        """
        p = self.predict_win_prob(row)
        if decimal_odds <= 1.0:
            return 0.0
        kelly = (p * decimal_odds - 1.0) / (decimal_odds - 1.0)
        kelly = max(0.0, min(cap, kelly))
        return kelly * kelly_fraction

    # ----- Persistence -----
    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "coef": self.coef_.tolist() if self.coef_ is not None else None,
            "intercept": self.intercept_,
            "feature_means": self.feature_means_.tolist() if self.feature_means_ is not None else None,
            "feature_stds":  self.feature_stds_.tolist()  if self.feature_stds_ is not None else None,
            "feature_names": self.feature_names,
            "n_train": self.n_train,
            "train_log_loss": self.train_log_loss,
            "train_accuracy": self.train_accuracy,
            "test_log_loss":  self.test_log_loss,
            "test_accuracy":  self.test_accuracy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LearnedConvictionModel":
        m = cls()
        m.coef_         = np.array(d["coef"]) if d.get("coef") else None
        m.intercept_    = float(d.get("intercept", 0.0))
        m.feature_means_= np.array(d["feature_means"]) if d.get("feature_means") else None
        m.feature_stds_ = np.array(d["feature_stds"])  if d.get("feature_stds")  else None
        m.feature_names = d.get("feature_names", list(FEATURES))
        m.n_train       = int(d.get("n_train", 0))
        m.train_log_loss= float(d.get("train_log_loss", float("nan")))
        m.train_accuracy= float(d.get("train_accuracy", float("nan")))
        m.test_log_loss = d.get("test_log_loss")
        m.test_accuracy = d.get("test_accuracy")
        return m

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LearnedConvictionModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Convenience: load the active conviction model if config flag enabled
# ---------------------------------------------------------------------------
ACTIVE_PATH = Path("models/conviction.json")


def get_active() -> Optional[LearnedConvictionModel]:
    if not ACTIVE_PATH.exists():
        return None
    try:
        return LearnedConvictionModel.load(ACTIVE_PATH)
    except Exception as e:
        log.warning("failed to load conviction model: %s", e)
        return None
