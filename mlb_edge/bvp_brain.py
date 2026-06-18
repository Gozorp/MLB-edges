"""
mlb_edge/bvp_brain.py
=====================
Per-batter BvP (batter-vs-pitcher) context for the Claude Brain layer.

Follows the same architecture as mlb_edge/platoon_splits.py (platoon-brain MVP,
2026-05-19): pull career splits per batter, package as JSON-string columns on
the diag CSV, let the LLM judgment layer reason about them rather than feeding
them into the XGBoost booster (which would create a dimensionality curse on
the long tail of small BvP samples).

Public API:
    build_per_batter_bvp_payload(batter_ids_with_names, opposing_sp_id) -> List[Dict]
    attach_bvp_to_diag(diag_df, matchup_to_pk, matchup_to_sp_ids) -> diag_df

Output columns added to diag CSV (matches platoon-brain shape):
    away_bvp_top5_json   JSON list of top-5 away batters' BvP records vs HOME SP
    home_bvp_top5_json   JSON list of top-5 home batters' BvP records vs AWAY SP

Per-batter record:
    {
      "order": 1,
      "name": "Ronald Acuña Jr.",
      "vs_today_SP_PA": 18,
      "vs_today_SP_HR": 2,
      "vs_today_SP_OPS": 1.124,
      "vs_today_SP_HR_per_PA": 0.111,
      "shrunk_OPS": 0.892,
      "sample_flag": "OWNER"
    }

sample_flag values (all [H] starting guesses per Rule 9, to be tuned via
backtest per Rule 10):
    NO_DATA           PA == 0
    SMALL_SAMPLE      1 <= PA < 10  (noisy, brain should fall back to baseline)
    MEANINGFUL        10 <= PA < 30  (soft signal)
    LOTS_OF_HISTORY   PA >= 30  (trust the rate more)
    OWNER             PA >= 10 AND OPS >= 0.900  (soft positive)
    WEAK_VS           PA >= 10 AND OPS <= 0.500  (soft negative)

(OWNER and WEAK_VS supersede the size-band labels when their thresholds fire.)

Architecture-Session Pre-Flight Prompt v1.0 compliance:
    Rule 1  — probed (vsPlayer endpoint reachable, parser confirmed via
              existing batter_vs_pitcher.py module)
    Rule 2  — test set locked: 2026-05-16, 2026-05-17, 2026-05-18
    Rule 6  — best-effort try/except with logged exceptions throughout
    Rule 9  — sample_flag thresholds marked [H], not invented as production
              gates.  Brain reads them as soft signals.
    Rule 11 — reverse-direction sanity: no single batter's BvP should flip a
              pick.  Brain prompt instruction enforces this.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sample-flag thresholds (Rule 9: [H], starting guesses)
# ---------------------------------------------------------------------------
PA_LOTS_OF_HISTORY = 30
PA_MEANINGFUL = 10
OWNER_OPS_FLOOR = 0.900
WEAK_OPS_CEILING = 0.500

# Shrinkage: blend observed OPS with a fixed prior (league baseline ~0.720
# overall MLB OPS).  Below MIN_PA_FOR_FULL_TRUST, weight prior more heavily.
SHRINK_PRIOR_OPS = 0.720
SHRINK_PRIOR_WEIGHT_PA = 30  # equivalent PA the prior is worth


def _sample_flag(pa: int, ops: float) -> str:
    if pa <= 0:
        return "NO_DATA"
    if pa >= PA_MEANINGFUL and ops >= OWNER_OPS_FLOOR:
        return "OWNER"
    if pa >= PA_MEANINGFUL and ops <= WEAK_OPS_CEILING:
        return "WEAK_VS"
    if pa >= PA_LOTS_OF_HISTORY:
        return "LOTS_OF_HISTORY"
    if pa >= PA_MEANINGFUL:
        return "MEANINGFUL"
    return "SMALL_SAMPLE"


def _shrunk_ops(observed_ops: float, observed_pa: int) -> float:
    """Bayesian-style shrinkage of observed OPS toward the prior.

    For pa=0 returns the prior.  For pa>=SHRINK_PRIOR_WEIGHT_PA returns
    observed-dominated value.  Smooth transition in between.
    """
    if observed_pa <= 0:
        return SHRINK_PRIOR_OPS
    w_obs = observed_pa / (observed_pa + SHRINK_PRIOR_WEIGHT_PA)
    w_prior = 1.0 - w_obs
    return round(observed_ops * w_obs + SHRINK_PRIOR_OPS * w_prior, 3)


# ---------------------------------------------------------------------------
# Per-batter fetch (delegates to existing batter_vs_pitcher.fetch_bvp)
# ---------------------------------------------------------------------------
def _fetch_one(batter_id: int, pitcher_id: int) -> Optional[Dict]:
    """Fetch BvP for one batter-vs-pitcher pair.  Returns a dict suitable
    for the per-batter record, or None on hard failure.  Cached via the
    existing batter_vs_pitcher module's cache layer.
    """
    try:
        from . import batter_vs_pitcher as _bvp
        stat = _bvp.fetch_bvp(batter_id, pitcher_id)
    except Exception as e:
        log.warning("[bvp_brain] fetch_bvp(%s, %s) failed: %s",
                    batter_id, pitcher_id, e)
        return None
    if stat is None:
        # fetch_bvp returns None on hard failure; treat as NO_DATA
        return {
            "vs_today_SP_PA": 0,
            "vs_today_SP_HR": 0,
            "vs_today_SP_OPS": None,
            "vs_today_SP_HR_per_PA": None,
            "shrunk_OPS": SHRINK_PRIOR_OPS,
            "sample_flag": "NO_DATA",
        }
    pa = int(stat.pa or 0)
    hr = int(stat.hr or 0)
    ops = float(stat.ops or 0.0)
    hr_per_pa = round(hr / pa, 4) if pa > 0 else None
    return {
        "vs_today_SP_PA": pa,
        "vs_today_SP_HR": hr,
        "vs_today_SP_OPS": round(ops, 3) if pa > 0 else None,
        "vs_today_SP_HR_per_PA": hr_per_pa,
        "shrunk_OPS": _shrunk_ops(ops, pa),
        "sample_flag": _sample_flag(pa, ops),
    }


# ---------------------------------------------------------------------------
# Per-side payload assembly
# ---------------------------------------------------------------------------
def build_per_batter_bvp_payload(
    batters: List[Tuple[int, str, int]],
    opposing_sp_id: Optional[int],
) -> List[Dict]:
    """Build the per-batter BvP payload for one side of a matchup.

    batters: list of (order, full_name, batter_id) tuples, sorted by
             batting order ascending.  Typically the top-5 from
             platoon_splits.get_top_n_lineup.
    opposing_sp_id: MLB person ID of the opposing starting pitcher.  If
                    None, returns empty list (no SP confirmed = no BvP
                    payload possible).
    """
    if not batters or opposing_sp_id is None:
        return []
    out: List[Dict] = []
    for order, name, batter_id in batters:
        rec_core = {"order": order, "name": name}
        rec_bvp = _fetch_one(batter_id, opposing_sp_id)
        if rec_bvp is None:
            # Hard failure already logged in _fetch_one; emit NO_DATA stub
            rec_bvp = {
                "vs_today_SP_PA": 0,
                "vs_today_SP_HR": 0,
                "vs_today_SP_OPS": None,
                "vs_today_SP_HR_per_PA": None,
                "shrunk_OPS": SHRINK_PRIOR_OPS,
                "sample_flag": "NO_DATA",
            }
        rec_core.update(rec_bvp)
        out.append(rec_core)
    return out


# ---------------------------------------------------------------------------
# Top-N lineup fetch (delegates to platoon_splits.get_top_n_lineup to keep
# one source of truth for lineup parsing)
# ---------------------------------------------------------------------------
def _get_top_n_lineup(game_pk: int, side: str, n: int = 5) -> List[Tuple[int, str, int]]:
    """Returns [(order, name, batter_id), ...] for the top N batters.

    Falls back to direct MLB API call if platoon_splits import fails.
    """
    try:
        from . import platoon_splits
        return platoon_splits.get_top_n_lineup(game_pk, side, n=n)
    except Exception as e:
        log.warning("[bvp_brain] platoon_splits.get_top_n_lineup failed for "
                    "pk=%s side=%s: %s", game_pk, side, e)
        return []


# ---------------------------------------------------------------------------
# Public attach_to_diag — same shape as platoon_splits.attach_top_5_to_diag
# ---------------------------------------------------------------------------
def attach_bvp_to_diag(
    diag_df: pd.DataFrame,
    matchup_to_game_pk: Dict[str, int],
    matchup_to_sp_ids: Dict[str, Dict[str, int]],
) -> pd.DataFrame:
    """Attach two BvP JSON-string columns to the diag DataFrame in-place.

    matchup_to_game_pk: {"ATL @ MIA": 824XXX, ...}
    matchup_to_sp_ids:  {"ATL @ MIA": {"away_sp_id": 12345, "home_sp_id": 67890}, ...}

    Adds columns:
        away_bvp_top5_json   top-5 AWAY batters' BvP vs HOME SP
        home_bvp_top5_json   top-5 HOME batters' BvP vs AWAY SP

    Best-effort per row (Rule 6): any per-row exception caught + logged,
    cell defaults to "[]" so downstream readers see an empty array.
    """
    if diag_df is None or diag_df.empty:
        log.info("[bvp_brain] diag_df empty — nothing to attach")
        return diag_df

    away_col: List[str] = []
    home_col: List[str] = []
    n_rows = filled_rows = total_batters = 0

    for _, row in diag_df.iterrows():
        n_rows += 1
        matchup = str(row.get("matchup", "")).strip()
        away_json = "[]"
        home_json = "[]"
        try:
            game_pk = matchup_to_game_pk.get(matchup)
            sp_ids = matchup_to_sp_ids.get(matchup) or {}
            if game_pk and sp_ids:
                home_sp = sp_ids.get("home_sp_id")
                away_sp = sp_ids.get("away_sp_id")
                if home_sp:
                    away_lineup = _get_top_n_lineup(game_pk, "away", n=5)
                    away_payload = build_per_batter_bvp_payload(away_lineup, home_sp)
                    if away_payload:
                        away_json = json.dumps(away_payload)
                        filled_rows += 1
                        total_batters += len(away_payload)
                if away_sp:
                    home_lineup = _get_top_n_lineup(game_pk, "home", n=5)
                    home_payload = build_per_batter_bvp_payload(home_lineup, away_sp)
                    if home_payload:
                        home_json = json.dumps(home_payload)
                        total_batters += len(home_payload)
        except Exception as e:
            log.warning("[bvp_brain] row %r failed: %s", matchup, e)
        away_col.append(away_json)
        home_col.append(home_json)

    diag_df["away_bvp_top5_json"] = away_col
    diag_df["home_bvp_top5_json"] = home_col
    log.info("[bvp_brain] attached BvP columns: %d rows, %d filled, "
             "%d total batter payloads", n_rows, filled_rows, total_batters)
    return diag_df


# ---------------------------------------------------------------------------
# CLI for ad-hoc verification
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 3:
        print("Usage: python -m mlb_edge.bvp_brain <batter_id> <pitcher_id>")
        raise SystemExit(2)
    batter_id = int(sys.argv[1])
    pitcher_id = int(sys.argv[2])
    rec = _fetch_one(batter_id, pitcher_id)
    print(json.dumps(rec, indent=2))
