"""
monte_carlo.py
--------------
Phase 1 of the bottom-up Monte Carlo plate-appearance simulator.

SHADOW MODE — adds `pred_winp_mc` / `pred_runs_mc` columns to the diag
CSVs alongside the production XGBoost columns. XGBoost predictions
(p_model, pred_runs, edge_pp, tier, kelly_*) are unchanged; the MC
columns are additive.

What this module does
~~~~~~~~~~~~~~~~~~~~~
Given a single game's matchup (lineups + SP + bullpen + park), run
`n_simulations` independent games where each plate appearance samples an
outcome from a blended (batter x pitcher x park x umpire) distribution.
Returns the empirical home-team win probability and the run-distribution
summary (mean, median, p25, p75).

Why "log-5" blending
~~~~~~~~~~~~~~~~~~~~
Per Bill James / Tom Tango. For each outcome class (K, BB, 1B, ...), we
combine the batter's per-PA rate p_batter and the pitcher's allowed rate
p_pitcher into a matchup-specific rate p_outcome:

    p = (p_b * p_p / p_lg) /
        (p_b * p_p / p_lg + (1-p_b) * (1-p_p) / (1-p_lg))

The blended vector is re-normalized so it sums to 1 across the 11
outcomes.

Park / umpire adjustments
~~~~~~~~~~~~~~~~~~~~~~~~~
- Park: multiply HR%/2B%/3B% by `park_factor` (relative to neutral 1.0).
- Umpire: shift K% by `ump_k_delta` (additive, in fraction units).

Base-running model
~~~~~~~~~~~~~~~~~~
Spec calls for minimum advancement on singles/doubles. We add a small
realism nudge per Tango's run-expectancy tables so the simulator lands
in the right ballpark vs the 8.7 MLB-average total:
  - On a single, runner from 2B scores ~60% of the time.
  - On a single, runner from 1B reaches 3B ~30% of the time.
  - On a double, runner from 1B scores ~40% of the time.
HR scores everyone. Triples score all runners. Walks/HBP only advance
forced runners. GIDP records 2 outs and clears 1B if applicable.

Pitcher swap
~~~~~~~~~~~~
The SP stays in until either:
  - the projected inning limit is reached (start of next half-inning), OR
  - the estimated pitch count exceeds 100 (~16 pitches/inning).
After the swap we use a weighted blend of the available bullpen arms'
rates. If no bullpen is supplied, we synthesise a "league-average
reliever" with K% +2pp vs LG_K_PCT.

Failure modes (all return a NaN-filled dict and log a warning):
  - lineup contains fewer than 9 batters
  - SP id is None
  - rate fetch raises any exception
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

import numpy as np

from .player_rates import (
    LEAGUE_PROB_VEC, LEAGUE_RATES, OUTCOMES,
    fetch_batter_rates, fetch_pitcher_allowed_rates, pitcher_rates_from_overrides,
    _league_rate_dict,
)

log = logging.getLogger(__name__)

# Indexes into the OUTCOMES vector -- used by the inner sim loop.
IDX_K   = OUTCOMES.index("K")
IDX_BB  = OUTCOMES.index("BB")
IDX_HBP = OUTCOMES.index("HBP")
IDX_1B  = OUTCOMES.index("1B")
IDX_2B  = OUTCOMES.index("2B")
IDX_3B  = OUTCOMES.index("3B")
IDX_HR  = OUTCOMES.index("HR")
IDX_GIDP = OUTCOMES.index("GIDP")
IDX_FO  = OUTCOMES.index("FO")
IDX_GO  = OUTCOMES.index("GO")
IDX_LO  = OUTCOMES.index("LO")

# Hit-class indices used for park scaling.
HIT_IDX = np.array([IDX_1B, IDX_2B, IDX_3B, IDX_HR])
HR_IDX = IDX_HR


def _default_reliever_rates() -> Dict[str, float]:
    """Default reliever rate vector (slight K bump). Used when no bullpen
    list is supplied; calibrated to FanGraphs 2024 reliever-only splits."""
    base = _league_rate_dict()
    base["K"] = 0.245   # +2pp vs the league avg of 0.225 (RPs K more)
    # Renormalize the residual across the non-K classes.
    rest_sum = sum(v for k, v in base.items() if k != "K")
    target = 1.0 - base["K"]
    if rest_sum > 0:
        scale = target / rest_sum
        for k in list(base.keys()):
            if k != "K":
                base[k] *= scale
    return base


def _dict_to_vec(rates: Dict[str, float]) -> np.ndarray:
    """Convert a {outcome: prob} dict to a float64 vector aligned with
    the OUTCOMES list. Strips meta keys like `_data_source`."""
    vec = np.array([float(rates.get(o, 0.0)) for o in OUTCOMES], dtype=np.float64)
    s = vec.sum()
    if s > 0:
        vec /= s
    return vec


def _log5_blend(p_batter: np.ndarray, p_pitcher: np.ndarray,
                p_league: np.ndarray) -> np.ndarray:
    """Log-5 blend (Bill James / Tango). All inputs are per-outcome
    probability vectors. Returns a re-normalized blended vector."""
    eps = 1e-9
    pb = np.clip(p_batter, eps, 1 - eps)
    pp = np.clip(p_pitcher, eps, 1 - eps)
    pl = np.clip(p_league, eps, 1 - eps)
    num = pb * pp / pl
    den = num + (1 - pb) * (1 - pp) / (1 - pl)
    blended = num / np.maximum(den, eps)
    s = blended.sum()
    if s <= 0:
        return p_league.copy()
    return blended / s


def _apply_park_and_ump(vec: np.ndarray,
                        park_factor: float = 1.0,
                        ump_k_delta: float = 0.0) -> np.ndarray:
    """Park boosts HR/2B/3B; umpire shifts K%. Always renormalizes."""
    out = vec.copy()
    if park_factor != 1.0:
        out[HIT_IDX] *= park_factor
    if ump_k_delta != 0.0:
        out[IDX_K] = max(out[IDX_K] + ump_k_delta, 0.001)
    s = out.sum()
    if s > 0:
        out /= s
    return out


def _advance(bases: List[bool], outcome_idx: int,
             rng: Optional[np.random.Generator] = None) -> tuple:
    """Advance the runners on `bases` (3-element list -- index 0 = 1B,
    1 = 2B, 2 = 3B; True = occupied) given an `outcome_idx`. Returns
    (new_bases, runs_scored). GIDP is handled in the sim loop directly."""
    runs = 0
    b1, b2, b3 = bases

    if outcome_idx == IDX_HR:
        runs = 1 + int(b1) + int(b2) + int(b3)
        return [False, False, False], runs
    if outcome_idx == IDX_3B:
        runs = int(b1) + int(b2) + int(b3)
        return [False, False, True], runs
    if outcome_idx == IDX_2B:
        # batter to 2B; 3B and 2B always score.
        # 1B usually to 3B, but ~40% of the time scores (aggressive read).
        runs = int(b2) + int(b3)
        new_b3 = False
        new_b2 = True
        new_b1 = False
        if b1:
            if rng is not None and rng.random() < 0.40:
                runs += 1
            else:
                new_b3 = True
        return [new_b1, new_b2, new_b3], runs
    if outcome_idx == IDX_1B:
        # batter to 1B; 3B always scores. 2B ~60% scores. 1B ~30% to 3B.
        runs = int(b3)
        new_b1 = True
        # Handle 2B runner.
        if b2:
            if rng is not None and rng.random() < 0.60:
                runs += 1
                two_to_3 = False
            else:
                two_to_3 = True
        else:
            two_to_3 = False
        # Handle 1B runner.
        if b1:
            if rng is not None and rng.random() < 0.30 and not two_to_3:
                # 1B aggressively to 3B (open base / OF bobble)
                new_b3 = True
                new_b2 = False
            else:
                new_b3 = two_to_3
                new_b2 = True
        else:
            new_b3 = two_to_3
            new_b2 = False
        return [new_b1, new_b2, new_b3], runs
    if outcome_idx in (IDX_BB, IDX_HBP):
        # Force-advance only.
        if b1 and b2 and b3:
            return [True, True, True], 1
        if b1 and b2 and not b3:
            return [True, True, True], 0
        if b1 and not b2:
            return [True, True, b3], 0
        return [True, b2, b3], 0

    # K / FO / GO / LO -- no base movement, no runs.
    return [b1, b2, b3], 0


def _simulate_half_inning(batter_vecs, rng, next_batter_idx):
    """Simulate one half-inning. Returns (runs_scored, next_batter_idx, pitches)."""
    outs = 0
    bases = [False, False, False]
    runs = 0
    bidx = next_batter_idx
    pitches = 0
    PITCHES_PER_PA = 3.9
    max_pa = 25
    pa_count = 0
    while outs < 3 and pa_count < max_pa:
        vec = batter_vecs[bidx]
        u = rng.random()
        cum = 0.0
        chosen_idx = 0
        for i in range(len(vec)):
            cum += vec[i]
            if u < cum:
                chosen_idx = i
                break
        pa_count += 1
        pitches += int(PITCHES_PER_PA)

        if chosen_idx == IDX_K:
            outs += 1
        elif chosen_idx in (IDX_GO, IDX_FO, IDX_LO):
            outs += 1
        elif chosen_idx == IDX_GIDP:
            if bases[0] and outs < 2:
                outs += 2
                bases[0] = False
            else:
                outs += 1
        else:
            bases, r = _advance(bases, chosen_idx, rng=rng)
            runs += r

        bidx = (bidx + 1) % 9

    return runs, bidx, pitches


def _simulate_one_game(
    home_batter_vecs, away_batter_vecs,
    home_sp_innings_cap, away_sp_innings_cap,
    home_sp_pitch_cap, away_sp_pitch_cap,
    rng,
    away_vs_home_sp, home_vs_away_sp,
    away_vs_home_bp, home_vs_away_bp,
):
    """Simulate a single 9-inning game. Extra innings: up to 12, then
    coin flip if still tied."""
    home_score = 0
    away_score = 0
    home_batter_idx = 0
    away_batter_idx = 0
    home_pitches = 0
    away_pitches = 0
    home_sp_active = True
    away_sp_active = True
    home_full_innings_thrown = 0
    away_full_innings_thrown = 0

    for inning in range(1, 10):
        # TOP -- away batting vs home pitcher.
        bvec = away_vs_home_sp if home_sp_active else away_vs_home_bp
        runs, away_batter_idx, p = _simulate_half_inning(
            bvec, rng, away_batter_idx
        )
        away_score += runs
        home_pitches += p
        if home_sp_active:
            home_full_innings_thrown += 1
            if (home_full_innings_thrown >= home_sp_innings_cap
                    or home_pitches >= home_sp_pitch_cap):
                home_sp_active = False

        # BOTTOM -- home batting vs away pitcher.
        if inning == 9 and home_score > away_score:
            break
        bvec = home_vs_away_sp if away_sp_active else home_vs_away_bp
        runs, home_batter_idx, p = _simulate_half_inning(
            bvec, rng, home_batter_idx
        )
        home_score += runs
        away_pitches += p
        if away_sp_active:
            away_full_innings_thrown += 1
            if (away_full_innings_thrown >= away_sp_innings_cap
                    or away_pitches >= away_sp_pitch_cap):
                away_sp_active = False

        if inning == 9 and home_score > away_score:
            break

    # Extra innings (no ghost runner).
    extra = 10
    while home_score == away_score and extra <= 12:
        bvec = away_vs_home_bp
        runs, away_batter_idx, _ = _simulate_half_inning(bvec, rng, away_batter_idx)
        away_score += runs
        if home_score > away_score:
            break
        bvec = home_vs_away_bp
        runs, home_batter_idx, _ = _simulate_half_inning(bvec, rng, home_batter_idx)
        home_score += runs
        if home_score > away_score:
            break
        extra += 1

    if home_score == away_score:
        if rng.random() < 0.5:
            home_score += 1
        else:
            away_score += 1

    return home_score, away_score


def _bullpen_vec(bullpen, date):
    """Weighted blend of the available reliever rate vectors."""
    if not bullpen:
        return _dict_to_vec(_default_reliever_rates())
    vecs = []
    weights = []
    for arm in bullpen:
        if not arm.get("available", True):
            continue
        pid = arm.get("pid") or arm.get("pitcher_id")
        if pid is None:
            continue
        k_pct = arm.get("k_pct")
        bb_pct = arm.get("bb_pct")
        xwoba = arm.get("xwoba_allowed")
        if k_pct is not None or bb_pct is not None or xwoba is not None:
            rates = pitcher_rates_from_overrides(k_pct, bb_pct, xwoba)
        else:
            rates = fetch_pitcher_allowed_rates(int(pid), date)
        vecs.append(_dict_to_vec(rates))
        weights.append(float(arm.get("weight", 1.0)))
    if not vecs:
        return _dict_to_vec(_default_reliever_rates())
    arr = np.array(vecs)
    w = np.array(weights)
    w = w / w.sum()
    return (arr * w[:, None]).sum(axis=0)


def _build_blended_matrix(batter_vecs, pitcher_vec, park_factor, ump_k_delta):
    """Per-batter log-5 blend vs pitcher, then park+ump. Returns (9, 11)."""
    out = np.empty_like(batter_vecs)
    for i in range(batter_vecs.shape[0]):
        blended = _log5_blend(batter_vecs[i], pitcher_vec, LEAGUE_PROB_VEC)
        out[i] = _apply_park_and_ump(blended, park_factor, ump_k_delta)
    return out


def simulate_game(
    home_lineup,
    away_lineup,
    home_sp,
    away_sp,
    home_bullpen=None,
    away_bullpen=None,
    park_factor=1.0,
    ump_k_delta=0.0,
    n_simulations=10000,
    rng_seed=None,
    date=None,
):
    """Run `n_simulations` independent simulations of one MLB game.

    Returns dict with:
      home_winp, away_winp, mean_total_runs, median_total_runs,
      p25_total_runs, p75_total_runs, mean_home_runs, mean_away_runs,
      n_simulations.

    Returns a dict of NaNs (and logs a warning) on any failure so the
    caller can fall back without crashing the slate.
    """
    rng = np.random.default_rng(rng_seed)
    nan_result = {
        "home_winp": float("nan"), "away_winp": float("nan"),
        "mean_total_runs": float("nan"), "median_total_runs": float("nan"),
        "p25_total_runs": float("nan"), "p75_total_runs": float("nan"),
        "mean_home_runs": float("nan"), "mean_away_runs": float("nan"),
        "n_simulations": 0,
    }

    if not home_lineup or not away_lineup:
        log.warning("[mc] missing lineup (home=%d / away=%d) -- skip",
                    len(home_lineup or []), len(away_lineup or []))
        return nan_result
    if len(home_lineup) < 9 or len(away_lineup) < 9:
        log.warning("[mc] lineup < 9 batters (home=%d / away=%d) -- skip",
                    len(home_lineup), len(away_lineup))
        return nan_result
    if len(home_lineup) > 9:
        home_lineup = home_lineup[:9]
    if len(away_lineup) > 9:
        away_lineup = away_lineup[:9]
    if not home_sp or not home_sp.get("pid"):
        log.warning("[mc] missing home SP id -- skip")
        return nan_result
    if not away_sp or not away_sp.get("pid"):
        log.warning("[mc] missing away SP id -- skip")
        return nan_result

    if date is None:
        from datetime import date as _date
        date = _date.today().isoformat()

    try:
        home_pids = [b["pid"] for b in home_lineup]
        away_pids = [b["pid"] for b in away_lineup]
        home_rates_df = fetch_batter_rates(home_pids, date)
        away_rates_df = fetch_batter_rates(away_pids, date)

        def _vecs_for(pids, rates_df):
            lookup = rates_df.set_index("player_id")
            vecs = np.zeros((9, len(OUTCOMES)), dtype=np.float64)
            for i, pid in enumerate(pids):
                pid_i = int(pid)
                if pid_i in lookup.index:
                    row = lookup.loc[pid_i]
                    if hasattr(row, 'iloc') and hasattr(row, 'shape') and len(row.shape) > 1:
                        row = row.iloc[0]
                    vec = np.array([float(row.get(o, 0.0)) for o in OUTCOMES])
                else:
                    vec = LEAGUE_PROB_VEC.copy()
                s = vec.sum()
                if s > 0:
                    vec /= s
                vecs[i] = vec
            return vecs

        home_batter_vecs = _vecs_for(home_pids, home_rates_df)
        away_batter_vecs = _vecs_for(away_pids, away_rates_df)

        def _sp_vec(sp):
            k = sp.get("k_pct")
            bb = sp.get("bb_pct")
            xw = sp.get("xwoba_allowed")
            if k is not None or bb is not None or xw is not None:
                rates = pitcher_rates_from_overrides(k, bb, xw)
            else:
                rates = fetch_pitcher_allowed_rates(int(sp["pid"]), date)
            return _dict_to_vec(rates)

        home_sp_vec = _sp_vec(home_sp)
        away_sp_vec = _sp_vec(away_sp)
        home_bp_vec = _bullpen_vec(home_bullpen, date)
        away_bp_vec = _bullpen_vec(away_bullpen, date)

        away_vs_home_sp = _build_blended_matrix(
            away_batter_vecs, home_sp_vec, park_factor, ump_k_delta)
        away_vs_home_bp = _build_blended_matrix(
            away_batter_vecs, home_bp_vec, park_factor, ump_k_delta)
        home_vs_away_sp = _build_blended_matrix(
            home_batter_vecs, away_sp_vec, park_factor, ump_k_delta)
        home_vs_away_bp = _build_blended_matrix(
            home_batter_vecs, away_bp_vec, park_factor, ump_k_delta)
    except Exception as e:
        log.warning("[mc] rate fetch / blend failed: %s -- skip", e)
        return nan_result

    home_sp_innings = float(home_sp.get("innings", 6.0))
    away_sp_innings = float(away_sp.get("innings", 6.0))
    home_sp_pitches = int(home_sp.get("pitch_cap", 100))
    away_sp_pitches = int(away_sp.get("pitch_cap", 100))

    home_runs_arr = np.empty(n_simulations, dtype=np.int32)
    away_runs_arr = np.empty(n_simulations, dtype=np.int32)
    try:
        for i in range(n_simulations):
            h, a = _simulate_one_game(
                home_batter_vecs=home_batter_vecs,
                away_batter_vecs=away_batter_vecs,
                home_sp_innings_cap=home_sp_innings,
                away_sp_innings_cap=away_sp_innings,
                home_sp_pitch_cap=home_sp_pitches,
                away_sp_pitch_cap=away_sp_pitches,
                rng=rng,
                away_vs_home_sp=away_vs_home_sp,
                home_vs_away_sp=home_vs_away_sp,
                away_vs_home_bp=away_vs_home_bp,
                home_vs_away_bp=home_vs_away_bp,
            )
            home_runs_arr[i] = h
            away_runs_arr[i] = a
    except Exception as e:
        log.warning("[mc] simulation loop crashed: %s -- skip", e)
        return nan_result

    totals = home_runs_arr + away_runs_arr
    return {
        "home_winp":         float((home_runs_arr > away_runs_arr).mean()),
        "away_winp":         float((away_runs_arr > home_runs_arr).mean()),
        "mean_total_runs":   float(totals.mean()),
        "median_total_runs": float(np.median(totals)),
        "p25_total_runs":    float(np.percentile(totals, 25)),
        "p75_total_runs":    float(np.percentile(totals, 75)),
        "mean_home_runs":    float(home_runs_arr.mean()),
        "mean_away_runs":    float(away_runs_arr.mean()),
        "n_simulations":     int(n_simulations),
    }


def simulate_slate_row(
    *, date, home_team, away_team,
    home_lineup_ids, away_lineup_ids,
    home_sp_id, away_sp_id,
    home_sp_k_pct=None, home_sp_bb_pct=None, home_sp_xwoba=None,
    away_sp_k_pct=None, away_sp_bb_pct=None, away_sp_xwoba=None,
    home_sp_innings=6.0, away_sp_innings=6.0,
    home_bullpen=None, away_bullpen=None,
    park_runs_factor=100.0, ump_k_pct_delta=0.0,
    n_simulations=10000, rng_seed=None,
):
    """Glue between the diag-CSV row and the simulate_game() API.

    Translates the row's column conventions (park_runs_factor at 100=neutral,
    ump_k_pct_delta in percentage points, SP K%/BB% in PERCENT) into the
    simulator's native units. Returns the same dict shape as simulate_game.
    """
    home_lineup = [{"pid": int(p), "name": ""} for p in home_lineup_ids]
    away_lineup = [{"pid": int(p), "name": ""} for p in away_lineup_ids]

    park_factor = (park_runs_factor / 100.0) if park_runs_factor else 1.0
    ump_delta_frac = (ump_k_pct_delta / 100.0) if ump_k_pct_delta else 0.0

    home_sp = {
        "pid": int(home_sp_id),
        "innings": home_sp_innings,
        "k_pct": home_sp_k_pct,
        "bb_pct": home_sp_bb_pct,
        "xwoba_allowed": home_sp_xwoba,
    }
    away_sp = {
        "pid": int(away_sp_id),
        "innings": away_sp_innings,
        "k_pct": away_sp_k_pct,
        "bb_pct": away_sp_bb_pct,
        "xwoba_allowed": away_sp_xwoba,
    }

    return simulate_game(
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        home_sp=home_sp,
        away_sp=away_sp,
        home_bullpen=home_bullpen,
        away_bullpen=away_bullpen,
        park_factor=park_factor,
        ump_k_delta=ump_delta_frac,
        n_simulations=n_simulations,
        rng_seed=rng_seed,
        date=date,
    )


if __name__ == "__main__":
    import argparse, json, time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=1000, help="simulations")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    t0 = time.time()
    result = simulate_game(
        home_lineup=[{"pid": 660271 + i, "name": "H%d" % i} for i in range(9)],
        away_lineup=[{"pid": 545361 + i, "name": "A%d" % i} for i in range(9)],
        home_sp={"pid": 621121, "innings": 6.0, "hand": "R"},
        away_sp={"pid": 607208, "innings": 6.0, "hand": "R"},
        home_bullpen=[], away_bullpen=[],
        n_simulations=args.n,
        rng_seed=args.seed,
    )
    elapsed = time.time() - t0
    print(json.dumps(result, indent=2))
    print("\nelapsed: %.2fs for %d sims (%.2fms per sim)" %
          (elapsed, args.n, elapsed / args.n * 1000))
