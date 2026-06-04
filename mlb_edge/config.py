"""
config.py
---------
Central configuration for the SP-anchored MLB model.

All magic numbers live here. Do not scatter thresholds across modules; change
them here and re-backtest. The defaults below encode the v12-CONVICTION
framework from prior model iterations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Feature weighting — Stage 1 (F5 / Starting Pitcher Anchor)
# ---------------------------------------------------------------------------
# These weights are *prior* weights used for feature scaling and sanity checks
# in the engineered-feature composite score. The gradient-boosted model learns
# its own feature importances; these act as a sanity anchor and drive the
# rule-based conviction filter.

SP_WEIGHTS: Dict[str, float] = {
    # Starting pitcher quality dominates F5 prediction.
    "sp_xera_gap": 0.35,        # (opp_SP_xERA - our_SP_xERA), larger = bigger edge
    "sp_xwoba_allowed_gap": 0.25,
    "sp_k_bb_pct_gap": 0.15,
    "sp_siera_gap": 0.15,
    "sp_recent_form_gap": 0.10,  # last-3-starts xFIP, fade recency over-reactions
}
assert abs(sum(SP_WEIGHTS.values()) - 1.0) < 1e-6, "SP weights must sum to 1.0"


# ---------------------------------------------------------------------------
# Feature weighting — Stage 2 (Full Game)
# ---------------------------------------------------------------------------
# Stage 2 takes Stage 1 F5 output as an input AND adds these. The high weight
# on f5_model_output is the architectural guarantee against SP dilution: the
# starting pitcher's contribution is already "locked in" before we layer
# offense/bullpen/context on top.

FULL_GAME_WEIGHTS: Dict[str, float] = {
    "f5_model_output": 0.60,     # hard anchor — SP signal survives
    "bullpen_siera_gap": 0.12,
    "bullpen_fatigue_gap": 0.08,
    "team_wrcplus_gap": 0.10,
    "handedness_split_edge": 0.05,
    "park_hr_factor": 0.03,
    "catcher_framing_gap": 0.02,
}
assert abs(sum(FULL_GAME_WEIGHTS.values()) - 1.0) < 1e-6, "Full-game weights must sum to 1.0"


# ---------------------------------------------------------------------------
# v12-CONVICTION filter thresholds
# ---------------------------------------------------------------------------
# A pick is only "live" if multiple independent signals converge. Single-signal
# edges tend to be noise. These thresholds are calibrated from prior backtests.

@dataclass(frozen=True)
class ConvictionThresholds:
    xera_gap_min: float = 0.75        # F1: opp SP xERA - our SP xERA (runs/9)
    xwoba_gap_min: float = 0.020      # F2: team xwOBA gap
    swing_take_gap_min: float = 15.0  # F3: runs_all swing/take gap (per-PA*1000 scaled)
    pitcher_luck_max: float = -1.0    # F4: ERA - xERA; negative = lucky, due for regression
    # F4 reliability gate. ERA - xERA is meaningless on tiny samples (e.g. a
    # pitcher returning from injury with 60 IP). Require both starters to have
    # at least this many pitches/BF before letting F4 fire as a primary signal.
    # 800 pitches ≈ 200 IP / 13 starts — a credible sample for ERA luck.
    sp_n_pitches_min_f4: int = 800
    # F1 reliability gate. xERA gap on small samples produces extreme values
    # that don't reflect true talent — Crochet's 7.88 ERA over ~4 starts
    # triggered a PLATINUM fade on 2026-04-25 that lost 17-1. Require both
    # starters to have at least this many pitches before F1 can fire as a
    # primary conviction signal. 600 pitches ≈ 25 IP, a credible floor for
    # xwOBA stabilization. Below this, F1 becomes a soft note, not a tier
    # driver. The shrinkage in point_in_time.pitcher_as_of() is the first
    # line of defense; this is the second.
    sp_n_pitches_min_f1: int = 600
    # F5 (bullpen-disagreement / Stage-2 flip) reliability gate. v11 fix —
    # `bullpen_siera_gap` on small April samples drove the BAL +0.68 false
    # signal that the model said "BAL bullpen is better than BOS". Require
    # both teams' bullpens to have at least this many pitches before any
    # bullpen-driven Stage-2 disagreement can promote a tier. ~3000 pitches
    # ≈ 20 team-games of relief work, when team-level bullpen rate stats
    # have meaningful information about true talent.
    bp_n_pitches_min_f5: int = 3000


CONVICTION = ConvictionThresholds()


# Conviction score -> bet tier mapping.
# The tier maps to a size multiplier applied to the Kelly stake.
TIER_SIZES: Dict[str, float] = {
    "DIAMOND": 1.00,   # 3+ signals fire — the elite tier
    "PLATINUM": 0.30,  # 2026-04-26 evening: PLATINUM re-enabled after v9-vs-v12
                       # diagnostic showed the multi-year SP frame (not tier
                       # filter) was the regression source. With current-year
                       # SP + light shrinkage, PLATINUM-tier bets are worth
                       # taking again. Backtest projection: ~65% hit rate,
                       # +27% ROI per bet, ~3-4 bets/day.
    "GOLD": 0.00,      # still dropped — single-signal bets remain too noisy
    "SKIP": 0.00,      # 0 signals — no bet regardless of nominal edge
}


# ---------------------------------------------------------------------------
# F5 / Stage-1 override rule (DISABLED BY DEFAULT — see backtest note below)
# ---------------------------------------------------------------------------
# Motivation: on the 4/23/26 slate, Stage 1 (F5, SP-anchored) and Stage 2
# (full game, +team/bullpen/park) disagreed on the pick side in 6 of 9 games.
# Manual per-game conflict resolution picked Stage 1's side in 5 of 6 cases
# — the one Stage-2 concession was Coors Field (park_runs_factor = 1.17),
# where the environmental effect legitimately outweighs the pitching anchor.
#
# Rule encoded: when Stage 1 and Stage 2 pick different sides, overwrite
# `model_prob` with `f5_prob` UNLESS |park_runs_factor − 1.0| >
# ENVIRONMENTAL_PARK_THRESHOLD. Coors (1.17) exempts; most parks (0.94-1.08)
# do not.
#
# Backtest verdict (2023+2024+2025 pooled, Statcast cached, 139→137 bets):
#   v8.1 (no override):  n=139  WR=62.6%  ROI=+11.42%
#   v9   (override on):  n=137  WR=56.2%  ROI=+6.98%   <-- loses 4.4 pp
#
# Per-season: 2023 −36pp, 2024 +26pp, 2025 −3pp. Mixed. The 2024 win does
# not offset the 2023 crater. Root cause: one slate's gut read (N=6 games)
# doesn't survive as a runtime rule over 139 market-tested bets.
#
# Leaving the code in place; the flag is OFF by default. Flip it to True
# to reproduce the v9 behavior, or to experiment with stricter env thresholds
# / secondary filters that might salvage the rule.
F5_OVERRIDE_ON_DISAGREEMENT: bool = False
ENVIRONMENTAL_PARK_THRESHOLD: float = 0.10


# ---------------------------------------------------------------------------
# Market / EV thresholds
# ---------------------------------------------------------------------------
MIN_EDGE_PCT: float = 0.04            # v8.2: loosened 0.05 -> 0.04 after
                                      #       grid_search_filter.py on 2023-2025
                                      #       walk-forward (cache v5 retrain).
                                      #       The (0.04, 0.07, 0.50) combo beat
                                      #       the old (0.05, 0.07, 0.45) on
                                      #       pooled ROI by ~12x (+4.50% vs
                                      #       +0.37%). Fewer bets (134 vs 203)
                                      #       but each one is much higher
                                      #       quality: WR 57.5% vs 52.2%.
                                      #       Positive in 2024 (+6.0%) AND 2025
                                      #       (+6.0%) with 2023 near break-even
                                      #       (-2.5%). No blow-up year.
MAX_EDGE_PCT: float = 0.15            # v8.1: kept at 0.07. Grid search confirms
                                      #       edges >0.07 are fool's gold — the
                                      #       (mi, 0.10) and (mi, 0.15) rows
                                      #       cluster around pooled ROI 0-2%
                                      #       with unstable per-season ROI.
MIN_FAIR_PROB: float = 0.42           # v8.2: raised 0.45 -> 0.50. Aligns with
                                      #       diagnose_v8_calibration finding
                                      #       that v8 is biased -0.125 on the
                                      #       dog slice (fair<0.45). Filtering
                                      #       at 0.50 sidesteps that miscal.
                                      #       Grid search (0.04, 0.07, 0.50) =
                                      #       best stable ROI across 2024-2025.
MIN_MODEL_PROB: float = 0.48          # avoid extreme longshots
MAX_MODEL_PROB: float = 0.72          # avoid extreme chalk (value compression)
KELLY_FRACTION: float = 0.25          # quarter-Kelly to control variance
MAX_DAILY_RISK_UNITS: float = 10.0    # v12 structural cap per slate


# ---------------------------------------------------------------------------
# Bullpen fatigue rules
# ---------------------------------------------------------------------------
BULLPEN_FATIGUE_LOOKBACK_DAYS: int = 3
BULLPEN_PITCHES_FATIGUE_THRESHOLD: int = 35  # cumulative pitches over lookback
BULLPEN_BACKTOBACK_APPEARANCES: int = 2      # consecutive-day appearances = unavailable


# ---------------------------------------------------------------------------
# Early-season shrinkage — linear blend toward league means
# ---------------------------------------------------------------------------
# In the first ~3-4 weeks of a season, team-level rate stats are wildly noisy.
# A team that went 3-for-40 with RISP in its first week has a computed wRC+
# near 40; another team that got hot has wRC+ near 170. Neither reflects
# true talent — they are sample-size artifacts that regress hard. If we feed
# those raw gaps into the model, early-season predictions are driven by
# noise rather than signal.
#
# Shrinkage formula (per-team, applied to each rate stat):
#     weight  = min(sample / STABLE_SAMPLE, 1.0)
#     shrunk  = weight * observed + (1 - weight) * league_mean
#
# At `STABLE_SAMPLE` the estimate is fully observed; below it, the estimate
# linearly blends toward the league mean. This is a light-touch empirical-
# Bayes approach — it does nothing past the stability threshold, so it can
# be left on year-round without distorting established-sample games.
EARLY_SEASON_SHRINKAGE_ENABLED: bool = True   # 2026-04-26 — re-enabled
                                                 # after diagnostic test showed
                                                 # the multi-year SP frame
                                                 # (sc_pitcher) was the actual
                                                 # bug, not shrinkage. Kept on
                                                 # at the LIGHT levels below
                                                 # (SP_STABLE 800, BP_STABLE
                                                 # 8000) for downside
                                                 # protection on small samples
                                                 # without over-compressing
                                                 # legitimate edges.

# Sample thresholds: number of games of accumulated data at which the stat
# is considered fully informative. We derive effective-games from PA counts
# and pitch counts below.
TEAM_STABLE_GAMES: float = 50.0
BULLPEN_STABLE_GAMES: float = 30.0
AVG_PA_PER_GAME: float = 38.0                # ~38 team PA per game
AVG_BULLPEN_PITCHES_PER_GAME: float = 150.0  # ~150 bullpen pitches per team-game

# Starting-pitcher stabilization. Per FanGraphs reliability research, SP xwOBA
# stabilizes around ~70 batters faced ≈ 1500 pitches. Below this threshold
# we blend toward a prior — pitcher-specific prior-season mean if available,
# else the league SP mean. Critical for early-season slates: a Cy Young-caliber
# LHP carrying a small-sample 7.88 ERA after 4 starts must not be priced as
# a true talent 7.88 ERA pitcher. (Triggered the 2026-04-25 BOS@BAL miss.)
SP_STABLE_PITCHES: float = 800.0   # RELAXED 2026-04-26 — was 1500 (FanGraphs
                                    # xwOBA full-stabilization point), but
                                    # 1500 over-shrunk small-sample EDGES
                                    # that v9 was correctly exploiting on
                                    # 04-24 (12/14 hit rate). At 800 a 4-
                                    # start pitcher with 500 pitches gets
                                    # weight=0.625 (62% raw, 38% prior) —
                                    # enough to soften a Crochet-class
                                    # 7.88 ERA but still let real signal
                                    # through. Tune up if BAL-class misses
                                    # come back; tune down if too noisy.
SP_PRIOR_YEAR_MIN_PITCHES: float = 800.0   # min prior-year sample to use that prior

# Bullpen stabilization (v11). Bullpen rate stats stabilize slower than SP
# rates because the unit is the whole reliever staff, with continuous turnover.
# Stable point ≈ 90 team-games of bullpen workload (~13,500 pitches). Below
# this we blend toward a prior — team-level prior-year aggregate if it has
# enough sample, else the league mean. Fixes the 2026-04-25 BAL miss where
# +25 team-games of luck made a bad bullpen look great. K%/BB% stabilize
# faster (~50 games), xERA slower — so we use feature-specific stables below.
BP_STABLE_PITCHES: float = 8000.0    # RELAXED 2026-04-26 — was 13500.
                                      # Same reasoning as SP_STABLE: full
                                      # stabilization isn't the right anchor
                                      # for predict-time shrinkage; we want
                                      # to soften extremes without erasing
                                      # legitimate small-sample signal.
BP_PRIOR_YEAR_MIN_PITCHES: float = 5000.0
BP_STABLE_PITCHES_K_BB: float = 4500.0   # was 7500 — same relaxation

# League-average priors (FanGraphs / Savant baselines). Shrinking toward
# these rather than the raw league aggregate keeps the priors stable across
# years where the scoring environment shifts.
LG_WRC_PLUS: float = 100.0
LG_WOBA: float = 0.315
LG_XWOBA: float = 0.315
LG_K_PCT: float = 22.0
LG_BB_PCT: float = 8.5
LG_HARDHIT_PCT: float = 38.0
LG_BULLPEN_XERA: float = 3.80
LG_BULLPEN_XWOBA: float = 0.315
LG_BULLPEN_K_PCT: float = 23.5      # relievers strike out more than starters
LG_BULLPEN_BB_PCT: float = 9.0      # also walk slightly more
LG_BULLPEN_HARDHIT_PCT: float = 37.5
# League SP averages (slightly better than overall since SPs are above-replacement)
LG_SP_XERA: float = 4.00
LG_SP_XWOBA: float = 0.310
LG_SP_K_BB_PCT: float = 14.0
LG_SP_K_PCT: float = 22.5
LG_SP_BB_PCT: float = 8.0
LG_SP_HARDHIT_PCT: float = 38.0
LG_SP_TTOP3_PENALTY: float = 0.020   # league SPs allow ~20 pts xwOBA more 3rd time


# ---------------------------------------------------------------------------
# Environmental overlays (applied post-model)
# ---------------------------------------------------------------------------
# Ball carry: +3.5 ft per +10°F above a 70°F baseline.
TEMP_BASELINE_F: float = 70.0
CARRY_FT_PER_10F: float = 3.5

# Umpire 'Matthew Effect' — elite starters get zone expansion.
UMP_ACE_ERA_PLUS_THRESHOLD: float = 125.0
UMP_ACE_ZONE_EXPANSION: float = 0.05           # 5% K-rate bump for aces
UMP_DIVISIONAL_DAMPENING: float = 0.5          # halve the expansion in intra-division

# Travel & getaway-day
TRAVEL_TZ_PENALTY: float = -0.10               # 10% offensive haircut after 2+ TZ travel
BACKUP_CATCHER_SP_PENALTY: float = -0.05       # 5% pitcher efficiency haircut


# ---------------------------------------------------------------------------
# Model hyperparameters
# ---------------------------------------------------------------------------
XGB_PARAMS_F5: Dict = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 5,
    "learning_rate": 0.04,
    "n_estimators": 600,
    "min_child_weight": 8,
    "subsample": 0.85,
    "colsample_bytree": 0.80,
    "reg_lambda": 1.5,
    "random_state": 42,
    "tree_method": "hist",
}

XGB_PARAMS_FULL: Dict = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 4,            # shallower — Stage 2 should mostly refine Stage 1
    "learning_rate": 0.03,
    "n_estimators": 500,
    "min_child_weight": 10,
    "subsample": 0.85,
    "colsample_bytree": 0.75,
    "reg_lambda": 2.0,
    "random_state": 42,
    "tree_method": "hist",
}

# Early stopping rounds — applied only when an eval_set is passed in.
# With eval_set None (inner OOF folds, final full-data training), the model
# fits the full n_estimators — that's fine because n_estimators is already
# tuned for the small-data regime.
EARLY_STOPPING_ROUNDS: int = 50


# ---------------------------------------------------------------------------
# Monotonic constraints — enforce domain knowledge on the gradient booster.
# XGBoost accepts a tuple (1, -1, 0) per feature: 1 = non-decreasing in target,
# -1 = non-increasing, 0 = no constraint. This is the formal mechanism that
# prevents the model from learning absurd relationships during overfitting.
# ---------------------------------------------------------------------------
F5_MONOTONE: Dict[str, int] = {
    "sp_xera_gap": 1,              # bigger gap (opp worse) -> higher win prob
    "sp_xwoba_allowed_gap": 1,
    "sp_k_bb_pct_gap": 1,
    "sp_siera_gap": 1,
    "sp_recent_form_gap": 1,
}

FULL_MONOTONE: Dict[str, int] = {
    "f5_model_output": 1,
    "bullpen_siera_gap": 1,
    "team_wrcplus_gap": 1,
    "bullpen_fatigue_gap": -1,     # more fatigued opp bullpen -> we win more
    # v11 bullpen rate stats. Sign convention: positive gap = home advantage.
    # The booster is forced to respect "better bullpen K%, fewer BB%, less
    # hard contact allowed -> higher home_win_prob" — preventing it from
    # picking up a perverse fit on small-sample noise.
    "bullpen_xwoba_gap": 1,
    "bullpen_k_pct_gap": 1,
    "bullpen_bb_pct_gap": 1,
    "bullpen_hardhit_gap": 1,
    # v12 high-leverage bullpen — same monotone direction as the regular
    # bullpen gap (positive = home advantage = higher home_win_prob).
    "hl_bullpen_xera_gap": 1,
    "hl_bullpen_xwoba_gap": 1,
    # Lineup-aware offense (cache v5). Positive gap = home lineup stronger
    # vs. today's opposing SP, which must monotonically raise home_win_prob.
    # These constraints prevent the booster from fitting a perverse
    # relationship during overfitting to a thin-sample early season.
    "lineup_vs_sp_gap": 1,
    "lineup_wrcplus_gap": 1,
    "lineup_hardhit_gap": 1,
    # Savant bat-tracking (cache v6). All gaps are signed so + = home edge.
    # `whiff_rate_gap` is pre-inverted upstream (away - home) for sign
    # consistency — lower whiff = better, so we map it through the same +1
    # direction as the other quality-of-contact signals.
    "team_bat_speed_gap": 1,
    "team_squared_up_swing_gap": 1,
    "team_blast_swing_gap": 1,
    "team_batter_run_value_gap": 1,
    "team_whiff_rate_gap": 1,
    # B-R team form (cache v7). All three are signed + = home edge, so the
    # booster should only fit monotone-increasing relationships to home WP.
    "team_win_pct_gap": 1,
    "team_run_diff_pg_gap": 1,
    "team_pythagorean_gap": 1,
    # Defense (cache v9). Gaps signed so + = home defense > away defense.
    # Better defense -> fewer runs allowed -> higher home win prob.
    "team_oaa_gap": 1,
    "team_frp_gap": 1,
    "team_frv_gap": 1,
    # SP TTOP3 penalty gap (cache v9). Computed via the same `inv` helper as
    # other "lower-is-better" SP stats, so + means home pitcher ages BETTER
    # (smaller penalty than away). Unambiguous home edge -> +1.
    "sp_ttop3_penalty_gap": 1,
    # Weather (humidity/precip/wind direction) and schedule context features
    # (is_day_game/dow_*) are NOT monotone — the relationship varies by park
    # and roof; let the booster learn it.
}


# ---------------------------------------------------------------------------
# Data source paths / API config
# ---------------------------------------------------------------------------
@dataclass
class DataConfig:
    statcast_cache_dir: str = "./data/statcast_cache"
    odds_cache_dir: str = "./data/odds_cache"
    odds_api_base: str = "https://api.the-odds-api.com/v4"
    odds_sport: str = "baseball_mlb"
    odds_regions: str = "us"
    odds_markets: str = "h2h,totals"
    odds_bookmakers: List[str] = field(default_factory=lambda: [
        "draftkings", "fanduel", "betmgm", "caesars", "pinnacle",
    ])


DATA = DataConfig()


# ---------------------------------------------------------------------------
# Live-news enrichment layer (mlb_edge/live_news.py)
# ---------------------------------------------------------------------------
# Master kill switch — if False, live_news.enrich_slate is never called and
# the bet sheet is built off raw model output.  Useful as a debugging
# fallback when an upstream news source breaks.
USE_LIVE_NEWS: bool = True

# Per-rule magnitudes — passed to live_news.enrich_slate(cfg=...).  Tuning
# these is the main lever for "how aggressive should the override layer be";
# they're intentionally conservative on first ship.
LIVE_NEWS_CFG = {
    # SP late scratch: 4pp toward the *other* side + tier demotion (1 step).
    # Picked to be roughly the magnitude of historical scratch-day P&L
    # delta (-3-5pp model vs market on the side losing the SP).
    "SP_SCRATCH_DELTA_PP": 0.04,

    # Bullpen short: 1.5pp toward the side whose pen is rested.
    "BULLPEN_SHORT_DELTA_PP": 0.015,

    # Line movement: weak signal (25-50bps) +0.5pp, strong (>=50bps) +1.5pp
    # *only if the move confirms the model's existing pick side*.
    "LINE_MOVE_WEAK_THRESHOLD_BPS":   25,
    "LINE_MOVE_STRONG_THRESHOLD_BPS": 50,
    "LINE_MOVE_WEAK_DELTA_PP":   0.005,
    "LINE_MOVE_STRONG_DELTA_PP": 0.015,

    # Tier 2 — injury news (mlb_edge/injury_news.py)
    # IL placements are usually known a few days in advance, so the model
    # has had time to react via team aggregates.  But the very-recent
    # placements (last 48-72h) haven't been priced in fully — a small
    # nudge per placement, capped at 4.
    "IL_PLACEMENT_DELTA_PP": 0.012,
    # Lineup scratches detected DURING the day (anchor-vs-current diff)
    # are higher signal than IL placements — we know the player was
    # expected to play but isn't.  Plus a tier-demotion step.
    "LINEUP_SCRATCH_DELTA_PP": 0.015,
}


# ---------------------------------------------------------------------------
# Learned conviction model (mlb_edge/learned_conviction.py)
# ---------------------------------------------------------------------------
# When True, edge_calculator.recommend_slate replaces the per-tier
# TIER_SIZES dict lookup with a logistic-regression-based stake
# multiplier learned from historical bet outcomes.  Falls back to the
# heuristic if `models/conviction.json` is missing or fails to load.
#
# Off by default — flip ON after a few days of audit data confirms the
# learned model produces sensible recommendations alongside the
# heuristic baseline.
USE_LEARNED_CONVICTION: bool = False


# ---------------------------------------------------------------------------
# Phase 1 — Stuff+ / Location+ / Pitching+ feature family.
# ---------------------------------------------------------------------------
# When True, build_pipeline injects 6 new SP_matchup features per game:
#     home_sp_stuff_plus, away_sp_stuff_plus, sp_stuff_plus_gap
#     home_sp_location_plus, away_sp_location_plus, sp_location_plus_gap
# (Pitching+ is intentionally NOT a separate feature — keep Stuff+ and
# Location+ separate so XGBoost can learn the appropriate weighting; a
# single combined Pitching+ would lose the discrimination between command
# failures and stuff/contact failures, see study_2026-05-01.md §9.)
#
# Off by default while we backtest. Flip ON only after CP4 walk-forward
# eval shows ≥0.005 Brier improvement on 2025 hold-out.
USE_STUFF_PLUS: bool = False

# ---------------------------------------------------------------------------
# Phase 4 — Bayesian shrinkage on small-sample features (2026-05-03)
# ---------------------------------------------------------------------------
# When ON, replaces raw gap features with shrunk versions before stage-2
# scoring:
#     shrunk = (n_eff / (n_eff + tau)) * raw + (tau / (n_eff + tau)) * 0
# where n_eff = min(home_n, away_n) per group:
#   - SP gaps (10 feats):       tau=600  (matches F1 floor)
#   - Bullpen gaps (6 feats):   tau=3000 (matches F5 floor)
#   - HL_Bullpen gaps (2 feats): tau=1000
#   - Lineup gaps (3 feats):    tau=9    (slot count)
#
# When n_eff is 0 or NaN, the gap collapses to 0 — directly neutralizing
# the "missing-bullpen-data inflates the gap" failure mode that produced
# the 84.7% / 71.4% / 65.6% inflated cards on the 2026-05-02 slate.
#
# Backtest verdict: DROP on the formal Brier gate (delta +0.0011, CI
# spans zero). However, the failure mode is unverifiable in the historical
# 2025 hold-out (0 archived games match the missing-bp pattern), so
# Phase 4 was approved for SHADOW deployment to measure the live-pipeline
# impact directly. See phase4_bayesian_shrinkage.md.
#
# USE_BAYESIAN_SHRINKAGE controls the production code path. Default OFF.
# USE_BAYESIAN_SHRINKAGE_SHADOW controls the shadow code path that ALSO
# computes a `p_model_shadow_phase4` column for offline comparison without
# affecting picks/edges/tiers. Default ON for live measurement.
USE_BAYESIAN_SHRINKAGE: bool = False
USE_BAYESIAN_SHRINKAGE_SHADOW: bool = True

BAYESIAN_SHRINKAGE_CFG = {
    "groups": [
        # (label, gap_features, home_n_col, away_n_col, tau)
        ("SP", [
            "sp_xera_gap", "sp_siera_gap", "sp_fip_gap", "sp_k_bb_pct_gap",
            "sp_xwoba_allowed_gap", "sp_recent_form_gap", "sp_hardhit_gap",
            "sp_stamina_gap", "sp_velo_drop_gap", "sp_vs_lineup_gap",
        ], "home_sp_n_pitches", "away_sp_n_pitches", 600),
        ("Bullpen", [
            "bullpen_siera_gap", "bullpen_xwoba_gap",
            "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
            "bullpen_hardhit_gap", "bullpen_fatigue_gap",
        ], "home_bullpen_n_pitches", "away_bullpen_n_pitches", 3000),
        ("HL_Bullpen", [
            "hl_bullpen_xera_gap", "hl_bullpen_xwoba_gap",
        ], "home_hl_bullpen_n_pitches", "away_hl_bullpen_n_pitches", 1000),
        ("Lineup", [
            "lineup_wrcplus_gap", "lineup_vs_sp_gap", "lineup_hardhit_gap",
        ], "home_lineup_n_slots", "away_lineup_n_slots", 9),
    ],
    "shadow_log_path": "D:/mlb_edge/phase4_shadow_log.csv",
}

# Configuration knobs for the pitch-quality dataset assembly + training.
# Persisted alongside the model artifact (models/stuff_plus_v1.json) so
# the inference path can reproduce the exact transform used at training.
STUFF_PLUS_CFG = {
    # Years included in training (Stage 1 of pitch-quality model).
    "train_years": (2022, 2023, 2024),
    "validate_year": 2025,
    "test_year": 2026,                   # YTD slice
    # Filter thresholds.
    "min_pitches_per_type": 5_000,       # drop pitch types with < this in train
    "min_sp_pitches": 500,               # filter to pitchers with this many
    # Pitch-type filter list. Position-player pitching gets dropped via the
    # release_speed cutoff; pitchouts and intentional balls go via description.
    "exclude_descriptions": ("pitchout", "intent_ball", "automatic_ball"),
    "min_release_speed": 65.0,           # excludes position-player junk-throws
    # League stats persisted at training time; used to z-score and rescale at
    # inference. Schema: {"mean": float, "sd": float, "scale": int = 100}.
    # The +10 sign-flip baked into the rescale so HIGHER Stuff+ = better SP.
    "rescale": {"center": 100, "scale": 10},
    # Cache locations.
    "cache_dir": "data/pitch_quality",
}
