"""
mlb_edge/calibration.py
-----------------------
Probability-calibration utilities for the Stage 2 booster.

Why a custom class instead of sklearn's IsotonicRegression / Platt?
    The model.py history (lines ~54-85) documents two prior calibration
    attempts that BOTH regressed ROI versus the raw booster:
      * Platt (sigmoid)     — over-corrected near the tails because the
                              OOF inner-CV fits saw less data than the
                              production booster, so their raw probs were
                              noisier and the Platt fit shifted the
                              decision threshold the wrong direction.
      * Isotonic (mono spl) — overfit on small samples in the extreme
                              deciles; produced flat-then-jagged steps
                              that didn't generalize.
    The verdict in the comments was that the booster's mis-calibration
    "is a zigzag, not a smooth sigmoid", and that the right approach is
    "binned isotonic with smoothing, or per-bin shrinkage."  This module
    implements that explicitly.

Algorithm
=========
Given a holdout of (raw_prob, y_true) pairs:

    1. Bin raw_prob into N equal-width bins on [0, 1].  Default N=10.
    2. For each bin, compute the empirical hit rate from the holdout:
           empirical_rate[i] = sum(y_true in bin_i) / count(bin_i)
       and apply a Beta(alpha, alpha) prior centered on the bin's
       midpoint, giving the Bayes-shrunk rate:
           shrunk_rate[i] = (k_i + alpha * mid_i) / (n_i + alpha)
       This handles small-sample bins gracefully — a bin with 2/3 hits
       on n=3 doesn't get treated as a 0.667 calibrated probability.
    3. Force monotonicity by running a single isotonic-regression pass
       on the (mid_i, shrunk_rate[i]) pairs weighted by n_i.  The
       calibrator output is a piecewise-linear function — we interpolate
       between the (now monotonic) per-bin calibrated rates at inference.

Public API
==========
    cal = BinnedIsotonicCalibrator(n_bins=10, prior_alpha=20.0)
    cal.fit(raw_probs, y_true)
    cal.predict(raw_probs) -> calibrated probabilities

Both `.fit` and `.predict` accept numpy arrays or pandas Series.

The class exposes a `.predict(...)` method so it slots directly into the
existing ``mlb_edge.model._apply_calibration`` shim — no changes needed
to consumers; just attach an instance to ``TrainedModel.calibrator``.

Diagnostics
===========
After fit, you can inspect:
    cal.bin_edges      : array of length n_bins+1
    cal.bin_centers    : array of length n_bins
    cal.bin_n          : counts per bin (holdout size)
    cal.bin_empirical  : raw empirical rates per bin (no shrinkage)
    cal.bin_shrunk     : shrunk rates (after Beta prior)
    cal.bin_calibrated : final monotonic rates (after isotonic pass)

These are what the eval tool reports as a reliability table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class BinnedIsotonicCalibrator:
    """Bayesian-shrunk binned-isotonic calibration.

    Designed to be saved into ``TrainedModel.calibrator`` and used via
    ``model._apply_calibration``.  Pickle-safe (pure numpy + dataclass).
    """
    n_bins: int = 10
    prior_alpha: float = 20.0
    eps: float = 1e-4

    # Set by .fit()
    bin_edges: np.ndarray       = field(default=None)   # type: ignore
    bin_centers: np.ndarray     = field(default=None)   # type: ignore
    bin_n: np.ndarray           = field(default=None)   # type: ignore
    bin_empirical: np.ndarray   = field(default=None)   # type: ignore
    bin_shrunk: np.ndarray      = field(default=None)   # type: ignore
    bin_calibrated: np.ndarray  = field(default=None)   # type: ignore
    fitted_n: int = 0

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------
    def fit(self, raw_probs, y_true) -> "BinnedIsotonicCalibrator":
        rp = np.asarray(raw_probs, dtype=float).flatten()
        yt = np.asarray(y_true, dtype=float).flatten()
        if rp.shape != yt.shape:
            raise ValueError(
                f"raw_probs / y_true shape mismatch: {rp.shape} vs {yt.shape}"
            )
        # drop NaN
        mask = np.isfinite(rp) & np.isfinite(yt)
        rp = rp[mask]; yt = yt[mask]
        if len(rp) < self.n_bins:
            raise ValueError(
                f"need at least n_bins={self.n_bins} samples to fit; got {len(rp)}"
            )
        self.fitted_n = int(len(rp))

        # 1. Bin raw probabilities.
        edges = np.linspace(0.0, 1.0, self.n_bins + 1)
        # np.digitize is right-exclusive by default; clip to [0, n_bins-1]
        bin_idx = np.clip(np.digitize(rp, edges[1:-1], right=False),
                          0, self.n_bins - 1)
        centers = (edges[:-1] + edges[1:]) / 2.0

        # 2. Empirical + Bayes-shrunk hit rates per bin.
        n_per     = np.zeros(self.n_bins, dtype=int)
        k_per     = np.zeros(self.n_bins, dtype=float)
        for b in range(self.n_bins):
            sel = (bin_idx == b)
            n_per[b] = int(sel.sum())
            k_per[b] = float(yt[sel].sum())
        # Avoid /0 — empty bins inherit the bin midpoint (no signal)
        with np.errstate(invalid="ignore", divide="ignore"):
            empirical = np.where(n_per > 0, k_per / np.maximum(n_per, 1),
                                 centers)
        with np.errstate(invalid="ignore", divide="ignore"):
            denom = n_per + self.prior_alpha
            shrunk = np.where(denom > 0,
                              (k_per + self.prior_alpha * centers) / np.maximum(denom, 1e-12),
                              centers)

        # 3. Monotonic isotonic pass — weighted by bin counts.
        calibrated = self._isotonic_pava(shrunk, np.maximum(n_per, 1).astype(float))

        # Store
        self.bin_edges      = edges
        self.bin_centers    = centers
        self.bin_n          = n_per
        self.bin_empirical  = empirical
        self.bin_shrunk     = shrunk
        self.bin_calibrated = calibrated
        return self

    # ------------------------------------------------------------------
    # Predict — piecewise-linear interpolation between bin centers
    # ------------------------------------------------------------------
    def predict(self, raw_probs) -> np.ndarray:
        if self.bin_calibrated is None:
            # Not fitted — return raw (identity).  Callers should never
            # rely on this; it's just safe.
            return np.asarray(raw_probs, dtype=float)
        rp = np.clip(np.asarray(raw_probs, dtype=float), 0.0, 1.0)
        # Linear interp between bin_centers; np.interp clamps to edge
        # values for out-of-range inputs (which is what we want — extreme
        # raw probs get the calibrated rate of the extremal bin).
        out = np.interp(rp, self.bin_centers, self.bin_calibrated)
        return np.clip(out, self.eps, 1.0 - self.eps)

    # ------------------------------------------------------------------
    # PAV — pool adjacent violators (weighted)
    # ------------------------------------------------------------------
    @staticmethod
    def _isotonic_pava(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Weighted PAV.  Returns a monotonically non-decreasing sequence
        that minimizes sum_i w_i * (out_i - values_i)^2."""
        v = np.asarray(values, dtype=float).copy()
        w = np.asarray(weights, dtype=float).copy()
        n = len(v)
        # Stack of (sum_value*weight, sum_weight, end_idx)
        out = list(zip(v * w, w, [1] * n))
        # Indices via list-of-blocks; merge whenever monotonicity violated
        i = 0
        while i + 1 < len(out):
            sw_i, w_i, n_i = out[i]
            sw_j, w_j, n_j = out[i+1]
            if (sw_i / w_i) > (sw_j / w_j):
                # merge i and i+1
                out[i] = (sw_i + sw_j, w_i + w_j, n_i + n_j)
                del out[i+1]
                i = max(i - 1, 0)
            else:
                i += 1
        # Expand back to per-bin values
        result = np.empty(n, dtype=float)
        idx = 0
        for sw, ww, count in out:
            mean = sw / ww
            result[idx:idx + count] = mean
            idx += count
        return result


# ----------------------------------------------------------------------
# Diagnostic helpers
# ----------------------------------------------------------------------
def reliability_table(raw_probs, y_true, n_bins: int = 10) -> dict:
    """Return a per-bin reliability summary suitable for printing /
    plotting.  Use on a holdout the calibrator hasn't been fit to."""
    cal_test = BinnedIsotonicCalibrator(n_bins=n_bins, prior_alpha=0.0)
    cal_test.fit(raw_probs, y_true)
    return {
        "edges":      cal_test.bin_edges.tolist(),
        "centers":    cal_test.bin_centers.tolist(),
        "n":          cal_test.bin_n.tolist(),
        "empirical":  cal_test.bin_empirical.tolist(),
        "predicted":  cal_test.bin_centers.tolist(),
    }


def brier_score(probs, y_true) -> float:
    p = np.asarray(probs, dtype=float).flatten()
    y = np.asarray(y_true, dtype=float).flatten()
    return float(np.mean((p - y) ** 2))


def log_loss(probs, y_true, eps: float = 1e-9) -> float:
    p = np.clip(np.asarray(probs, dtype=float).flatten(), eps, 1 - eps)
    y = np.asarray(y_true, dtype=float).flatten()
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
