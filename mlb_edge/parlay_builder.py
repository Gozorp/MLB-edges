"""
mlb_edge/parlay_builder.py
--------------------------
Auto-graded parlay recommendation layer.

Replaces the manual "ask Claude for grades each night" workflow with a
deterministic per-slate scoring pass that ranks every game A through D
and emits suggested 2/3/4-leg parlays into ``parlay_<date>.txt`` next
to the slate diagnostic CSV.

Grading rubric (mirrors the audit framework we'd been doing manually):

    +3   if the game clears every bet-eligibility gate (`why_skipped`
         is empty)
    +2   if PLATINUM-tier and SP edge confirms the pick
    +1   if PLATINUM-tier and SP edge is missing/neutral (post-5/1 fix:
         lineup-only PLATINUM was the failure mode that lost 3 LAD
         picks in a row)
     0   if PLATINUM-tier but SP edge AGAINST the pick (withheld)
    +1   if at least one F-signal fires
    +2   if the SP xERA edge agrees with the pick (>= 0.30 magnitude)
    +1   if same as above but either SP has < 100 PA (small-sample)
    -2   if the SP xERA edge AGAINST the pick (>= 0.30 magnitude)
    -1   if same as above but small-sample
    +1   if Stage 1 and Stage 2 agree to within 10pp on the picked side
    -1   if Stage 1 and Stage 2 disagree by 15-30pp
    -2   if Stage 1 and Stage 2 disagree by 30+pp

    +1   if PQI confirms picked side (|pqi_diff| >= 3.0)
    -1   if PQI contradicts picked side (late-game-degradation flag)
    +1   if team_quality confirms picked side (record/form/offense edge)
    -1   if team_quality contradicts picked side (the LAA-archetype gap:
         picked team materially weaker on win% / form / RPG than opp)

    Cap (Odds-API): when `fair_prob` is missing (Odds API didn't fire),
    score is capped at 0 (C grade) regardless of conviction signals.
    Without market validation we have no external check on overconfidence.

    Cap (Compound-Small-Sample): when BOTH SPs have <60 BF, the SP-edge
    layer is unreliable enough that compounded modifiers can produce
    spurious A grades.  Score is capped at 3 (B+ max) regardless of
    other signals.  Background: 5/1 NYM @ LAA had Scott (6 BF) and
    Urena (50 BF) and graded A; the pick lost.  Now caps to B+.

    Cap (F-signal-required A): when no F-signal fires (no F2 xwOBA gap,
    no F3 swing-take gap), score is capped at 4 (A- max) even if all
    other signals stack.  Background: 5/1 KC @ SEA scored 7 from gates
    + SP edge + PQI + TQ but had no lineup-conviction signal; the pick
    lost to variance.  Pure-pitching A grades are now graded A- to
    reflect the missing lineup confirmation.

    Parlay diversity cap: max 2 anchors of the same conviction profile
    (chalk = market favorite, contrarian = market underdog) per parlay.
    Prevents 3+ chalk or 3+ contrarian tickets from sharing the same
    failure mode.

    Score >=5  -> A     (parlay anchor)
    Score >=4  -> A-    (parlay-worthy)
    Score >=3  -> B+    (stretch leg, max 1 per ticket)
    Score >=2  -> B
    Score >=1  -> B-
    Score >=0  -> C     (DO NOT PARLAY)
    Score <0   -> D     (DO NOT PARLAY)

Public API:
    grade_picks(diag_df, anchor, sp_xstats_df) -> DataFrame with `grade` col
    recommend_parlays(graded_df) -> dict[2/3/4-leg suggestions]
    write_parlay_report(graded_df, slate_date, out_path) -> None
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import post_calibrator as _post_cal

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SP xStats lookup helper
# ---------------------------------------------------------------------------
# Tries FanGraphs first (daily-refresh, doesn't suffer the Savant freeze
# that hit us 4/27 → 5/1).  Falls back to the Savant CSV when FanGraphs
# is unavailable.  Both sources expose the same downstream contract:
# a DataFrame the lookup helper can resolve by pitcher name.
def _load_sp_xstats() -> Optional[pd.DataFrame]:
    """Load the most-recent SP xStats DataFrame from any available source.

    Resolution order:
      1. FanGraphs sp-dashboard (most current, has xERA + xFIP + WAR)
      2. Savant pitcher expected-stats CSV (older fallback)

    Returns None if both sources fail.  Adds a `__source` attribute on
    the returned DataFrame so caller can log which path was used.
    """
    # Try FanGraphs first
    try:
        from . import fangraphs_scraper as _fg
        fg_df = _fg.load_cached("sp-dashboard")
        if fg_df is not None and not fg_df.empty:
            fg_df = _normalize_fg_dashboard(fg_df)
            fg_df.attrs["__source"] = "fangraphs"
            log.info("[sp-xstats] using FanGraphs sp-dashboard (%d pitchers)",
                     len(fg_df))
            return fg_df
    except ImportError:
        pass
    except Exception as e:
        log.warning("[sp-xstats] FanGraphs load failed (%s); trying Savant", e)

    # Fall back to Savant
    import glob
    candidates = sorted(glob.glob("data/savant_extra/savant_expected-stats-pitcher_*.csv"))
    if not candidates:
        log.warning("[sp-xstats] no Savant CSV found and FanGraphs unavailable")
        return None
    try:
        sv_df = pd.read_csv(candidates[-1])
        sv_df.attrs["__source"] = "savant"
        log.info("[sp-xstats] using Savant cache: %s", candidates[-1])
        return sv_df
    except Exception as e:
        log.warning("[sp-xstats] could not load Savant SP xStats: %s", e)
        return None


def _normalize_fg_dashboard(fg_df: pd.DataFrame) -> pd.DataFrame:
    """Map FanGraphs dashboard columns onto the Savant-style schema the
    lookup helper expects.

    Savant's expected-stats-pitcher CSV uses lowercase columns:
        last_name, first_name, player_id, pa, xera, ...
    FanGraphs Dashboard uses:
        Name, Team, W, L, SV, G, GS, IP, K/9, BB/9, ..., ERA, xERA, FIP, xFIP, WAR

    We synthesize the columns the lookup needs:
        column 0 = "name" (full "First Last")
        pa       = batters faced (BF) if present, else IP * 4.3 (BF proxy)
        xera     = xERA (FanGraphs canonical column)
    """
    out = fg_df.copy()
    # Find best column matches case-insensitively
    cols_lower = {c.lower(): c for c in out.columns}
    name_col = cols_lower.get("name") or list(out.columns)[1] if len(out.columns) > 1 else None
    xera_col = cols_lower.get("xera")
    bf_col   = cols_lower.get("bf") or cols_lower.get("tbf")
    ip_col   = cols_lower.get("ip")

    if name_col is None or xera_col is None:
        log.warning("[sp-xstats] FanGraphs dashboard missing required columns "
                    "(name=%r, xera=%r); falling back to Savant", name_col, xera_col)
        return out  # caller may still try; lookup will return None

    # Build the Savant-shaped frame (lookup keys on column 0 for name,
    # 'pa' for sample, 'xera' for the metric).
    new = pd.DataFrame()
    new["name_full"] = out[name_col].astype(str)
    if bf_col:
        new["pa"] = pd.to_numeric(out[bf_col], errors="coerce").fillna(0).astype(int)
    elif ip_col:
        # 4.3 BF/IP is the league-average proxy
        ip = pd.to_numeric(out[ip_col], errors="coerce").fillna(0)
        new["pa"] = (ip * 4.3).round().astype(int)
    else:
        new["pa"] = 0
    new["xera"] = pd.to_numeric(out[xera_col], errors="coerce")
    return new


def _lookup_sp(name: str, sp_df: Optional[pd.DataFrame]) -> Optional[dict]:
    if not name or sp_df is None: return None
    last = name.split()[-1]
    rows = sp_df[sp_df.iloc[:, 0].astype(str).str.contains(last, case=False, na=False)]
    if rows.empty: return None
    first = name.split()[0]
    better = rows[rows.iloc[:, 0].astype(str).str.contains(first, case=False, na=False)]
    if not better.empty: rows = better
    r = rows.iloc[0]
    try:
        return {"name": str(r.iloc[0]), "pa": int(r.get("pa", 0) or 0),
                "xera": float(r.get("xera")) if pd.notna(r.get("xera")) else None}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------
def _score_pick(row: pd.Series, away_sp: Optional[dict],
                 home_sp: Optional[dict]) -> Tuple[int, List[str]]:
    """Return (numeric_score, list_of_reasons).

    `away_sp` and `home_sp` are the dicts returned by `_lookup_sp` (each
    has `xera` and `pa` keys), or None when no name was provided.
    """
    score = 0
    reasons: List[str] = []
    matchup = row.get("matchup", "")
    pick    = row.get("pick", "")
    away, home = (matchup.split(" @ ") + ["", ""])[:2]
    away = away.strip(); home = home.strip()

    why = row.get("why_skipped", "")
    if pd.isna(why): why = ""
    why = str(why)
    # Apply post-bake probability calibration to f5_prob / full_prob.
    # Fitted offline on historical (model_prob, won) pairs to shrink
    # over-confident model outputs back toward empirical hit rate.
    # Pass-through if models/calibration_v1.json is missing (fail-open).
    _cal = _post_cal.get_default()
    if _cal.is_loaded:
        for _col in ("f5_prob", "full_prob", "p_model", "pick_prob"):
            _v = row.get(_col)
            if pd.notna(_v):
                try:
                    _new = _cal.calibrate(float(_v))
                    if _new != float(_v):
                        row = row.copy()
                        row[_col] = _new
                except (TypeError, ValueError):
                    pass

    tier = row.get("tier", "")
    signals_str = row.get("signals", "")
    if pd.isna(signals_str): signals_str = ""
    signals_str = str(signals_str)

    # ---- Compute SP edge alignment FIRST so PLATINUM bonus can be gated ----
    away_sp_xera = away_sp["xera"] if (away_sp and away_sp.get("xera") is not None) else None
    home_sp_xera = home_sp["xera"] if (home_sp and home_sp.get("xera") is not None) else None
    away_sp_pa   = (away_sp.get("pa", 0) if away_sp else 0) or 0
    home_sp_pa   = (home_sp.get("pa", 0) if home_sp else 0) or 0

    sp_edge_status = "missing"   # "agrees" | "against" | "neutral" | "missing"
    sp_edge_pts = 0
    sp_edge_note = ""
    if away_sp_xera is not None and home_sp_xera is not None:
        gap = away_sp_xera - home_sp_xera   # positive = home SP better
        sp_winner = home if gap > 0 else away
        mag = abs(gap)
        # SP SMALL-SAMPLE GUARD: when either SP has fewer than ~100 PA
        # against (~4 GS proxy), the xERA estimate is noisy enough that
        # full +/-2 weighting overstates conviction.  Half-weight to +/-1.
        MIN_STABLE_PA = 100
        min_pa = min(away_sp_pa, home_sp_pa)
        small_sample = min_pa < MIN_STABLE_PA
        full_pts = 1 if small_sample else 2
        ss_note = (f" [small-sample SP, min_pa={min_pa}]"
                   if small_sample else "")
        if mag >= 0.30:
            if sp_winner == pick:
                sp_edge_status = "agrees"; sp_edge_pts = full_pts
                sp_edge_note = (f"SP edge {mag:.2f} xERA agrees with pick "
                                f"(+{full_pts}){ss_note}")
            else:
                sp_edge_status = "against"; sp_edge_pts = -full_pts
                sp_edge_note = (f"SP edge {mag:.2f} xERA AGAINST pick "
                                f"(-{full_pts}){ss_note}")
        else:
            sp_edge_status = "neutral"

    # +3 for clears every gate
    if not why:
        score += 3
        reasons.append("clears every gate (+3)")

    # +2 for PLATINUM that clears — GATED BY SP-EDGE AGREEMENT.
    # Background: PLATINUM tier requires F2+F3 (lineup-driven) to fire
    # simultaneously.  When SP-edge layer doesn't confirm the pick, the
    # PLATINUM call is essentially a pure-lineup bet — the failure mode
    # that produced 3 straight LAD losses (4/28 PLATINUM, 4/29 PLATINUM,
    # 5/1 A-).  Don't double-count the lineup signal without external SP
    # confirmation.
    if tier == "PLATINUM" and not why:
        if sp_edge_status == "agrees":
            score += 2
            reasons.append("PLATINUM tier (+2; SP edge confirms)")
        elif sp_edge_status == "against":
            reasons.append("PLATINUM tier WITHHELD (SP edge against pick — "
                           "lineup-only conviction not rewarded)")
        else:
            # neutral / missing — partial credit only
            score += 1
            reasons.append("PLATINUM tier (+1; SP edge unconfirmed)")

    # +1 for any F-signal
    if signals_str.strip():
        score += 1
        reasons.append("F-signal fires (+1)")

    # Apply pre-computed SP-edge points
    if sp_edge_note:
        score += sp_edge_pts
        reasons.append(sp_edge_note)

    # ----- Pitching Quality Index (PQI) — late-game-degradation signal -----
    # PQI weights SP performance against the team's bullpen, accounting
    # for leverage role, recent fatigue, and projected SP innings.
    # Returns +/- 1 modifier: confirms or contradicts the picked side.
    # Disabled when bullpen data unavailable (sandbox runs without
    # roster fetches).  Gated by a feature flag so the module can be
    # disabled centrally without code edits to the grader.
    pqi_diff_value = row.get("pqi_diff")
    if pd.notna(pqi_diff_value):
        try:
            from . import pitching_quality as _pq
            mod, pqi_note = _pq.pqi_grade_modifier(
                float(pqi_diff_value), pick, home, away
            )
            if mod != 0:
                score += mod
                reasons.append(f"{pqi_note} ({mod:+d})")
        except ImportError:
            pass
        except Exception as e:
            log.debug("PQI modifier failed (non-fatal): %s", e)

    # ----- Team-quality modifier — record / form / offense -----
    # Catches the gap the pitcher-driven model misses: when the picked
    # team is materially weaker on win%, last-10 form, or offensive
    # RPG than the opponent.  Background: 5/4 CHW @ LAA — model picked
    # LAA on Soriano's 0.84 ERA, but CHW (16-18) is a structurally
    # better team than LAA (13-22), and CHW won 5-0 when Soriano had
    # a bad night.  The pitcher-only model couldn't see this gap.
    #
    # Capped at +/-1, same as PQI.  Reads team_quality_diff from the
    # row when set by an upstream pipeline step; otherwise computes
    # in-line via the team_quality module.
    # team_quality_modifier DISABLED 2026-05-08 — historical eval showed
    # the picks it pushed into PLATINUM/A grade went 3-4 (43%), below coinflip.
    # Logging the magnitude for visibility but not contributing to score.
    tq_diff_value = row.get("team_quality_mod")
    if pd.notna(tq_diff_value) and tq_diff_value != 0:
        reasons.append(f"team_quality modifier ({int(tq_diff_value):+d}; DISABLED, score+=0)")

    # ----- LARGE-NEGATIVE-EDGE CAP (2026-05-08) -----
    # Backtested on 144 historical picks: graded picks with edge_pp < -3pp
    # went 5-4 (55%) — slight positive value, so don't cap that lightly.
    # But Vegas-implied disagreement of 8+pp is a much stronger signal
    # and indicates the model may be missing a real factor.  Only cap
    # when edge_pp < -8 (extreme market disagreement).
    edge_pp_for_cap = row.get("edge_pp")
    if pd.notna(edge_pp_for_cap):
        try:
            _e = float(edge_pp_for_cap)
            if _e < -8.0 and score >= 3:
                reasons.append(f"large negative edge ({_e:+.1f}pp) caps grade at C (score {score} -> 2)")
                score = 2
        except (TypeError, ValueError):
            pass

    # Stage 1/2 agreement on picked side
    #
    # 2026-05-10 update: penalty threshold tightened from 0.15 to 0.12 after
    # 5/9 postgame review (postgame/2026-05-09.json) showed NYY @ MIL graded
    # GOLD with delta=0.1290 and lost — the existing 0.15 cutoff let this
    # leg into a parlay anchor. 5/9 also flagged MIN @ CLE (delta=0.1796)
    # which DID trip the old 0.15 rule but the -1 nudge wasn't enough to
    # demote from GOLD; that case is now handled by the existing 0.18 A-cap
    # rule and the broader 0.12 penalty here.  Lowering the threshold
    # converts the new "moderate disagreement" band (0.12–0.15) from no-op
    # to -1, which would have demoted NYY @ MIL from a GOLD parlay leg.
    f5 = row.get("f5_prob")
    full = row.get("full_prob")
    # 2026-05-13: extract p_model as a local so the hard-cap rules below
    # (CAP 2, CAP 3) can reference it without raising NameError.  Earlier
    # caps push committed those references but never pulled the value
    # out of row, which crashed grade_picks and silently suppressed the
    # entire diag CSV rewrite (cap audit found no cap-era data).
    p_model = row.get("p_model")
    if pd.notna(f5) and pd.notna(full):
        f5 = float(f5); full = float(full)
        # Convert both to picked-side prob
        pick_f5 = f5 if pick == home else (1.0 - f5)
        pick_fl = full if pick == home else (1.0 - full)
        disagree = abs(pick_fl - pick_f5)
        if disagree < 0.10:
            score += 1
            reasons.append(f"Stage 1/2 agree (Δ={disagree:.2f}) (+1)")
        elif disagree >= 0.30:
            score -= 2
            reasons.append(f"Stage 1/2 MAJOR disagree (Δ={disagree:.2f}) (-2)")
        elif disagree >= 0.12:
            score -= 1
            reasons.append(f"Stage 1/2 disagree (Δ={disagree:.2f}) (-1)")

    # ----- F-SIGNAL-REQUIRED A CAP -----
    # When no F-signal fires (no F2 xwOBA gap, no F3 swing-take gap),
    # the A grade is "pure pitching wishful thinking" — score 5+ comes
    # entirely from gates(+3), SP edge(+2), PQI(+1), TQ(+1) without any
    # lineup-conviction confirmation.  Background: 5/1 KC @ SEA scored
    # 7 (full stack) but had no F-signal; SEA pick lost to variance.
    # Cap at A- (score 4) when no F-signal — preserves A for cases
    # where lineup/conviction layer also confirms.
    if not signals_str.strip() and score >= 5:
        reasons.append(
            f"no F-signal: pure-pitching A capped at A- (score {score} -> 4)"
        )
        score = 4

    # ----- STAGE 1/2 LARGE-DISAGREEMENT CAP -----
    # The Stage 1/2 disagree modifier (above) only subtracts 1 or 2.  But on
    # 2026-05-07 BOS @ DET we picked DET at PLATINUM A with disagree Δ=0.19
    # (Stage 1 said 45.4%, Stage 2 jumped to 50%) and the pick lost.  The
    # disagreement is a real warning that the F5/full-game models can't
    # agree on the side, which historically correlates with picks losing
    # to bullpen / late-leverage variance.  When disagreement is meaningful
    # (>= 0.18), cap A grades at A- so the leg stops being a parlay anchor.
    if pd.notna(f5) and pd.notna(full):
        try:
            _f5_pick = float(f5) if pick == home else 1.0 - float(f5)
            _fl_pick = float(full) if pick == home else 1.0 - float(full)
            _gap = abs(_fl_pick - _f5_pick)
            if _gap >= 0.18 and score >= 5:
                reasons.append(
                    f"Stage 1/2 large disagreement (Δ={_gap:.2f}) caps A at A- "
                    f"(score {score} -> 4)"
                )
                score = 4
        except (TypeError, ValueError):
            pass

    # ----- NEGATIVE-EDGE A CAP -----
    # When the model picks the side Vegas has priced HIGHER (edge_pp < 0), the
    # public has already priced in the conviction the model is acting on.
    # Historically these "agree-with-the-chalk" A picks are the most expensive
    # parlay-anchor losses because variance still applies but the price isn't
    # rewarding us.  Background: 2026-05-07 TEX @ NYY (edge=-2.8pp) and MIN @
    # WSH (edge=-1.7pp) both graded A and both lost.  Cap A grades at A- when
    # edge is negative beyond a small grace zone, so they no longer anchor a
    # parlay.
    edge_pp_raw = row.get("edge_pp")
    if pd.notna(edge_pp_raw):
        try:
            _edge = float(edge_pp_raw)
            if _edge < -1.0 and score >= 5:
                reasons.append(
                    f"negative edge ({_edge:+.1f}pp) caps A at A- "
                    f"(score {score} -> 4)"
                )
                score = 4
        except (TypeError, ValueError):
            pass

    # ----- COMPOUND-SMALL-SAMPLE CAP -----
    # When BOTH SPs have <60 BF, the SP-edge layer is unreliable enough
    # that the model can compound several +1 modifiers into an A grade
    # purely on noise.  Background: 5/1 NYM @ LAA — Christian Scott had
    # 6 BF (1.1 IP MLB debut), Walbert Urena had 50 BF (2 GS).  Old
    # rules graded LAA at A (score 6); the pick lost.  When both SPs
    # are this thin, cap the score at 3 (B+ max) regardless of other
    # signals — they can't anchor a parlay on this.
    #
    # Threshold 60 BF chosen so the rule fires on Scott+Urena (6+50=
    # both <60) but NOT on combinations where one SP has reasonable
    # sample (e.g., 5/4 BOS Tolle 42 BF + DET Holton 70 BF — only
    # one is tiny, the other has 3+ GS).  60 BF ≈ ~14 IP ≈ 2-3 GS.
    #
    # Both > 0 check: a TBD/no-data SP doesn't fire this rule (it's
    # already handled by the SP-edge "missing" path).  Only fires when
    # both teams have ANNOUNCED a starter and BOTH samples are tiny.
    COMPOUND_SS_BF_THRESHOLD = 60
    if (away_sp_pa > 0 and home_sp_pa > 0 and
            away_sp_pa < COMPOUND_SS_BF_THRESHOLD and
            home_sp_pa < COMPOUND_SS_BF_THRESHOLD):
        if score > 3:
            reasons.append(
                f"compound-small-sample CAP: both SPs <{COMPOUND_SS_BF_THRESHOLD} BF "
                f"(away={away_sp_pa}, home={home_sp_pa}); score capped at B+"
            )
            score = min(score, 3)

    # ========================================================================
    # 2026-05-13: FIVE HARD-CAP RULES validated by docs/data/postgame/*.json
    # ========================================================================
    # Each rule corresponds to a recurring failure mode observed across the
    # 5/8 - 5/11 postgame archive.  All five act as POST-SCORING caps: they
    # fire after the multi-signal scoring above has run and apply a hard
    # tier reduction.  The pre-cap score is snapshotted so grade_picks can
    # write both `grade_score` (post-cap, used by parlay grader) and
    # `pre_cap_score` (post-scoring, pre-hard-cap; used by the weekly
    # backtest to monitor whether the caps have become so restrictive that
    # they are choking out genuine +EV plays).
    pre_cap_score = score

    import re as _re_caps

    # Rule 1 — NEGATIVE-EDGE GOLD HARD CAP
    # Validation: 3-for-3 losses across the postgame archive.
    #   2026-05-09 CHC @ TEX (edge -4.41pp, GOLD CONFIRM, lost 0-6)
    #   2026-05-09 NYY @ MIL (edge -2.00pp, GOLD CONFIRM, lost 3-4)
    #   2026-05-11 NYY @ BAL (edge -4.75pp, GOLD pre-DOWNGRADE, lost 2-3)
    # Distinct from the existing -8pp cap (which only fires at deep negative
    # edges).  ANY negative edge on a GOLD pick (score >= 3 == B+ or higher)
    # collapses to score=1 (B-, DO NOT PARLAY).
    _epp = row.get("edge_pp")
    if pd.notna(_epp):
        try:
            _epp_f = float(_epp)
            if _epp_f < 0 and score >= 3:
                reasons.append(
                    f"[HARD CAP 1] negative-edge GOLD ({_epp_f:+.1f}pp < 0) "
                    f"prevents GOLD confirmation (score {score} -> 1)"
                )
                score = 1
        except (TypeError, ValueError):
            pass

    # Rule 3 — PLATINUM CALIBRATION ARTIFACT HARD CAP
    # Validation: 2-for-2 losses.
    #   2026-05-10 ATL @ LAD (p_model=0.9447, delta=0.4679, lost 2-7)
    #   2026-05-11 SF  @ LAD (p_model=0.9447, delta=0.4679, lost 3-9)
    # Pattern: when model_prob > 0.85 AND Stage 1/2 delta > 0.20, the booster
    # has been pushed past the calibrator's reliable range — almost always a
    # data artifact (often limited Statcast for the picked side's SP).
    # Hard SKIP (score = 0).
    if pd.notna(p_model) and p_model > 0.85 and pd.notna(f5) and pd.notna(full):
        try:
            _p5 = float(f5) if pick == home else 1.0 - float(f5)
            _pf = float(full) if pick == home else 1.0 - float(full)
            _delta = abs(_pf - _p5)
            if _delta > 0.20:
                reasons.append(
                    f"[HARD CAP 3] PLATINUM calibration artifact: p_model {p_model:.3f}>0.85 "
                    f"AND Stage 1/2 delta {_delta:.2f}>0.20 (score {score} -> 0)"
                )
                score = 0
        except (TypeError, ValueError):
            pass

    # Rule 4 — STAGE 1/2 GAP + CONFIDENCE_DOWNGRADE HARD CONTRA-INDICATOR
    # Validation: 3 supporting cases in the archive.
    #   2026-05-09 MIN @ CLE (delta=0.179, conf_dn=True, lost)
    #   2026-05-10 MIN @ CLE (similar pattern, lost)
    #   2026-05-10 PIT @ SF  (F2 exception failed, conf_dn=True, lost)
    # When the two stages disagree AND the pipeline already flagged
    # confidence_downgrade, the bullpen-carry thesis is structurally fragile.
    # Cap at score=1 (B-, DO NOT PARLAY) regardless of F-signal stack.
    _conf_dn_raw = row.get("confidence_downgrade")
    _conf_dn = (str(_conf_dn_raw).strip().lower() in ("true", "1"))
    if _conf_dn and pd.notna(f5) and pd.notna(full):
        try:
            _p5 = float(f5) if pick == home else 1.0 - float(f5)
            _pf = float(full) if pick == home else 1.0 - float(full)
            _delta = abs(_pf - _p5)
            if _delta >= 0.12 and score >= 3:
                reasons.append(
                    f"[HARD CAP 4] Stage 1/2 delta {_delta:.2f} + confidence_downgrade=True "
                    f"(score {score} -> 1)"
                )
                score = 1
        except (TypeError, ValueError):
            pass

    # Rule 5 — F1* SMALL-SAMPLE SP QUARANTINE
    # Validation: rookie/early-career SP blowups.
    #   2026-05-08 NYY @ MIL (Misiorowski rookie, F1*, lost 0-6)
    #   2026-05-09 NYY @ MIL (Schlittler small sample, F1*, lost 3-4)
    #   2026-05-09 CHC @ TEX (Leiter early career, F1*, lost 0-6)
    # When F1_xera_gap is asterisked (indicating thin Statcast sample),
    # it cannot be the sole F-signal supporting a GOLD pick.  Either another
    # lineup signal (F2 / F3) or PQI confirmation must also fire to sustain
    # the tier.  Otherwise cap at B (score=2).
    # 2026-05-13 regex fix: signals_str contains "F1_xera_gap=1.90*" (asterisk
    # at the end of the numeric value), not "F1_xera_gap*".  Original pattern
    # never matched any live diag CSV — SD@MIL 5/13 was the first parlay-tier
    # exposure and the cap fired zero times because of the regex mismatch.
    _f1_star = bool(_re_caps.search(r"F1_xera_gap=[\d.]+\*", signals_str))
    if _f1_star and score >= 3:
        _has_other_signal = (
            ("F2_xwoba_gap=" in signals_str) or
            ("F3_swing_take_gap=" in signals_str) or
            any("PQI confirms" in r for r in reasons)
        )
        if not _has_other_signal:
            reasons.append(
                f"[HARD CAP 5] F1* small-sample SP without other lineup/PQI "
                f"support cannot sustain GOLD (score {score} -> 2)"
            )
            score = 2

    # Rule 2 — F3 > 1000 + HOME-FAV > 65% REQUIRES ELITE OPPOSING SP
    # Validation: 2 cases — one failure that motivated the rule, one
    # OVERRIDE that confirmed Claude's executive layer applied it correctly.
    #   2026-05-10 ATL @ LAD (F3=1774, p_model=0.65+, opp SP not elite, lost 2-7)
    #   2026-05-11 SEA @ HOU (F3=1783, similar pattern, Claude correctly OVERRODE)
    # F3 measures lineup contact quality, not total-game quality — when the
    # opposing SP is genuinely elite (season xERA < 4.0), F3 magnitude alone
    # cannot override the home-favorite heuristic.  Cap at score=3 (B+).
    _m_f3 = _re_caps.search(r"F3_swing_take_gap=([0-9.]+)", signals_str)
    if _m_f3 and pd.notna(p_model) and float(p_model) > 0.65 and score >= 5:
        try:
            _f3_val = float(_m_f3.group(1))
            if _f3_val > 1000:
                opp_sp = home_sp if pick != home else away_sp
                opp_xera = None
                if opp_sp is not None:
                    for key in ("xera", "xERA", "season_xera"):
                        v = opp_sp.get(key)
                        if v is not None:
                            try:
                                opp_xera = float(v)
                                break
                            except (TypeError, ValueError):
                                pass
                # Cap fires unless we can verify the opposing SP is elite
                # (xERA strictly under 4.0).  Missing data conservatively
                # treats the SP as non-elite — better to leave money on
                # the table than chase variance.
                if opp_xera is None or opp_xera >= 4.0:
                    reasons.append(
                        f"[HARD CAP 2] F3={_f3_val:.0f}>1000 + p_model>{0.65:.2f} "
                        f"without elite opposing SP (xERA={opp_xera}) "
                        f"(score {score} -> 3)"
                    )
                    score = 3
        except (TypeError, ValueError):
            pass

    # Rule 6 — EXTREME POSITIVE EDGE HALLUCINATION CAP (2026-05-13)
    # Validation: 3 losses on edge_pp > +23pp in 6 days.
    #   2026-05-08 SEA @ CHW (edge +31.2pp, lost 12-8 the wrong way)
    #   2026-05-08 NYM @ ARI (edge +23pp, lost 1-3)
    #   2026-05-13 PHI @ BOS (edge +31.0pp, A-tier loss 1-3)
    # Pattern: when the isotonic calibrator's upper bucket is sparse,
    # f5_prob/full_prob can drift well past the calibrated range.  A claimed
    # +25pp edge against the closing line is mathematically implausible in
    # MLB markets — closing lines are tight enough that genuine 25pp edges
    # don't exist.  This is the calibrator hallucinating, not the model
    # finding hidden value.  Force SKIP regardless of F-signal stack.
    if pd.notna(_epp):
        try:
            _epp_extreme = float(_epp)
            if _epp_extreme > 25.0:
                reasons.append(
                    f"[HARD CAP 6] edge_pp={_epp_extreme:+.1f}pp>25.0 "
                    f"is calibrator hallucination range, not real value "
                    f"(score {score} -> 0)"
                )
                score = 0
        except (TypeError, ValueError):
            pass

    # Surface pre_cap_score so grade_picks can write it as a separate column.
    # Encoded as a structured tag at the END of reasons so we can parse it
    # back out without changing the function signature.
    if pre_cap_score != score:
        reasons.append(f"[PRE_CAP_SCORE={pre_cap_score}]")

    return score, reasons


def _score_to_grade(score: int) -> str:
    if score >= 5: return "A"
    if score >= 4: return "A-"
    if score >= 3: return "B+"
    if score >= 2: return "B"
    if score >= 1: return "B-"
    if score >= 0: return "C"
    return "D"


def _compute_pqi_for_matchup(matchup: str,
                              away_sp_xera: Optional[float],
                              home_sp_xera: Optional[float],
                              slate_date: Optional[date] = None,
                              ) -> Optional[float]:
    """Compute pqi_diff (home - away) for a matchup string like 'BOS @ DET'.

    Returns None when:
      - slate_date is missing
      - either team's bullpen roster fetch fails
      - the pitching_quality module is unavailable

    Caller should treat None as "no PQI signal for this game" and skip
    the modifier.  This keeps slate runs stable on offline / sandbox
    environments where MLB Stats API rosters may be unreachable.
    """
    if slate_date is None:
        return None
    if "@" not in matchup:
        return None
    try:
        from . import pitching_quality as _pq
    except ImportError:
        return None

    away_abbr, home_abbr = (s.strip() for s in matchup.split("@", 1))
    home_id = _pq.TEAM_ID.get(home_abbr)
    away_id = _pq.TEAM_ID.get(away_abbr)
    if home_id is None or away_id is None:
        log.debug("[pqi] unknown team abbreviation: %s/%s",
                  home_abbr, away_abbr)
        return None

    try:
        diff, _, _ = _pq.pqi_diff(
            home_team=home_abbr, home_team_id=home_id,
            home_sp_xera=home_sp_xera, home_sp_recent_ip=None,
            away_team=away_abbr, away_team_id=away_id,
            away_sp_xera=away_sp_xera, away_sp_recent_ip=None,
            slate_date=slate_date,
        )
        return diff
    except Exception as e:
        log.debug("[pqi] compute failed for %s: %s", matchup, e)
        return None


def grade_picks(diag_df: pd.DataFrame,
                anchor: Optional[Dict[str, dict]] = None,
                sp_df: Optional[pd.DataFrame] = None,
                slate_date: Optional[date] = None) -> pd.DataFrame:
    """Add `grade`, `grade_score`, `grade_reasons` columns to a slate
    diagnostic DataFrame.  `anchor` maps game_pk -> {away_sp_name, home_sp_name}.
    `sp_df` is the SP xStats DataFrame; both default to lookups
    from disk when None.

    When `slate_date` is provided, PQI (Pitching Quality Index) is also
    computed per game and injected as a +/-1 grade modifier (the
    late-game-pitching-degradation signal).  Pass None to skip PQI —
    useful for backtest runs where bullpen roster data isn't archived.
    """
    if sp_df is None:
        sp_df = _load_sp_xstats()
    if anchor is None:
        anchor = {}

    out = diag_df.copy()
    out["grade"] = ""
    out["grade_score"] = 0
    out["grade_reasons"] = ""
    # Pre-cap snapshot: what grade_score WOULD have been before the five
    # 2026-05-13 hard caps fired.  Equal to grade_score when no cap fired.
    # Surfaced in the diag CSV so the weekly backtest can monitor whether
    # the caps are over-restricting (lots of pre_cap_grade == A but
    # grade == D rows that ended up winning).
    out["pre_cap_score"] = 0
    out["pre_cap_grade"] = ""

    # Build matchup -> SP names map from anchor (anchor keys are str(game_pk))
    # We need to also infer game_pk from matchup if not present in diag_df.
    # The diag CSV doesn't carry game_pk, so we rely on the matchup string.
    # The caller can supply a {matchup: {away_sp_name, home_sp_name}} dict
    # in `anchor` if they want explicit control.
    matchup_to_sps: Dict[str, dict] = {}
    if anchor:
        # If keyed by game_pk (str) — caller is responsible for the
        # matchup-key form.  Try to detect via a quick heuristic: if any
        # value contains '@' in its key, use as-is; otherwise we don't have
        # a matchup map.
        for k, v in anchor.items():
            if "@" in str(k):
                matchup_to_sps[str(k)] = v

    for idx, row in out.iterrows():
        matchup = str(row.get("matchup", "")).strip()
        sps = matchup_to_sps.get(matchup, {})
        away_sp_name = sps.get("away_sp_name")
        home_sp_name = sps.get("home_sp_name")
        a = _lookup_sp(away_sp_name, sp_df) if away_sp_name else None
        h = _lookup_sp(home_sp_name, sp_df) if home_sp_name else None

        # Inject pqi_diff into the row before scoring, when slate_date is
        # available.  Skipped silently when the row already has a value
        # (e.g. set by an upstream pipeline step), or when bullpen roster
        # data is unreachable.
        if slate_date is not None and "pqi_diff" not in out.columns:
            out["pqi_diff"] = pd.NA
        if slate_date is not None and pd.isna(out.at[idx, "pqi_diff"]):
            a_xera = a.get("xera") if a else None
            h_xera = h.get("xera") if h else None
            pqi_value = _compute_pqi_for_matchup(
                str(row.get("matchup", "")), a_xera, h_xera, slate_date,
            )
            if pqi_value is not None:
                out.at[idx, "pqi_diff"] = pqi_value

        # Inject team_quality_mod into the row before scoring.  Pulls
        # team season W-L + offensive RPG + last-10 form from MLB Stats
        # API and returns a +/-1 modifier.  Silently skips on network
        # failure or when no signal triggers.
        if slate_date is not None and "team_quality_mod" not in out.columns:
            out["team_quality_mod"] = pd.NA
        if slate_date is not None and pd.isna(out.at[idx, "team_quality_mod"]):
            try:
                from . import team_quality as _tq
                tq_mod, tq_note = _tq.team_quality_modifier_for_matchup(
                    str(row.get("matchup", "")), str(row.get("pick", "")),
                    slate_date,
                )
                out.at[idx, "team_quality_mod"] = tq_mod
                if tq_note:
                    log.debug("[team_quality] %s: %s",
                              row.get("matchup", ""), tq_note)
            except ImportError:
                out.at[idx, "team_quality_mod"] = 0
            except Exception as e:
                log.debug("[team_quality] failed: %s", e)
                out.at[idx, "team_quality_mod"] = 0

        # Refresh the row Series so _score_pick sees both injected values
        row = out.iloc[idx]

        score, reasons = _score_pick(row, a, h)

        # ODDS-API GUARD: when `fair_prob` is missing, the Odds API didn't
        # populate market context for this game.  Without an edge-vs-market
        # check, the model has no external sanity check on its conviction —
        # exactly the failure mode we hit on 5/1 (4-9 record, 0-for-3 on
        # >65% confidence picks).  Cap grade at C so no parlay-eligible
        # tier (A/A-/B+) can be assigned without market validation.
        fp = row.get("fair_prob")
        if pd.isna(fp) or fp in ("", None):
            if score > 0:
                reasons.append("fair_prob missing — Odds API did not fire "
                               "(cap at C; market validation required)")
                score = min(score, 0)

        # Extract the pre-cap score tag (added by _score_pick when any of the
        # five hard caps fired).  This lets the weekly backtest compare what
        # the score WOULD have been without the new caps — the cheapest way
        # to detect over-restriction (caps choking out genuine +EV plays).
        # If no cap fired, pre_cap_score == score.
        import re as _re_gp
        _pre_cap = score
        for _r in reasons:
            _m = _re_gp.match(r"\[PRE_CAP_SCORE=(-?\d+)\]", _r)
            if _m:
                try:
                    _pre_cap = int(_m.group(1))
                except (TypeError, ValueError):
                    pass
                break

        out.at[idx, "grade_score"] = score
        out.at[idx, "pre_cap_score"] = _pre_cap
        out.at[idx, "grade"] = _score_to_grade(score)
        out.at[idx, "pre_cap_grade"] = _score_to_grade(_pre_cap)
        out.at[idx, "grade_reasons"] = " | ".join(reasons)
    return out


# ---------------------------------------------------------------------------
# Parlay assembly
# ---------------------------------------------------------------------------
# Edge-band filter: parlay legs must have |edge_pp| <= MAX_PARLAY_EDGE_PP.
# Picks with extreme model-vs-market disagreement (chalk-fades like the
# 2026-04-30 KC->OAK at -27pp where market thinks OAK 94%, model 67%) are
# high-variance even when audit grade is otherwise OK — exclude them.
MIN_PARLAY_EDGE_PP = -5.0   # tolerable negative edge (mild market fade)
MAX_PARLAY_EDGE_PP = 15.0   # over this = model overconfidence

PARLAY_INCLUDE_GRADES = {"A", "A-", "B+"}
ANCHOR_GRADES = {"A", "A-"}
STRETCH_GRADES = {"B+"}

# Variance-reserve rule: limit anchors of the same conviction profile to
# this max per parlay.  Chalk = picked side is market favorite (fair_prob
# >= 0.50); contrarian = picked side is market underdog (fair_prob < 0.50).
# Without this cap, a 4-leg parlay can be 100% chalk or 100% contrarian
# and share the same failure mode (e.g., a chalk-bombing slate wipes all
# 4 legs simultaneously).  Forcing diversity reduces correlated variance.
MAX_ANCHORS_SAME_PROFILE = 2


def _classify_profile(row: pd.Series) -> str:
    """Classify a pick as 'chalk' or 'contrarian' based on market position.

    chalk        — picked side is the market favorite (fair_prob >= 0.50)
    contrarian   — picked side is the market underdog (fair_prob < 0.50)
    unknown      — fair_prob unavailable (capped C anyway, won't be in parlay)
    """
    fp = row.get("fair_prob")
    if pd.isna(fp) or fp in ("", None):
        return "unknown"
    try:
        return "chalk" if float(fp) >= 0.50 else "contrarian"
    except (TypeError, ValueError):
        return "unknown"


@dataclass
class ParlaySuggestion:
    legs: List[dict]      # [{matchup, pick, p_model, grade}, ...]
    joint_prob: float
    note: str = ""


def recommend_parlays(graded_df: pd.DataFrame) -> Dict[int, ParlaySuggestion]:
    """Return {2: suggestion, 3: suggestion, 4: suggestion} for parlay sizes.

    Uses A/A- picks as anchors; allows up to 1 B+ stretch leg in the 3- and
    4-leg tickets.  Picks are sorted by grade then by p_model magnitude.
    """
    df = graded_df.copy()
    df = df[df["grade"].isin(PARLAY_INCLUDE_GRADES)].copy()

    # Edge-band filter — keep edges in [MIN_PARLAY_EDGE_PP, MAX_PARLAY_EDGE_PP].
    # Negative edges past the floor = chalk-fades (model fading the market);
    # positive edges past the ceiling = model overconfidence.
    if "edge_pp" in df.columns:
        edges = df["edge_pp"].fillna(0).astype(float)
        df = df[(edges >= MIN_PARLAY_EDGE_PP) & (edges <= MAX_PARLAY_EDGE_PP)].copy()

    if df.empty:
        return {n: ParlaySuggestion(legs=[], joint_prob=0.0,
                                     note="no parlay-worthy picks tonight")
                for n in (2, 3, 4)}

    # Classify each pick by conviction profile (chalk vs contrarian).
    # Used by the variance-reserve rule below.
    df["__profile"] = df.apply(_classify_profile, axis=1)

    # Sort: anchors first, then stretches, by grade rank then descending p_model
    grade_rank = {"A": 0, "A-": 1, "B+": 2}
    df["__rank"] = df["grade"].map(grade_rank)
    df = df.sort_values(["__rank", "p_model"], ascending=[True, False]).reset_index(drop=True)

    def joint(rows: pd.DataFrame) -> float:
        p = 1.0
        for _, r in rows.iterrows():
            p *= float(r["p_model"])
        return p

    def _pick_with_profile_cap(pool: pd.DataFrame, n: int,
                                profile_caps: Dict[str, int]) -> pd.DataFrame:
        """Take up to n picks from `pool`, respecting per-profile caps.

        Iterates the (already-sorted) pool top-down, including each pick
        unless its profile is already at the cap.  This produces the
        highest-grade combination that satisfies diversity.
        """
        chosen_rows = []
        used = {p: 0 for p in profile_caps}
        for _, r in pool.iterrows():
            profile = r.get("__profile", "unknown")
            cap = profile_caps.get(profile, len(pool))   # unknown -> uncapped
            if used.get(profile, 0) >= cap:
                continue
            chosen_rows.append(r)
            used[profile] = used.get(profile, 0) + 1
            if len(chosen_rows) >= n:
                break
        return pd.DataFrame(chosen_rows) if chosen_rows else pd.DataFrame()

    # Variance-reserve cap: max MAX_ANCHORS_SAME_PROFILE chalk + same
    # contrarian.  Unknown (no fair_prob) doesn't fire here — those are
    # already capped at C earlier and won't reach this code.
    PROFILE_CAPS = {
        "chalk":      MAX_ANCHORS_SAME_PROFILE,
        "contrarian": MAX_ANCHORS_SAME_PROFILE,
    }

    out: Dict[int, ParlaySuggestion] = {}
    for n in (2, 3, 4):
        anchors_pool = df[df["grade"].isin(ANCHOR_GRADES)]
        anchors = _pick_with_profile_cap(anchors_pool, n, PROFILE_CAPS)
        if len(anchors) >= n:
            chosen = anchors.head(n)
            profile_count = chosen["__profile"].value_counts().to_dict()
            note = (f"{n} anchors at A/A- grade "
                    f"(diversity: {profile_count})")
        else:
            # Need stretch legs to fill the ticket
            stretches_needed = n - len(anchors)
            # Stretch legs also respect profile caps, but counted separately
            # so a chalk-anchor + chalk-stretch is still allowed up to cap
            stretches_pool = df[df["grade"].isin(STRETCH_GRADES)]
            # Adjust caps for stretches based on what anchors already used
            anchor_profiles = anchors["__profile"].value_counts().to_dict() \
                if not anchors.empty else {}
            stretch_caps = {p: max(0, MAX_ANCHORS_SAME_PROFILE - anchor_profiles.get(p, 0))
                            for p in PROFILE_CAPS}
            stretches = _pick_with_profile_cap(stretches_pool, stretches_needed,
                                                 stretch_caps)
            if len(anchors) + len(stretches) < n:
                chosen = pd.concat([anchors, stretches])
                note = (f"only {len(chosen)} parlay-worthy pick(s) survive "
                        f"diversity cap; can't reach {n} legs (max "
                        f"{MAX_ANCHORS_SAME_PROFILE}/profile)")
            else:
                chosen = pd.concat([anchors, stretches])
                note = (f"{len(anchors)} anchor(s) + {len(stretches)} "
                        f"stretch leg(s); diversity-balanced")

        legs = [{"matchup": r["matchup"], "pick": r["pick"],
                 "p_model": float(r["p_model"]), "grade": r["grade"]}
                for _, r in chosen.iterrows()]
        out[n] = ParlaySuggestion(
            legs=legs,
            joint_prob=joint(chosen) if len(chosen) > 0 else 0.0,
            note=note,
        )
    return out


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_parlay_report(graded_df: pd.DataFrame, slate_date: date,
                        out_path: Optional[Path] = None) -> Path:
    """Write a human-readable parlay_<date>.txt report next to the slate."""
    if out_path is None:
        out_path = Path(f"parlay_{slate_date.isoformat()}.txt")

    parlays = recommend_parlays(graded_df)
    n_picks = len(graded_df)
    by_grade = graded_df["grade"].value_counts().to_dict()

    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f"  PARLAY BUILDER — {slate_date.isoformat()}")
    lines.append("=" * 78)
    lines.append(f"  Slate size:  {n_picks} games")
    lines.append(f"  Grade map:   "
                 + "  ".join(f"{g}={by_grade.get(g,0)}"
                             for g in ["A","A-","B+","B","B-","C","D"]))
    lines.append("")

    # Per-grade pick listing
    for grade in ["A", "A-", "B+"]:
        rows = graded_df[graded_df["grade"] == grade]
        if rows.empty: continue
        label = {"A":"GRADE A   (anchor — include in every ticket)",
                 "A-":"GRADE A-  (parlay-worthy)",
                 "B+":"GRADE B+  (stretch — max 1 per ticket)"}[grade]
        lines.append(label)
        for _, r in rows.iterrows():
            tier = r.get("tier","")
            sigs_raw = r.get("signals","")
            sigs = (str(sigs_raw).strip() if pd.notna(sigs_raw) else "") or "(no F-signals)"
            why_raw = r.get("why_skipped","")
            why  = (str(why_raw).strip() if pd.notna(why_raw) else "")
            status = "BET-ELIGIBLE" if not why else "filtered"
            lines.append(f"  {r['matchup']:<14}  {r['pick']:<3}  "
                         f"p={float(r['p_model'])*100:4.1f}%  "
                         f"{tier:<8}  {status}")
            lines.append(f"      reasons: {r['grade_reasons']}")
        lines.append("")

    # Bottom of barrel
    # Mark picks that pass grade but fail the edge filter
    grade_pass = graded_df["grade"].isin(PARLAY_INCLUDE_GRADES)
    if "edge_pp" in graded_df.columns:
        edges = graded_df["edge_pp"].fillna(0).astype(float)
        edge_excluded = grade_pass & ((edges < MIN_PARLAY_EDGE_PP) |
                                       (edges > MAX_PARLAY_EDGE_PP))
        if edge_excluded.any():
            lines.append(f"EDGE-BAND FILTER  ({int(edge_excluded.sum())} games "
                         f"excluded; band = [{MIN_PARLAY_EDGE_PP:+.0f}, "
                         f"{MAX_PARLAY_EDGE_PP:+.0f}]pp)")
            for _, r in graded_df[edge_excluded].iterrows():
                lines.append(f"  {r['matchup']:<14}  {r['pick']:<3}  "
                             f"p={float(r['p_model'])*100:4.1f}%  "
                             f"edge={float(r['edge_pp']):+.1f}pp  "
                             f"grade={r['grade']}")
            lines.append("")

    avoid = graded_df[~graded_df["grade"].isin(PARLAY_INCLUDE_GRADES)]
    if not avoid.empty:
        lines.append(f"DO NOT PARLAY  ({len(avoid)} games)")
        for _, r in avoid.iterrows():
            lines.append(f"  {r['matchup']:<14}  {r['pick']:<3}  "
                         f"p={float(r['p_model'])*100:4.1f}%  "
                         f"grade={r['grade']:<3}")
        lines.append("")

    # Recommended parlays
    lines.append("=" * 78)
    lines.append("  RECOMMENDED PARLAYS")
    lines.append("=" * 78)
    for n in (2, 3, 4):
        s = parlays[n]
        if not s.legs:
            lines.append(f"\n  {n}-LEG: skipped — {s.note}")
            continue
        lines.append(f"\n  {n}-LEG  ({s.note})  joint p ≈ {s.joint_prob*100:.1f}%")
        for leg in s.legs:
            lines.append(f"    {leg['matchup']:<14}  {leg['pick']:<3}  "
                         f"p={leg['p_model']*100:4.1f}%  grade={leg['grade']}")

    lines.append("")
    lines.append("=" * 78)
    lines.append("  GUIDANCE")
    lines.append("=" * 78)
    lines.append("  - 2-leg parlay: safest. Take if both legs are A/A-.")
    lines.append("  - 3-leg: take if at least 2 legs are A/A-.")
    lines.append("  - 4-leg: take only if all 4 legs are A/A-, or 3 anchors + 1 B+.")
    lines.append("  - DO NOT exceed 4 legs even if more A-tier picks exist —")
    lines.append("    parlay variance grows faster than payouts beyond 4.")
    lines.append("  - Skip the slate entirely if fewer than 2 A/A- picks.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote parlay report to %s", out_path)
    return out_path
