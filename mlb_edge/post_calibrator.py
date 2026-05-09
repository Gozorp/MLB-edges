"""
mlb_edge/post_calibrator.py
---------------------------
Post-bake probability calibrator.  Reads a binned-isotonic table fitted from
historical (model_prob, win_outcome) pairs and remaps over/under-confident
probabilities back to their empirical hit rate.

Why post-bake instead of inside the booster:
    The XGBoost bundle is expensive to retrain and the historical sample
    available for calibration is small (low hundreds of games).  A binned
    remap fitted offline on those games can shrink overconfidence at the
    tails without touching the booster — and the table can be re-fit
    daily by the auto_weight_update cron as more results come in.

Calibration table format (models/calibration_v1.json):

    {
      "version": "v1",
      "n_samples": 126,
      "table": [
        {"bin_lo": 0.0, "bin_hi": 0.1, "bin_mid": 0.05,
         "n": 0, "calibrated_rate": 0.05},
        ...
      ]
    }

Public API
==========
    cal = PostCalibrator.load("models/calibration_v1.json")
    cal_prob = cal.calibrate(0.78)   # → 0.647 if model was overconfident at 78%
    cal.is_loaded                    # bool — False means pass-through

The calibrator is intentionally fail-open: if the JSON is missing or bad,
calibrate() returns the input unchanged.  Logged at DEBUG level so a
mis-deploy doesn't silently change the model's behavior.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


class PostCalibrator:
    def __init__(self, table: Optional[List[dict]] = None,
                 metadata: Optional[dict] = None) -> None:
        self.table = table or []
        self.metadata = metadata or {}
        self.is_loaded = bool(table)
        # Pre-extract sorted (mid, calibrated_rate) for fast lookup
        if self.is_loaded:
            sorted_t = sorted(self.table, key=lambda r: r["bin_mid"])
            self._mids = [r["bin_mid"] for r in sorted_t]
            self._rates = [r["calibrated_rate"] for r in sorted_t]
        else:
            self._mids = []
            self._rates = []

    @classmethod
    def load(cls, path: str | Path) -> "PostCalibrator":
        p = Path(path)
        if not p.exists():
            log.debug("post_calibrator: %s not found, using pass-through", p)
            return cls()
        try:
            with p.open() as f:
                data = json.load(f)
            table = data.get("table") or []
            if not table:
                log.warning("post_calibrator: %s has empty table", p)
                return cls()
            log.info("post_calibrator: loaded %s (n_samples=%d, version=%s)",
                     p, data.get("n_samples", "?"), data.get("version", "?"))
            return cls(table=table, metadata={k: v for k, v in data.items() if k != "table"})
        except Exception as e:
            log.warning("post_calibrator: failed to load %s: %s", p, e)
            return cls()

    def calibrate(self, p: float) -> float:
        """Return the calibrated probability for raw prob p.  Pass-through
        if the calibrator isn't loaded or p is outside [0, 1]."""
        if not self.is_loaded:
            return p
        try:
            p = float(p)
        except (TypeError, ValueError):
            return p
        if not (0.0 <= p <= 1.0):
            return p
        # Linear interpolation through bin midpoints
        mids, rates = self._mids, self._rates
        if p <= mids[0]:
            return rates[0]
        if p >= mids[-1]:
            return rates[-1]
        for i in range(len(mids) - 1):
            if mids[i] <= p <= mids[i + 1]:
                t = (p - mids[i]) / (mids[i + 1] - mids[i])
                return rates[i] + t * (rates[i + 1] - rates[i])
        return p


# Module-level singleton — load once on first import.
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "models" / "calibration_v1.json"
_singleton: Optional[PostCalibrator] = None


def get_default() -> PostCalibrator:
    global _singleton
    if _singleton is None:
        _singleton = PostCalibrator.load(_DEFAULT_PATH)
    return _singleton


def calibrate(p: float) -> float:
    """Convenience: apply the default calibrator to a raw probability."""
    return get_default().calibrate(p)
