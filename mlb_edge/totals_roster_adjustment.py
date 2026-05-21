"""
mlb_edge/totals_roster_adjustment.py
====================================
Heuristic post-model adjustment to pred_runs based on BvP + platoon
lineup-vs-SP signals.  Phase 1 of the "roster-adjusted totals" track.

Architecture: takes the existing two-stage XGBoost totals prediction
(`pred_runs`), computes a delta from per-side BvP aggregates, and emits
`pred_runs_bvp_adjusted = pred_runs + total_runs_delta` alongside the
original.

SHADOW MODE (2026-05-20 ship):
  The adjusted value is NOT used for production O/U decisions in this
  commit.  Both columns flow through to the dashboard and postgame
  cron.  After 7+ days of postgame data accumulates, compare RMSE of
  pred_runs_bvp_adjusted vs pred_runs against actual total runs; if
  adjusted is materially better (>=5% RMSE reduction), Phase 2 promotes
  it to the production O/U decision.

Why heuristic, not a learned model:
  The proper roster-adjusted totals model requires retraining XGBoost
  with point-in-time BvP features.  Historical BvP backfill (3 seasons
  x ~2400 games x 36 batter-pitcher pairs) is multi-day wall-clock at
  reasonable API rate limits, and the vsPlayer endpoint returns CAREER
  totals, not point-in-time-as-of-date, so the retrain needs play-by-
  play reconstruction.  That's tracked as Phase 2.  Tonight's heuristic
  ships the directional signal so we can validate the concept while
  the data infrastructure for the proper retrain is built.

Per Architecture-Session Pre-Flight Prompt v1.0:
  Rule 1  — probed: batter_vs_pitcher._aggregate_lineup_vs_sp produces
            the exact bvp_ops_shrunk + signal_strength fields needed
  Rule 6  — best-effort try/except per side, failure logs warning,
            zero delta returned (= no behavior change)
  Rule 9  — RUNS_PER_OPS_POINT is a starting guess [H], not a backtest
            output.  Magnitude derived from league-wide regression
            (100pp OPS ~ 1.2 R/G/team), damped by 0.4 because BvP-OPS
            is matchup-specific not season-wide.  Will be re-fit when
            postgame cron accumulates enough adjusted-vs-actual rows.
  Rule 10 — production deploy gate: 7+ days of postgame RMSE comparison
            before the adjusted prediction becomes the live O/U pick.
  Rule 11 — reverse-direction sanity: signal_strength weight returns 0
            when lineup has no historical PAs vs SP, so the cap can
            never make a confident-but-data-free adjustment.

Public API:
    compute_roster_adjustment(
        *, home_lineup_ids, away_lineup_ids,
        home_sp_id, away_sp_id, season_ops_lookup=None
    ) -> Dict[str, float]
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

log = logging.getLogger(__name__)


# Calibration constants — all [H] starting guesses per Rule 9.
LEAGUE_OPS = 0.720
RUNS_PER_OPS_POINT = 0.05  # R/G per 1pp OPS deviation from LEAGUE_OPS

# Signal-strength weight: how much to trust the BvP aggregate based on
# total observed PAs.  signal_strength = total_PA / 9 (PA per lineup
# spot).  Weight ramps from 0 at signal=0 to 1.0 at signal=5 (45+ PA/spot).
SIGNAL_STRENGTH_FULL_WEIGHT = 5.0


def _signal_weight(signal_strength: float) -> float:
    """Returns a [0, 1] weight reflecting confidence in the BvP signal.

    At signal_strength = 0 (lineup never faced this SP), returns 0 (no
    adjustment).  Ramps linearly to 1.0 at signal_strength = 5.
    """
    if signal_strength is None or signal_strength <= 0:
        return 0.0
    return min(1.0, float(signal_strength) / SIGNAL_STRENGTH_FULL_WEIGHT)


def _one_side_delta(
    lineup_ids: Iterable[int],
    sp_id: Optional[int],
    season_ops_lookup: Optional[Dict[int, float]],
) -> Dict[str, float]:
    """Compute the runs-delta contribution from one offense's lineup-vs-SP
    BvP aggregate.  Returns dict with keys: delta, n_pa, ops_shrunk, weight.
    """
    out = {"delta": 0.0, "n_pa": 0.0,
           "ops_shrunk": LEAGUE_OPS, "weight": 0.0}
    if not sp_id or not lineup_ids:
        return out
    try:
        from .batter_vs_pitcher import _aggregate_lineup_vs_sp
        agg = _aggregate_lineup_vs_sp(
            lineup_ids, int(sp_id), season_ops_lookup)
        weight = _signal_weight(agg.get("bvp_signal_strength", 0))
        ops_shrunk = float(agg.get("bvp_ops_shrunk", LEAGUE_OPS))
        delta = (ops_shrunk - LEAGUE_OPS) * 100 * RUNS_PER_OPS_POINT * weight
        out["delta"] = round(delta, 3)
        out["n_pa"] = float(agg.get("bvp_n_pa", 0))
        out["ops_shrunk"] = round(ops_shrunk, 4)
        out["weight"] = round(weight, 3)
    except Exception as e:
        log.warning("[totals_roster] one-side delta failed (sp=%s): %s",
                    sp_id, e)
    return out


def compute_roster_adjustment(
    *,
    home_lineup_ids: Iterable[int],
    away_lineup_ids: Iterable[int],
    home_sp_id: Optional[int],
    away_sp_id: Optional[int],
    season_ops_lookup: Optional[Dict[int, float]] = None,
) -> Dict[str, float]:
    """Compute delta to predicted runs based on lineup-aware BvP signals.

    Returns a dict with the supporting telemetry for the diag CSV:
      home_runs_delta, away_runs_delta, total_runs_delta,
      home_bvp_n_pa, away_bvp_n_pa,
      home_bvp_ops_shrunk, away_bvp_ops_shrunk,
      home_bvp_weight, away_bvp_weight.

    Failure modes:
      - Missing SP IDs (rookie/unannounced) -> deltas default to 0.
      - Empty lineup IDs -> deltas default to 0.
      - vsPlayer endpoint failure (rate limit, network) -> deltas default
        to 0, log.warning emitted per side.

    Best-effort per Rule 6; cap NEVER raises an exception out of this
    function so the totals pipeline can swallow the adjustment safely.
    """
    home = _one_side_delta(home_lineup_ids, away_sp_id, season_ops_lookup)
    away = _one_side_delta(away_lineup_ids, home_sp_id, season_ops_lookup)
    return {
        "home_runs_delta":      home["delta"],
        "away_runs_delta":      away["delta"],
        "total_runs_delta":     round(home["delta"] + away["delta"], 3),
        "home_bvp_n_pa":        home["n_pa"],
        "away_bvp_n_pa":        away["n_pa"],
        "home_bvp_ops_shrunk":  home["ops_shrunk"],
        "away_bvp_ops_shrunk":  away["ops_shrunk"],
        "home_bvp_weight":      home["weight"],
        "away_bvp_weight":      away["weight"],
    }


# ---------------------------------------------------------------------------
# CLI for ad-hoc verification
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 5:
        print("Usage: python -m mlb_edge.totals_roster_adjustment "
              "<home_sp_id> <home_lineup_csv_ids> <away_sp_id> <away_lineup_csv_ids>")
        print("Example: python -m mlb_edge.totals_roster_adjustment "
              "434378 592450,545361,605141 621121 660271,547989,571448")
        raise SystemExit(2)
    home_sp = int(sys.argv[1])
    home_ln = [int(x) for x in sys.argv[2].split(",") if x.strip()]
    away_sp = int(sys.argv[3])
    away_ln = [int(x) for x in sys.argv[4].split(",") if x.strip()]
    import json as _j
    print(_j.dumps(compute_roster_adjustment(
        home_lineup_ids=home_ln,
        away_lineup_ids=away_ln,
        home_sp_id=home_sp,
        away_sp_id=away_sp,
    ), indent=2))
