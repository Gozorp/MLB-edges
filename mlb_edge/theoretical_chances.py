#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
theoretical_chances.py  --  "Theoretical chances" (HYPOTHETICAL toy model)
==========================================================================
Requested as an explicitly *theoretical / hypothetical* win-probability sandbox.
This is NOT the production model. Anything it outputs must be surfaced to users
as a clearly-labeled "Theoretical chances (hypothetical)" figure, kept separate
from the real pick / probability. Nothing in the production pipeline imports
this module -- it is fully isolated, so it cannot affect live picks.

It follows the spirit of the requested "Path-Integral Predictive Architecture",
but rewritten so it actually RUNS and returns a real number (the supplied code
did not: its 24-state Q matrix was a placeholder, `scoring_reward_matrix` was
undefined, and `np.math.factorial` is gone in NumPy 2). Honest provenance per
phase:

  Phase 1  REAL        air density from T/H/P -> ball "carry" -> HR/XBH nudge.
  Phase 3  REAL        Log5 batter-vs-pitcher composition + James-Stein /
                       empirical-Bayes shrinkage of noisy rates to a prior.
  Inning   REAL        24 base-out-state Markov chain w/ standard advancement,
                       Monte-Carlo'd to a per-inning run PMF.
  Phase 5  REAL        convolution of 9 i.i.d. inning PMFs -> each team's game
                       score distribution -> P(home > away) (+ coin-flip ties).
  Phase 2  HEURISTIC   the "quantum drift-diffusion" swing model is a small,
                       transparent whiff nudge -- NOT literal wave-function
                       collapse (swing decisions are not quantum-mechanical).
  Phase 4  HEURISTIC   the "HJBI managerial game" is a small late-game run-
                       suppression nudge -- NOT a differential-game solve
                       (managers do not play continuous-time Nash equilibria,
                       and it isn't estimable from box data anyway).

Demo:  python mlb_edge/theoretical_chances.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Outcome order everywhere: [out, bb, 1b, 2b, 3b, hr]
OUT, BB, S1, S2, S3, HR = range(6)
# League-average plate-appearance outcome prior (rough MLB shares).
LEAGUE_PA = np.array([0.690, 0.085, 0.140, 0.045, 0.004, 0.036], dtype=float)


# ----------------------------------------------------------------------------
# Phase 1 (REAL): atmospheric air density -> ball carry
# ----------------------------------------------------------------------------
def air_density(temp_f: float, relative_humidity_pct: float, pressure_inhg: float) -> float:
    """Humid-air density in kg/m^3 (same formula as the supplied code -- it was
    one of the parts that was actually correct)."""
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    temp_k = temp_c + 273.15
    pressure_pa = pressure_inhg * 3386.39
    p_sat = 610.78 * np.exp((17.27 * temp_c) / (temp_c + 237.3))
    vapor = (relative_humidity_pct / 100.0) * p_sat
    dry = pressure_pa - vapor
    return float(dry / (287.058 * temp_k) + vapor / (461.495 * temp_k))


def carry_factor(rho: float) -> float:
    """Thinner air (lower rho) -> more carry -> more HR/XBH. ~1.0 at sea level
    (rho ~= 1.18). Physically-directed heuristic, gently bounded."""
    f = (1.18 / max(rho, 1e-6)) ** 0.5
    return float(np.clip(f, 0.85, 1.20))


# ----------------------------------------------------------------------------
# Phase 3 (REAL): shrinkage + Log5
# ----------------------------------------------------------------------------
def james_stein_shrink(observed: np.ndarray, prior: np.ndarray, n: int, n0: float = 200.0) -> np.ndarray:
    """Shrink an observed rate vector toward a league prior by sample size n
    (empirical-Bayes flavour). Small n -> mostly prior; large n -> mostly self."""
    w = n / (n + n0)
    out = w * observed + (1.0 - w) * prior
    return out / out.sum()


def log5_pa(batter: np.ndarray, pitcher: np.ndarray, league: np.ndarray = LEAGUE_PA) -> np.ndarray:
    """Per-outcome odds-ratio (Log5) composition of a batter vs a pitcher
    relative to league baseline, renormalized to a valid PA distribution."""
    eps = 1e-12
    odds = (batter * pitcher) / (league + eps)
    return odds / odds.sum()


# ----------------------------------------------------------------------------
# Phases 2 & 4 (HEURISTIC, clearly labeled -- not physics)
# ----------------------------------------------------------------------------
def swing_whiff_nudge(pa: np.ndarray, tunneling_index: float = 0.0) -> np.ndarray:
    """Phase 2 stand-in. Higher pitcher 'tunneling' -> a few more whiffs (out
    share up), mass taken proportionally from contact outcomes. Pure heuristic."""
    if tunneling_index == 0.0:
        return pa
    pa = pa.copy()
    bump = 0.06 * np.tanh(tunneling_index)          # <= ~6% relative
    take = pa[OUT] * bump
    pa[OUT] += take
    contact = pa[BB:].sum()
    if contact > 0:
        pa[BB:] -= take * (pa[BB:] / contact)
    return np.clip(pa, 1e-9, None) / pa.sum()


def managerial_leverage_nudge(pmf: np.ndarray, suppression: float = 0.0) -> np.ndarray:
    """Phase 4 stand-in. A small late-game run-suppression effect (a sharper
    bullpen trims the high tail of the run distribution). Heuristic, bounded."""
    if suppression <= 0.0:
        return pmf
    runs = np.arange(len(pmf))
    weight = np.exp(-suppression * 0.04 * runs)     # gently shave the tail
    out = pmf * weight
    return out / out.sum()


# ----------------------------------------------------------------------------
# Inning engine (REAL): 24 base-out states, standard advancement, Monte-Carlo
# ----------------------------------------------------------------------------
def inning_run_pmf(pa: np.ndarray, rng: np.random.Generator,
                   n_sims: int = 40000, max_runs: int = 18) -> np.ndarray:
    """Monte-Carlo a single half-inning under the per-PA outcome distribution,
    returning a PMF over runs scored. Base advancement rules:
        BB  -> batter to 1B, force chained runners
        1B  -> batter to 1B, every runner +1 base
        2B  -> batter to 2B, every runner +2 bases
        3B  -> all runners score, batter to 3B
        HR  -> batter + all runners score
        OUT -> outs += 1
    (Outs end the inning at 3; productive outs / DPs / errors are ignored --
    standard for a toy 24-state chain.)"""
    cum = np.cumsum(pa)
    counts = np.zeros(max_runs + 1, dtype=float)
    rand = rng.random(n_sims * 22)                  # pre-draw; ~plenty per inning
    ri = 0
    for _ in range(n_sims):
        on1 = on2 = on3 = False
        outs = runs = 0
        while outs < 3:
            if ri >= rand.size:                     # extremely rare top-up
                rand = rng.random(n_sims * 4); ri = 0
            ev = int(np.searchsorted(cum, rand[ri])); ri += 1
            if ev == OUT:
                outs += 1
            elif ev == BB:
                if on1 and on2 and on3:
                    runs += 1
                elif on1 and on2:
                    on3 = True
                elif on1:
                    on2 = True
                on1 = True
            elif ev == S1:
                if on3:
                    runs += 1
                on3, on2, on1 = on2, on1, True
            elif ev == S2:
                runs += int(on3) + int(on2)
                on3, on2, on1 = on1, True, False
            elif ev == S3:
                runs += int(on1) + int(on2) + int(on3)
                on1 = on2 = False; on3 = True
            else:  # HR
                runs += 1 + int(on1) + int(on2) + int(on3)
                on1 = on2 = on3 = False
        counts[min(runs, max_runs)] += 1.0
    return counts / counts.sum()


def game_score_pmf(inning_pmf: np.ndarray, innings: int = 9) -> np.ndarray:
    """Full-game score distribution = convolution of `innings` i.i.d. inning
    PMFs (the FFT-convolution step of Phase 5; np.convolve is exact here)."""
    pmf = inning_pmf.copy()
    for _ in range(innings - 1):
        pmf = np.convolve(pmf, inning_pmf)
    return pmf / pmf.sum()


def win_probability(home_pmf: np.ndarray, away_pmf: np.ndarray) -> float:
    """P(home runs > away runs) + 0.5 * P(tie). The 0.5 is an honest coin-flip
    for the extra-inning ghost-runner rule -- not the supplied 0.53452 magic
    constant."""
    cdf_away = np.cumsum(away_pmf)
    p_home_more = 0.0
    p_tie = 0.0
    for h in range(len(home_pmf)):
        below = cdf_away[h - 1] if h - 1 >= 0 else 0.0
        p_home_more += home_pmf[h] * below
        if h < len(away_pmf):
            p_tie += home_pmf[h] * away_pmf[h]
    return float(p_home_more + 0.5 * p_tie)


# ----------------------------------------------------------------------------
# Top-level assembly
# ----------------------------------------------------------------------------
@dataclass
class TeamInputs:
    name: str
    bat_rates: Optional[np.ndarray] = None   # [out,bb,1b,2b,3b,hr]; None -> league
    bat_pa: int = 600                        # sample size for shrinkage
    opp_sp_rates: Optional[np.ndarray] = None
    tunneling_index: float = 0.0             # opposing-pitcher deception (heuristic)
    bullpen_suppression: float = 0.0         # late-game relief quality (heuristic)


@dataclass
class Weather:
    temp_f: float = 70.0
    humidity_pct: float = 50.0
    pressure_inhg: float = 29.92


def _effective_pa(team: TeamInputs, carry: float) -> np.ndarray:
    bat = LEAGUE_PA if team.bat_rates is None else np.asarray(team.bat_rates, float)
    bat = james_stein_shrink(bat, LEAGUE_PA, team.bat_pa)
    pa = bat if team.opp_sp_rates is None else log5_pa(bat, np.asarray(team.opp_sp_rates, float))
    # Phase 1 carry: scale HR strongly, XBH mildly, renormalize.
    pa = pa.copy()
    pa[HR] *= carry
    pa[S2] *= 1.0 + (carry - 1.0) * 0.5
    pa[S3] *= 1.0 + (carry - 1.0) * 0.5
    pa = pa / pa.sum()
    # Phase 2 heuristic whiff nudge.
    return swing_whiff_nudge(pa, team.tunneling_index)


def theoretical_win_probability(home: TeamInputs, away: TeamInputs,
                                weather: Optional[Weather] = None,
                                seed: int = 7) -> dict:
    """Return the HYPOTHETICAL home win probability and the pieces behind it."""
    weather = weather or Weather()
    rng = np.random.default_rng(seed)
    rho = air_density(weather.temp_f, weather.humidity_pct, weather.pressure_inhg)
    carry = carry_factor(rho)

    home_pa = _effective_pa(home, carry)
    away_pa = _effective_pa(away, carry)

    home_inn = managerial_leverage_nudge(inning_run_pmf(home_pa, rng), away.bullpen_suppression)
    away_inn = managerial_leverage_nudge(inning_run_pmf(away_pa, rng), home.bullpen_suppression)

    home_game = game_score_pmf(home_inn)
    away_game = game_score_pmf(away_inn)
    wp_home = win_probability(home_game, away_game)

    return {
        "wp_home": wp_home,
        "wp_away": 1.0 - wp_home,
        "air_density": rho,
        "carry_factor": carry,
        "home_exp_runs": float(np.dot(np.arange(len(home_game)), home_game)),
        "away_exp_runs": float(np.dot(np.arange(len(away_game)), away_game)),
        "label": "THEORETICAL / HYPOTHETICAL — not the production pick",
    }


# Thin wrapper that keeps the requested class name for continuity.
class EnterpriseSabermetricFieldEngine:
    """Honors the requested entry point; delegates to the runnable functions."""
    compute_atmospheric_air_density = staticmethod(air_density)
    execute_multiclass_log5_matchup = staticmethod(log5_pa)

    def theoretical_win_probability(self, home: TeamInputs, away: TeamInputs,
                                    weather: Optional[Weather] = None) -> dict:
        return theoretical_win_probability(home, away, weather)


if __name__ == "__main__":
    eng = EnterpriseSabermetricFieldEngine()

    print("=== Theoretical chances (HYPOTHETICAL toy model) ===\n")

    # 1) Two league-average teams, neutral weather -> ~50/50.
    r = theoretical_win_probability(TeamInputs("HOME"), TeamInputs("AWAY"))
    print(f"League avg vs league avg (neutral): HOME {r['wp_home']*100:5.1f}%  "
          f"AWAY {r['wp_away']*100:5.1f}%  | exp runs {r['home_exp_runs']:.2f}-{r['away_exp_runs']:.2f}")

    # 2) A strong offense at hot, thin-air Coors-like conditions vs a weak one.
    strong = np.array([0.640, 0.095, 0.150, 0.055, 0.005, 0.055])   # more XBH/HR, fewer outs
    weak   = np.array([0.730, 0.070, 0.125, 0.038, 0.003, 0.025])
    r2 = theoretical_win_probability(
        TeamInputs("HOME", bat_rates=strong, tunneling_index=0.0),
        TeamInputs("AWAY", bat_rates=weak,  bullpen_suppression=0.0),
        Weather(temp_f=92, humidity_pct=20, pressure_inhg=24.9),     # high altitude
    )
    print(f"Strong off. (thin air)  vs weak off.: HOME {r2['wp_home']*100:5.1f}%  "
          f"AWAY {r2['wp_away']*100:5.1f}%  | rho={r2['air_density']:.3f} "
          f"carry={r2['carry_factor']:.3f} | exp runs {r2['home_exp_runs']:.2f}-{r2['away_exp_runs']:.2f}")

    # 3) Even bats, but the away pitcher 'tunnels' and the home pen is lights-out.
    r3 = theoretical_win_probability(
        TeamInputs("HOME", bullpen_suppression=1.0),
        TeamInputs("AWAY", tunneling_index=1.0),
        Weather(),
    )
    print(f"Deception + bullpen edge to HOME    : HOME {r3['wp_home']*100:5.1f}%  "
          f"AWAY {r3['wp_away']*100:5.1f}%")

    print("\n(HYPOTHETICAL — isolated from the production model and pick.)")
