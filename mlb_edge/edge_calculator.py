"""
edge_calculator.py
------------------
Turn model probabilities + Vegas odds into actionable bet recommendations.

Pipeline per game:
  1. Devig Vegas odds (Shin) -> fair implied probabilities
  2. Compute EV edge = model_prob - fair_implied
  3. Apply v12-CONVICTION filter based on independent signal convergence
  4. Size with fractional Kelly, clamped by the daily risk cap

The conviction filter is intentionally strict. Backtests have shown that
edge-only betting (ignoring the convergence check) underperforms because
gradient-boosted models surface lots of small, noisy edges. Requiring
multi-signal agreement removes the noise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import (
    CONVICTION,
    KELLY_FRACTION,
    MAX_DAILY_RISK_UNITS,
    MAX_EDGE_PCT,
    MAX_MODEL_PROB,
    MIN_EDGE_PCT,
    MIN_FAIR_PROB,
    MIN_MODEL_PROB,
    SP_WEIGHTS,
    TIER_SIZES,
)
from .market_analysis import shin
from .recursive_weight_update import get_active_weights
from .sp_savant_gate import adjusted_xera_gap

# v5.1 PLATINUM-eligibility threshold on the reliability-weighted xERA gap.
# Raw xera_gap_min (0.75) is the GOLD-tier floor; PLATINUM additionally
# requires adj_xera_gap >= ADJ_XERA_PLATINUM_MIN, where adj_xera_gap is
# raw xera_gap * sp_sample_reliability. This is the v5.1 fix that prevents
# the 2026-04-25 BAL-style blow-up — raw xera_gap=3.18 produced a PLATINUM
# at reliability=0.32 (adj=1.018 < 1.20), and the bet lost 17-1.
ADJ_XERA_PLATINUM_MIN = 1.20


def _f1_scale() -> float:
    """v5.1 recursive penalty multiplier on the F1 raw signal. After a
    PLATINUM blowout, recursive_weight_update reduces sp_xera_gap's stored
    weight; we read that here and divide by the baseline so the effective
    xera_gap a slate sees is its raw value times the (penalized/baseline)
    ratio. On a clean baseline this is exactly 1.0 — a no-op."""
    active = get_active_weights(SP_WEIGHTS)
    base = SP_WEIGHTS["sp_xera_gap"]
    if base <= 0:
        return 1.0
    return float(active.get("sp_xera_gap", base) / base)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------
def american_to_decimal(odds: float) -> float:
    """American to decimal odds (b+1 form where b = profit per $1 staked)."""
    if pd.isna(odds):
        return np.nan
    return 1 + (odds / 100.0 if odds > 0 else 100.0 / (-odds))


def expected_value(prob: float, decimal_odds: float) -> float:
    """EV per $1 risked. Positive = +EV."""
    if pd.isna(prob) or pd.isna(decimal_odds):
        return np.nan
    return prob * (decimal_odds - 1) - (1 - prob)


def kelly_stake(prob: float, decimal_odds: float,
                fraction: float = KELLY_FRACTION,
                max_stake: float = 0.05) -> float:
    """
    Fractional Kelly as share of bankroll. Clamped at max_stake to avoid
    concentration from occasional model probability spikes.
    """
    if pd.isna(prob) or pd.isna(decimal_odds) or decimal_odds <= 1:
        return 0.0
    b = decimal_odds - 1
    raw = (b * prob - (1 - prob)) / b
    if raw <= 0:
        return 0.0
    return float(min(fraction * raw, max_stake))


# ---------------------------------------------------------------------------
# Conviction filter (v12-CONVICTION)
# ---------------------------------------------------------------------------
@dataclass
class ConvictionResult:
    tier: str
    primary_score: int
    signals_fired: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def score_conviction(row: pd.Series) -> ConvictionResult:
    """
    Evaluate the 4 independent signals described in the project memory:
      F1: SP xERA gap      >= CONVICTION.xera_gap_min
      F2: team xwOBA gap   >= CONVICTION.xwoba_gap_min
      F3: swing-take gap   >= CONVICTION.swing_take_gap_min
      F4: pitcher luck     <= CONVICTION.pitcher_luck_max (our SP unlucky)

    Tiering:
      3+ signals fired -> DIAMOND
      2 signals fired  -> PLATINUM
      1 signal AND it's F4 -> GOLD  (luck plays are smaller by default)
      otherwise -> SKIP

    Hard vetoes (added after the 2026-04-24 MIL-vs-Skenes blow-up):
      * F1-negative veto. If the OPPOSING SP is dominant (sp_xera_gap <=
        -xera_gap_min) we SKIP unconditionally. The model's lean for our side
        is most likely wrong — the ace will absorb whatever offensive edge we
        thought we had. Previously this was just a diagnostic note.
      * F4 sample-size gate. ERA - xERA is meaningless on tiny samples (e.g.
        a pitcher returning from injury with 60 IP). Don't fire F4 on a
        starter who has fewer than `sp_n_pitches_min_f4` pitches in the
        point-in-time window.
    """
    signals: List[str] = []
    notes: List[str] = []

    # F1 - SP xERA gap
    # Sample-size gate (added after the 2026-04-25 BOS@BAL miss). xERA on a
    # 4-start sample (~100 pitches) doesn't reflect true talent — Crochet's
    # 7.88 ERA on tiny sample triggered a PLATINUM fade that lost 17-1.
    # Require BOTH starters to clear sp_n_pitches_min_f1 before letting F1
    # fire as a primary conviction signal. Below the threshold, the gap is
    # demoted to a note (still informative for diagnostics, no tier weight).
    home_n_f1 = row.get("home_sp_n_pitches", np.nan)
    away_n_f1 = row.get("away_sp_n_pitches", np.nan)
    n_min_f1 = CONVICTION.sp_n_pitches_min_f1
    f1_sample_ok = (pd.notna(home_n_f1) and home_n_f1 >= n_min_f1 and
                    pd.notna(away_n_f1) and away_n_f1 >= n_min_f1)
    xera_raw = row.get("sp_xera_gap", np.nan)
    f1_scale = _f1_scale()
    xera = xera_raw * f1_scale if pd.notna(xera_raw) else xera_raw
    adj_xera = adjusted_xera_gap(row) * f1_scale
    if pd.notna(xera) and xera >= CONVICTION.xera_gap_min:
        if f1_sample_ok:
            # v5.1 PLATINUM-eligibility gate. Raw xERA gap meeting the minimum
            # is necessary but no longer sufficient to anchor a PLATINUM tier.
            # adj_xera (raw * sp_sample_reliability) must also clear
            # ADJ_XERA_PLATINUM_MIN. Below that floor, F1 still fires for GOLD
            # but is downgraded with a diagnostic note so it cannot combine
            # with another signal to produce a PLATINUM.
            if adj_xera >= ADJ_XERA_PLATINUM_MIN:
                signals.append(f"F1_xera_gap={xera:.2f}")
            else:
                signals.append(f"F1_xera_gap={xera:.2f}*")
                notes.append(
                    f"F1 reliability-weighted ({adj_xera:.2f}) below "
                    f"PLATINUM floor {ADJ_XERA_PLATINUM_MIN:.2f} — GOLD only"
                )
        else:
            notes.append(
                f"F1 suppressed (n_pitches home={home_n_f1!r}, "
                f"away={away_n_f1!r} < {n_min_f1})"
            )
    elif pd.notna(xera) and xera <= -CONVICTION.xera_gap_min:
        if f1_sample_ok:
            # Opposing SP is dominant — kill the bet outright. Don't let F4
            # luck-regression alone qualify it as a GOLD play.
            notes.append(f"F1 negative ({xera:.2f}) — fade side, vetoed")
            return ConvictionResult(tier="SKIP", primary_score=0,
                                    signals_fired=[], notes=notes)
        else:
            # Negative-F1 veto also requires a credible sample. Otherwise we'd
            # SKIP every early-season game where one SP has 4 bad starts.
            notes.append(
                f"F1 negative-veto suppressed (small sample: home={home_n_f1!r}, "
                f"away={away_n_f1!r})"
            )

    # F2 - team xwOBA gap
    xwoba = row.get("team_woba_gap", np.nan)
    if pd.notna(xwoba) and xwoba >= CONVICTION.xwoba_gap_min:
        signals.append(f"F2_xwoba_gap={xwoba:.3f}")

    # F3 - swing/take run value gap
    stake_gap = row.get("swing_take_gap", np.nan)
    if pd.notna(stake_gap) and stake_gap >= CONVICTION.swing_take_gap_min:
        signals.append(f"F3_swing_take_gap={stake_gap:.1f}")

    # F4 — DROPPED 2026-04-26 evening. Signal-meta logistic regression on
    # 7,615 historical games found F4's standalone weight = +0.006 — i.e.
    # statistically indistinguishable from zero. F4's underlying input
    # (`home_sp_luck` / `away_sp_luck`) still feeds the XGBoost main model
    # as one of its 70 features, so its real predictive contribution is
    # preserved. We just stop double-counting it as an explicit conviction
    # signal in the heuristic tier filter, where it was producing noise
    # and inflating tier counts.
    #
    # If you want to re-enable it for diagnostics: uncomment the block
    # below. The original logic is preserved verbatim.
    f4_fired = False
    # our_luck = row.get("home_sp_luck", np.nan)
    # opp_luck = row.get("away_sp_luck", np.nan)
    # our_n = row.get("home_sp_n_pitches", np.nan)
    # opp_n = row.get("away_sp_n_pitches", np.nan)
    # n_min = CONVICTION.sp_n_pitches_min_f4
    # if pd.notna(our_luck) and our_luck >= -CONVICTION.pitcher_luck_max:
    #     if pd.notna(our_n) and our_n >= n_min:
    #         signals.append(f"F4_our_sp_unlucky={our_luck:.2f}")
    #         f4_fired = True
    #     else:
    #         notes.append(f"F4 our-SP suppressed (n_pitches={our_n!r} < {n_min})")
    # if pd.notna(opp_luck) and opp_luck <= CONVICTION.pitcher_luck_max:
    #     if pd.notna(opp_n) and opp_n >= n_min:
    #         signals.append(f"F4_opp_sp_lucky={opp_luck:.2f}")
    #         f4_fired = True
    #     else:
    #         notes.append(f"F4 opp-SP suppressed (n_pitches={opp_n!r} < {n_min})")

    # F5 - bullpen quality (v11). Sign convention after perspective flip:
    # positive bullpen_siera_gap = our bullpen better than opponent's.
    # Sample-size gate: requires both teams' bullpens to have crossed
    # bp_n_pitches_min_f5 pitches (~20 team-games of relief). Below this,
    # April small-sample noise drives extreme values that don't reflect
    # true talent — see 2026-04-25 BAL "+0.68 advantage" that ended in a
    # 9th-inning 10-run meltdown.
    bp_gap = row.get("bullpen_siera_gap", np.nan)
    our_bp_n = row.get("home_bullpen_n_pitches", np.nan)
    opp_bp_n = row.get("away_bullpen_n_pitches", np.nan)
    n_min_f5 = CONVICTION.bp_n_pitches_min_f5
    f5_sample_ok = (pd.notna(our_bp_n) and our_bp_n >= n_min_f5 and
                    pd.notna(opp_bp_n) and opp_bp_n >= n_min_f5)
    bp_demote = False
    bp_veto = False
    if pd.notna(bp_gap):
        if bp_gap >= 0.40 and f5_sample_ok:
            signals.append(f"F5_bullpen_gap=+{bp_gap:.2f}")
        elif bp_gap <= -0.80 and f5_sample_ok:
            # Severe bullpen disadvantage with credible sample — veto.
            # This is the v11 fix for blowouts driven by relief collapses.
            notes.append(f"F5 bullpen veto ({bp_gap:.2f} — our pen significantly worse)")
            bp_veto = True
        elif bp_gap <= -0.50 and f5_sample_ok:
            # Moderate disadvantage — demote tier by one step.
            notes.append(f"F5 bullpen demote ({bp_gap:.2f} — our pen worse)")
            bp_demote = True
        elif not f5_sample_ok and abs(bp_gap) >= 0.40:
            notes.append(
                f"F5 suppressed (bp_n_pitches our={our_bp_n!r}, "
                f"opp={opp_bp_n!r} < {n_min_f5})"
            )

    if bp_veto:
        return ConvictionResult(tier="SKIP", primary_score=0,
                                signals_fired=[], notes=notes)

    primary_score = len([s for s in signals if not s.startswith("F4_") or len(signals) == 1])
    # Count unique signal families for tiering (F1/F2/F3/F4/F5)
    fired_families = {s.split("_")[0] for s in signals}

    if len(fired_families) >= 3:
        tier = "DIAMOND"
    elif len(fired_families) == 2:
        tier = "PLATINUM"
    elif len(fired_families) == 1:
        tier = "GOLD"
    else:
        tier = "SKIP"

    # v5.1: soft-F1 demotion. A starred F1 (raw threshold met, reliability-
    # weighted PLATINUM floor missed) cannot combine with another family to
    # anchor a PLATINUM/DIAMOND. Demote one tier when present.
    soft_f1_present = any(s.startswith("F1_") and s.endswith("*") for s in signals)
    if soft_f1_present:
        tier = {"DIAMOND": "PLATINUM", "PLATINUM": "GOLD",
                "GOLD": "GOLD", "SKIP": "SKIP"}[tier]

    # v11: bullpen-disadvantage demotion (one tier down). Soft penalty for
    # cases where our pick has a worse-but-not-disastrous bullpen.
    if bp_demote:
        tier = {"DIAMOND": "PLATINUM", "PLATINUM": "GOLD",
                "GOLD": "SKIP", "SKIP": "SKIP"}[tier]

    return ConvictionResult(tier=tier,
                            primary_score=len(fired_families),
                            signals_fired=signals,
                            notes=notes)


# ---------------------------------------------------------------------------
# Per-slate recommender
# ---------------------------------------------------------------------------
def recommend_slate(games: pd.DataFrame,
                    odds: pd.DataFrame,
                    bankroll: float = 100.0) -> pd.DataFrame:
    """
    Merge games (with model_prob) + odds and produce a bet sheet.

    games schema needed:
        game_id, game_date, home_team, away_team, model_prob, sp_xera_gap,
        team_woba_gap, swing_take_gap, home_sp_luck, away_sp_luck
    odds schema needed (long form, one row per side):
        commence_time, home_team, away_team, outcome (team name),
        price (American), market='h2h'

    NOTE on the merge key: `games.game_id` holds the MLB Stats API `game_pk`
    (an integer), while `odds.game_id` holds the-odds-api's own UUID/hash
    string. They identify the same real-world game but have NO relationship
    to each other. An earlier revision merged on `game_id` directly, which
    either raised an int/str dtype error or silently produced an all-NaN
    left join. We now match on (home_team_abbr, away_team_abbr, date), the
    same key the historical pipeline (`build_pipeline.merge_games_and_odds`)
    uses end-to-end.
    """
    from .stadiums import normalize_team  # local import — avoid circularity

    h2h = odds[odds["market"] == "h2h"].copy()
    if h2h.empty:
        log.warning("No h2h odds on slate")
        return pd.DataFrame()

    # Vectorized American → decimal. Previously `.apply(american_to_decimal)`
    # per-row; same np.where shape as build_pipeline and odds_f5.
    p = h2h["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    h2h["decimal"] = dec

    # Normalize home/away team names on the odds side so they match `games`'s
    # already-normalized abbreviations. main.run_predict normalizes `outcome`
    # but leaves home_team/away_team as full strings.
    h2h["home_team_abbr"] = h2h["home_team"].apply(normalize_team)
    h2h["away_team_abbr"] = h2h["away_team"].apply(normalize_team)
    # Convert UTC commence_time → ET calendar date. Late-starting West Coast
    # games have commence_time in UTC that crosses into the next day, but the
    # slate's game_date is the ET calendar date — naive .dt.date dropped those.
    h2h["commence_date"] = (pd.to_datetime(h2h["commence_time"], utc=True)
                            .dt.tz_convert("America/New_York").dt.date)

    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]
    pivot = (h2h.pivot_table(index=keys, columns="outcome",
                             values="decimal", aggfunc="median")
             .reset_index())

    g = games.copy()
    # Normalize games side too — slate uses modern codes (CWS, ATH) while
    # stadiums.TEAM_ALIASES maps odds-api names to legacy codes (CHW, OAK).
    g["home_team_abbr"] = g["home_team"].apply(normalize_team)
    g["away_team_abbr"] = g["away_team"].apply(normalize_team)
    g["game_date_only"] = pd.to_datetime(g["game_date"]).dt.date
    merged = g.merge(pivot,
                     left_on=["home_team_abbr", "away_team_abbr", "game_date_only"],
                     right_on=keys, how="left", suffixes=("", "_odds"))
    merged = merged.drop(columns=[c for c in keys
                                  + ["home_team_abbr", "away_team_abbr", "game_date_only"]
                                  if c in merged.columns])

    recs = []
    total_risk = 0.0

    for _, r in merged.sort_values("model_prob", ascending=False).iterrows():
        # Pivot columns use the normalized outcome code (e.g. CHW, OAK), so
        # look up by the normalized home/away team — slate codes (CWS, ATH)
        # would miss the pivot columns otherwise.
        home_dec = r.get(normalize_team(r["home_team"]))
        away_dec = r.get(normalize_team(r["away_team"]))
        if pd.isna(home_dec) or pd.isna(away_dec):
            continue

        # Devig on implied probabilities derived directly from decimal.
        p_home_raw = 1.0 / home_dec
        p_away_raw = 1.0 / away_dec
        p_home_fair, p_away_fair = shin(p_home_raw, p_away_raw)

        # Choose the side where the model favors
        model_p = r["model_prob"]
        if model_p >= 0.5:
            side, dec, fair, p_model = "home", home_dec, p_home_fair, model_p
            team = r["home_team"]
        else:
            side, dec, fair, p_model = "away", away_dec, p_away_fair, 1 - model_p
            team = r["away_team"]

        if not (MIN_MODEL_PROB <= p_model <= MAX_MODEL_PROB):
            continue

        # v8: require market to see us as a realistic-enough side. Bets where
        # `fair < 0.45` (especially < 0.30) had catastrophic backtest ROI —
        # the market already knows those are longshots and we're just paying
        # juice to chase them.
        if pd.isna(fair) or fair < MIN_FAIR_PROB:
            continue

        ev = expected_value(p_model, dec)
        edge = p_model - (fair if pd.notna(fair) else np.nan)

        # v8: require edge to be in the profitable band [5pp, 10pp]. Edges
        # above 10pp are almost always false signals — the backtest shows
        # 46 bets at 20pp+ returned -36.3% ROI. The model is disagreeing
        # with the market where the market is usually right.
        if pd.isna(edge) or edge < MIN_EDGE_PCT or edge > MAX_EDGE_PCT:
            continue

        # v12-CONVICTION gate — build the row-perspective for the chosen side
        perspective = r.copy()
        if side == "away":
            # Flip gap-signed features so "positive = our edge"
            for col in ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                        "sp_siera_gap", "sp_fip_gap",
                        # v11 bullpen features — same sign convention as SP gaps
                        "bullpen_siera_gap", "bullpen_xwoba_gap",
                        "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
                        "bullpen_hardhit_gap", "bullpen_fatigue_gap"]:
                if col in perspective:
                    perspective[col] = -perspective[col]
            # Swap luck perspective
            perspective["home_sp_luck"], perspective["away_sp_luck"] = (
                perspective.get("away_sp_luck"), perspective.get("home_sp_luck"),
            )
            # v10 fix (caught in v11 audit): the F1 and F4 sample-size gates
            # in score_conviction read home_sp_n_pitches/away_sp_n_pitches
            # directly. Without this swap, an away pick is gated against the
            # WRONG team's sample sizes — could falsely fire on small-sample
            # opposing pitchers or falsely suppress on credible-sample our SP.
            perspective["home_sp_n_pitches"], perspective["away_sp_n_pitches"] = (
                perspective.get("away_sp_n_pitches"), perspective.get("home_sp_n_pitches"),
            )
            # v11: swap bullpen sample-size pass-throughs so the F5 sample
            # gate reads "our_bp_n" / "opp_bp_n" correctly.
            perspective["home_bullpen_n_pitches"], perspective["away_bullpen_n_pitches"] = (
                perspective.get("away_bullpen_n_pitches"), perspective.get("home_bullpen_n_pitches"),
            )

        conviction = score_conviction(perspective)

        # USE_LEARNED_CONVICTION: replace the per-tier multiplier with
        # a logistic-regression-based Kelly fraction trained on
        # historical bet outcomes.  See mlb_edge/learned_conviction.py.
        # Falls back to heuristic if the model file is missing.
        try:
            from . import config as _cfg
            from . import learned_conviction as _lc
        except ImportError:
            _cfg = None
            _lc = None
        learned_model = (_lc.get_active() if (_cfg and getattr(_cfg, "USE_LEARNED_CONVICTION", False) and _lc) else None)

        if learned_model is not None:
            # Build a single-row dict shaped like the bt_*.csv schema so
            # _extract_features can parse it consistently.
            lc_row = {
                "prob":    p_model,
                "fair":    fair,
                "signals": ", ".join(conviction.signals_fired),
                "tier":    conviction.tier,
            }
            stake_frac = learned_model.predict_stake_multiplier(
                lc_row, decimal_odds=dec,
                kelly_fraction=KELLY_FRACTION,
                cap=1.0,
            )
            size_mult = float("nan")  # not used in learned mode; keep for logging
        else:
            size_mult = TIER_SIZES[conviction.tier]
            if size_mult == 0:
                continue
            stake_frac = kelly_stake(p_model, dec) * size_mult

        if stake_frac <= 0:
            continue
        stake_units = stake_frac * bankroll

        # Enforce daily risk cap
        if total_risk + stake_units > MAX_DAILY_RISK_UNITS:
            stake_units = max(0.0, MAX_DAILY_RISK_UNITS - total_risk)
            if stake_units <= 0:
                break

        total_risk += stake_units

        recs.append({
            "game_id": r["game_id"],
            "team":    team,
            "side":    side,
            "decimal": round(dec, 3),
            "model_prob": round(p_model, 4),
            "fair_prob":  round(fair, 4) if pd.notna(fair) else np.nan,
            "edge_pp":   round(edge * 100, 2),
            "ev_per_$1": round(ev, 4),
            "tier":      conviction.tier,
            "signals":   ", ".join(conviction.signals_fired),
            "stake_u":   round(stake_units, 2),
        })

    out = pd.DataFrame(recs)
    if out.empty:
        log.info("No bets pass the filter today.")
    else:
        log.info("Total risk: %.2f u on %d bets", total_risk, len(out))
    return out
