"""
main_predict.py
---------------
End-to-end live-prediction orchestrator.

Replaces the older `python -m mlb_edge.main --mode predict` workflow with a
production driver that:
    0. Refreshes Savant leaderboards via savant_scraper (idempotent).
    1. Calls auto_weight_update.run() for yesterday's slate (closes the
       recursive-learning loop without manual CSV uploads).
    2. Pulls live weather, lineups, and bullpen context.
    3. Calls the existing predict pipeline (build_pipeline + model + edge
       calculator), passing through the live context as feature overrides.
    4. Optionally bypasses the zero-bet trigger and emits a structured
       per-game probability table for diagnostic transparency.
    5. Persists picks + audit CSVs in the format the rest of the pipeline
       expects, so tomorrow's auto-update will pick them up.

Usage:
    python -m mlb_edge.main_predict --date 2026-04-27 --bankroll 100
    python -m mlb_edge.main_predict --date 2026-04-27 --diagnostic-table
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import auto_weight_update as awu
from . import build_pipeline as bp
from . import data_ingestion as di
from . import model as md
from . import savant_scraper
from .bullpen_fatigue_blocker import apply_bullpen_ceiling
from .bullpen_tracker import snapshot as bullpen_snapshot
from .edge_calculator import (
    american_to_decimal, expected_value, kelly_stake,
    recommend_slate, score_conviction,
)
from .config import (
    KELLY_FRACTION, MAX_DAILY_RISK_UNITS, MAX_EDGE_PCT, MAX_MODEL_PROB,
    MIN_EDGE_PCT, MIN_FAIR_PROB, MIN_MODEL_PROB, SP_WEIGHTS, TIER_SIZES,
)
from .live_lineups import fetch_slate_meta
from .live_weather import fetch_slate_weather
from .market_analysis import shin
from .sp_savant_gate import gate_sp_features, SP_THIN_SAMPLE_THRESHOLD
from .stadiums import normalize_team

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("mlb_edge.main_predict")


def _atomic_to_csv(df, path):
    """Write a CSV atomically: to <path>.tmp.<pid>, then os.replace().
    The diag CSV at the repo root is read by concurrent jobs/sidecars; a
    plain to_csv() leaves a torn file if two runs overlap or the process
    dies mid-write (the picks_*_diag.csv.corrupt* incidents)."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Live ingestion
# ---------------------------------------------------------------------------
def gather_live_context(slate_date: date) -> dict:
    log.info("[live] fetching slate weather...")
    weather_df = fetch_slate_weather(slate_date.isoformat())
    log.info("[live] fetching confirmed lineups + probable pitchers...")
    lineup_meta = fetch_slate_meta(slate_date.isoformat())
    log.info("[live] computing 72h bullpen workload...")
    bp_snap = bullpen_snapshot(slate_date, persist=True)
    return {"weather": weather_df, "lineups": lineup_meta, "bullpen": bp_snap}


def overlay_live_features(games: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    if games.empty:
        return games
    out = games.copy()
    w = ctx.get("weather")
    if w is not None and not w.empty:
        weather_lookup = w.set_index("team_home")
        for col_target, col_src in (
            ("park_runs_factor", "runs_factor_effective"),
            ("park_hr_factor",   "hr_factor_effective"),
            ("temp_f",           "temp_f"),
            ("wind_mph",         "wind_mph"),
            ("wind_to_cf_mph",   "wind_to_cf_mph"),
            ("precip_prob",      "precip_prob"),
        ):
            if col_src in weather_lookup.columns:
                home_abbrs = out["home_team"].apply(normalize_team)
                vals = home_abbrs.map(weather_lookup[col_src])
                if col_target in out.columns:
                    out[col_target] = vals.fillna(out[col_target])
                else:
                    out[col_target] = vals
    lineups = ctx.get("lineups", [])
    if lineups:
        conf_home = {m.home_abbr: m.home_lineup_confirmed for m in lineups}
        conf_away = {m.away_abbr: m.away_lineup_confirmed for m in lineups}
        out["lineup_confirmed_home"] = out["home_team"].apply(
            lambda t: conf_home.get(normalize_team(t), False))
        out["lineup_confirmed_away"] = out["away_team"].apply(
            lambda t: conf_away.get(normalize_team(t), False))
    return out


# ---------------------------------------------------------------------------
# PENDING_SP_DATA placeholder
# ---------------------------------------------------------------------------
# A row in this state means the model could not produce a real prediction
# because one or both starters lack the Statcast sample needed to populate
# SP features (rookies, openers, just-off-IL, or probable pitcher not yet
# announced when the workflow ran). Surfacing these as their own tier keeps
# the dashboard at the full slate count instead of silently dropping the
# matchup, and the validate step in daily-slate.yml excludes this tier from
# its blank-row denominator since the blanks here are expected.
def _load_model_guardrails() -> dict:
    """Guardrail state fit by tools/model_guardrails.py from graded history
    (calibration ceiling, tier demotions, blind-spot teams). Missing or
    unreadable state degrades to static defaults -- never blocks a slate."""
    try:
        with open(os.path.join("data", "state", "model_guardrails.json"),
                  encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


_GUARDRAILS = _load_model_guardrails()


def _pending_sp_data_row(*, away_abbr: str, home_abbr: str,
                         why_skipped: str,
                         game_pk=None, game_num=None) -> dict:
    return {
        "matchup":              f"{away_abbr} @ {home_abbr}",
        # Per-game identity (2026-07-17): matchup strings collide on
        # doubleheaders; game_pk is THE disambiguator.
        "game_pk":              game_pk,
        "game_num":             game_num,
        "pick":                 "TBD",
        "f5_prob":              None,
        "full_prob":            None,
        "p_model":              None,
        "pick_prob":            None,
        "p_model_shadow_phase4": None,
        "bp_min":               None,
        "fair_prob":            None,
        "edge_pp":              None,
        "ev_per_dollar":        None,
        "tier":                 "PENDING_SP_DATA",
        "signals":              "thin_sp_data",
        "why_skipped":          why_skipped,
        "odds_status":          "pending_sp_data",
    }


def append_unannounced_sp_pending_rows(table: pd.DataFrame,
                                       schedule: list,
                                       slate_date: date) -> pd.DataFrame:
    """Add PENDING_SP_DATA rows for scheduled games that didn't make it
    into `table` because their probable pitcher wasn't announced yet (and
    so build_slate_frame skipped them).
    """
    if not schedule:
        return table
    have_matchups = set(table["matchup"].tolist()) if not table.empty and "matchup" in table.columns else set()
    # Per-game identity (2026-07-17): dedupe scheduled games against the
    # table by game_pk. The old matchup-only check DROPPED a doubleheader
    # game 2 whenever game 1 was already scored — the slate could not even
    # represent two games of the same matchup.
    have_pks = set()
    if not table.empty and "game_pk" in table.columns:
        have_pks = set(pd.to_numeric(table["game_pk"], errors="coerce")
                       .dropna().astype(int).tolist())
    if not table.empty and "game_id" in table.columns:
        have_pks |= set(pd.to_numeric(table["game_id"], errors="coerce")
                        .dropna().astype(int).tolist())
    additions = []
    for g in schedule:
        if g.get("home_sp_id") and g.get("away_sp_id"):
            continue
        home_abbr = normalize_team(g.get("home_team") or "")
        away_abbr = normalize_team(g.get("away_team") or "")
        if not home_abbr or not away_abbr:
            continue
        matchup = f"{away_abbr} @ {home_abbr}"
        pk = g.get("game_pk")
        if pk:
            if int(pk) in have_pks:
                continue
        elif matchup in have_matchups:
            # legacy fallback when the schedule carries no game identity
            continue
        missing_sides = []
        if not g.get("home_sp_id"):
            missing_sides.append(f"{home_abbr} (home)")
        if not g.get("away_sp_id"):
            missing_sides.append(f"{away_abbr} (away)")
        why = (
            f"Probable SP not yet announced for "
            f"{', '.join(missing_sides)}; will fill in next workflow run "
            f"(needs 100+ Statcast pitches once announced to score)"
        )
        additions.append(_pending_sp_data_row(
            away_abbr=away_abbr, home_abbr=home_abbr, why_skipped=why,
            game_pk=pk, game_num=g.get("game_number") or 1,
        ))
        have_matchups.add(matchup)
        if pk:
            have_pks.add(int(pk))
    if not additions:
        return table
    log.info("Appended %d PENDING_SP_DATA rows for unannounced-SP games",
             len(additions))
    add_df = pd.DataFrame(additions)
    if table is None or table.empty:
        return add_df
    return pd.concat([table, add_df], ignore_index=True, sort=False)


# ---------------------------------------------------------------------------
# Diagnostic per-game table (bypasses zero-bet filter)
# ---------------------------------------------------------------------------
def build_diagnostic_table(games: pd.DataFrame,
                           odds_long: Optional[pd.DataFrame],
                           odds_status: str = "unknown") -> pd.DataFrame:
    """Build the picks_<date>_diag.csv table.

    Column semantics (DO NOT REINVERT — eval bug 2026-05-02):
      - `full_prob`  : home-perspective probability from the model.
      - `p_model`    : PICK-perspective probability (= full_prob if pick==home,
                       else 1 - full_prob). Equal to the explicit `pick_prob`
                       column added below.
      - `pick_prob`  : explicit alias of `p_model` so future readers can't
                       confuse perspective. Always pick-perspective.
      - `fair_prob`  : Shin-devigged market prob from the PICK side (NaN if
                       odds unavailable for this matchup).
      - `edge_pp`    : (p_model - fair_prob) * 100, both pick-perspective.

    `odds_status` annotates why fair_prob may be missing:
      - "fetched"     : OddsClient returned data and pivot matched.
      - "unavailable" : OddsClient returned empty (rate-limit/API down/no key).
      - "no_match"    : OddsClient returned data but no row for this matchup.
      - "unknown"     : caller didn't pass a status (legacy).
    """
    if games.empty:
        return pd.DataFrame()

    pivot = None
    if odds_long is not None and not odds_long.empty:
        h2h = odds_long[odds_long["market"] == "h2h"].copy()
        h2h["decimal"] = h2h["price"].apply(american_to_decimal) \
            if "decimal" not in h2h.columns else h2h["decimal"]
        h2h["home_abbr"] = h2h["home_team"].apply(normalize_team)
        h2h["away_abbr"] = h2h["away_team"].apply(normalize_team)
        h2h["commence_date"] = (pd.to_datetime(h2h["commence_time"], utc=True)
                                .dt.tz_convert("America/New_York").dt.date)
        keys = ["home_abbr", "away_abbr", "commence_date"]
        pivot = (h2h.pivot_table(index=keys, columns="outcome",
                                 values="decimal", aggfunc="median")
                 .reset_index())

    rows = []
    for _, r in games.iterrows():
        home_abbr = normalize_team(r["home_team"])
        away_abbr = normalize_team(r["away_team"])

        # 2026-05-10 fix: emit PENDING_SP_DATA rows for games where one or
        # both starters have catastrophically thin Statcast samples (rookies,
        # openers, just-off-IL). Prior to this fix the row still landed in the
        # CSV but with normal pick/tier/edge values that downstream consumers
        # treated as a real recommendation; the SP-savant gate flagged it via
        # signals="sp_savant_gate=THIN_SAMPLE" but the dashboard didn't
        # surface that distinction. Now we route those rows through a
        # dedicated PENDING_SP_DATA tier with the SP name + pitch count in
        # `why_skipped` so users see "model has insufficient data" instead of
        # a misleadingly confident pick.
        h_n = r.get("home_sp_n_pitches", float("nan"))
        a_n = r.get("away_sp_n_pitches", float("nan"))
        # Display the TRUE pitch count (home_sp_n_pitches is NaN'd below 100
        # by pitcher_as_of; the actual count rides in *_actual) so a thin arm
        # shows "85", not "0". The gate decision below still keys off h_n/a_n.
        h_n_act = r.get("home_sp_n_pitches_actual", h_n)
        a_n_act = r.get("away_sp_n_pitches_actual", a_n)
        h_name = (r.get("home_sp_name") or "").strip()
        a_name = (r.get("away_sp_name") or "").strip()
        thin_sides: list[str] = []
        if pd.isna(h_n) or float(h_n) < SP_THIN_SAMPLE_THRESHOLD:
            label = h_name or f"{home_abbr} SP"
            n_disp = "0" if pd.isna(h_n_act) else str(int(h_n_act))
            thin_sides.append(
                f"{label} has only {n_disp} Statcast pitches season-to-date; "
                f"need {SP_THIN_SAMPLE_THRESHOLD}+ to score"
            )
        if pd.isna(a_n) or float(a_n) < SP_THIN_SAMPLE_THRESHOLD:
            label = a_name or f"{away_abbr} SP"
            n_disp = "0" if pd.isna(a_n_act) else str(int(a_n_act))
            thin_sides.append(
                f"{label} has only {n_disp} Statcast pitches season-to-date; "
                f"need {SP_THIN_SAMPLE_THRESHOLD}+ to score"
            )
        # 2026-07-17 (user-directed): thin-SP games are SCORED, not withheld.
        # The old PENDING_SP_DATA placeholder left the row pick-less (TBD)
        # all day — most visibly on DH game 2s started by call-ups. The
        # model's prediction (SP features shrunken/NaN'd by pitcher_as_of,
        # which XGBoost routes through its missing-value branches) is now
        # published WITH the caveat in why_skipped, and the tier is forced
        # to SKIP below so no stake ever rides on a sub-threshold sample.
        thin_sp = bool(thin_sides)

        # Stage 1's F5 probability is exposed by model.predict() as `f5_prob`
        # (see mlb_edge.model.predict line ~557). Earlier drafts of this
        # diagnostic table read `f5_model_prob`, which never existed — every
        # row therefore reported f5_prob=None, masking Stage 1 / Stage 2
        # disagreements. Fixed here so the column shows actual values.
        f5_p = r.get("f5_prob", float("nan"))
        full_p = r.get("model_prob", float("nan"))
        # Phase 4 shadow (home-perspective). NaN when shadow disabled.
        full_p_shadow = r.get("model_prob_shadow_phase4", float("nan"))
        # Phase 4 archetype check helper — bullpen sample sizes captured
        # at predict time so daily shadow eval can identify archetype rows
        # (high-confidence pick + missing-bullpen-data) without re-running
        # the slate frame.
        h_bp = r.get("home_bullpen_n_pitches", float("nan"))
        a_bp = r.get("away_bullpen_n_pitches", float("nan"))
        bp_min = (min(float(h_bp), float(a_bp))
                  if pd.notna(h_bp) and pd.notna(a_bp) else float("nan"))

        home_dec = away_dec = float("nan")
        if pivot is not None:
            match = pivot[(pivot["home_abbr"] == home_abbr)
                          & (pivot["away_abbr"] == away_abbr)]
            if not match.empty:
                home_dec = match.iloc[0].get(home_abbr, float("nan"))
                away_dec = match.iloc[0].get(away_abbr, float("nan"))

        if pd.notna(home_dec) and pd.notna(away_dec):
            p_h_raw = 1.0 / home_dec
            p_a_raw = 1.0 / away_dec
            fair_h, fair_a = shin(p_h_raw, p_a_raw)
            # SANITY CAP (2026-05-09): real MLB games never have devigged
            # Vegas-implied prob outside ~[0.20, 0.80].  Anything outside
            # [0.10, 0.90] indicates stale/mis-parsed odds (e.g. doubleheader
            # collision in the median aggregation, futures market leaking in,
            # or a single-book outlier).  Treat as missing so downstream falls
            # back to the no-market-data path rather than baking an absurd
            # 99% fair_prob into the slate.  Tracked via odds_status.
            if (pd.notna(fair_h) and (fair_h < 0.10 or fair_h > 0.90)) or \
               (pd.notna(fair_a) and (fair_a < 0.10 or fair_a > 0.90)):
                log.warning("Suspicious devigged fair prob for %s @ %s: "
                            "fair_h=%.3f fair_a=%.3f (home_dec=%.3f away_dec=%.3f) "
                            "— treating as missing odds",
                            away_abbr, home_abbr,
                            fair_h if pd.notna(fair_h) else float("nan"),
                            fair_a if pd.notna(fair_a) else float("nan"),
                            home_dec, away_dec)
                fair_h, fair_a = float("nan"), float("nan")
        else:
            fair_h, fair_a = float("nan"), float("nan")

        if pd.notna(full_p) and full_p >= 0.5:
            side, p_model, fair = "home", full_p, fair_h
            picked = home_abbr
        else:
            side, p_model, fair = "away", 1 - full_p if pd.notna(full_p) else float("nan"), fair_a
            picked = away_abbr

        # ---- probability guardrails (2026-07-17 audit; user-directed) ----
        # Applied BEFORE edge/EV/Kelly/tier so every downstream number sees
        # the guarded probability; the raw model output is preserved in the
        # pick_prob_raw column (and full_prob stays untouched for eval).
        p_model_raw = p_model
        guard_sig = []
        if thin_sp and pd.notna(p_model):
            # (7) thin-SP shrink toward the coin, weighted by the thinner
            # side's Statcast sample share (postmortem root cause #4: thin
            # xERA is noise dressed as signal). Floor 0.25 keeps SOME lean.
            _eff = []
            for _na in (h_n_act, a_n_act):
                try:
                    _eff.append(min(1.0, (float(_na) if pd.notna(_na) else 0.0)
                                    / float(SP_THIN_SAMPLE_THRESHOLD)))
                except Exception:
                    _eff.append(0.0)
            _w = max(0.25, min(_eff) if _eff else 0.25)
            p_model = 0.5 + (p_model - 0.5) * _w
            guard_sig.append("thin_sp_shrunk_w=%.2f" % _w)
        _ceil = float(_GUARDRAILS.get("prob_ceiling") or 0.70)
        if pd.notna(p_model) and p_model > _ceil:
            # (3) calibration ceiling: audited buckets above 0.65 ran
            # +10.7pp / +22.8pp hot; the tail has not earned its confidence.
            p_model = _ceil
            guard_sig.append("prob_ceiling_%.2f" % _ceil)

        # Phase 4 shadow — pick-perspective probability under shrinkage.
        # Pick side is locked by the production model (above) so the shadow
        # is the shrinkage model's probability on the SAME pick. That way
        # the daily eval can compare the two head-to-head: did shrinkage
        # change how confident we'd be on production's pick?
        if pd.notna(full_p_shadow):
            p_model_shadow = (full_p_shadow if side == "home"
                              else 1 - full_p_shadow)
        else:
            p_model_shadow = float("nan")

        conv = score_conviction(r if side == "home" else _flip_perspective(r))
        edge = (p_model - fair) if (pd.notna(p_model) and pd.notna(fair)) else float("nan")
        picked_dec = home_dec if side == "home" else away_dec
        ev = expected_value(p_model, picked_dec)

        # Bullpen-strain interaction (the "WHIP-to-OPS collision" the user
        # named).  We can't access per-closer WHIP from this pipeline, so we
        # use opposing high-leverage bullpen xwOBA × our top-lineup xwoba as
        # the multiplicative interaction term.  Higher = greater collision
        # risk (opposing pen bleeds against our top hitters).  Pick-side
        # perspective: if we pick home, our hitters face away_hl_pen; if we
        # pick away, our hitters face home_hl_pen.  See lineup_shape.py for
        # threshold guidance (>0.115 = HIGH collision risk).
        try:
            from .lineup_shape import bullpen_strain_score as _strain
        except ImportError:
            from lineup_shape import bullpen_strain_score as _strain
        if side == "home":
            _opp_pen = r.get("away_hl_bullpen_xwoba")
            _our_top = r.get("home_lineup_xwoba")
        else:
            _opp_pen = r.get("home_hl_bullpen_xwoba")
            _our_top = r.get("away_lineup_xwoba")
        pen_strain_pick_side = _strain(_opp_pen, _our_top)
        # Re-bind on r so the diag row's r.get("pen_strain_pick_side") finds it
        try:
            r["pen_strain_pick_side"] = pen_strain_pick_side
        except Exception:
            pass

        # Kelly sizing recommendations (bankroll fractions).
        # Full Kelly assumes perfect calibration — even a 2pp miscalibration
        # in p_model blows the bankroll up over a long enough sequence. We
        # always cap raw Kelly at 0.25 (i.e. never recommend betting more
        # than 25% of bankroll on a single game even if the math says so).
        # Quarter Kelly (the existing KELLY_FRACTION=0.25 default) is the
        # standard for live betting; eighth Kelly is the conservative
        # variant we recommend for any tier where the model hasn't yet
        # accumulated enough postgame outcomes to validate calibration.
        if (pd.notna(p_model) and pd.notna(picked_dec)
                and picked_dec > 1.0 and 0.0 < p_model < 1.0):
            _b = picked_dec - 1.0
            _kelly_raw = (_b * p_model - (1.0 - p_model)) / _b
            _kelly_raw = max(0.0, _kelly_raw)  # negative = no edge -> no bet
            kelly_full = min(_kelly_raw, 0.25)
            kelly_quarter = 0.25 * _kelly_raw
            kelly_eighth = 0.125 * _kelly_raw
        else:
            kelly_full = kelly_quarter = kelly_eighth = 0.0

        why_skipped = []
        if pd.isna(p_model) or not (MIN_MODEL_PROB <= p_model <= MAX_MODEL_PROB):
            why_skipped.append(f"model_prob {p_model:.3f} outside [{MIN_MODEL_PROB},{MAX_MODEL_PROB}]")
        if pd.notna(fair) and fair < MIN_FAIR_PROB:
            why_skipped.append(f"fair_prob {fair:.3f} < {MIN_FAIR_PROB}")
        if pd.notna(edge) and (edge < MIN_EDGE_PCT or edge > MAX_EDGE_PCT):
            why_skipped.append(f"edge {edge*100:+.2f}pp outside [{MIN_EDGE_PCT*100:.0f},{MAX_EDGE_PCT*100:.0f}]pp")
        if TIER_SIZES.get(conv.tier, 0.0) == 0.0:
            why_skipped.append(f"tier {conv.tier} -> stake_mult=0")

        # ---- tier guardrails (2026-07-17 audit; user-directed) ----
        # Applied to the tier LABEL so stake sizing (TIER_SIZES) and every
        # downstream reader follow automatically. Order: data-driven
        # demotion, then hard caps to SKIP.
        tier_out = conv.tier
        _dem = (_GUARDRAILS.get("tier_demotions") or {}).get(tier_out)
        if _dem and TIER_SIZES.get(tier_out, 0.0) > TIER_SIZES.get(_dem, 0.0):
            # (5) self-demotion: this tier's rolling win rate fell below the
            # GOLD benchmark; it inherits GOLD sizing until it re-earns.
            why_skipped.append("guardrail: %s demoted to %s (rolling win rate "
                               "below benchmark)" % (tier_out, _dem))
            guard_sig.append("tier_demoted_%s_to_%s" % (tier_out, _dem))
            tier_out = _dem
        _blind = set(_GUARDRAILS.get("blindspot_teams") or [])
        if TIER_SIZES.get(tier_out, 0.0) > 0.0 and (home_abbr in _blind or away_abbr in _blind):
            # (4) blind-spot cap: the model calls this team's games right
            # <46% of the time over 25+ graded games — worse than a coin.
            _bt = home_abbr if home_abbr in _blind else away_abbr
            why_skipped.append("guardrail: blind-spot team %s (model <46%% "
                               "accurate in its games) -> no stake" % _bt)
            guard_sig.append("blindspot_%s" % _bt)
            tier_out = "SKIP"
        if TIER_SIZES.get(tier_out, 0.0) > 0.0 and pd.notna(fair) and fair < 0.5:
            # (2) contrarian cap: picks against the market side graded 45.1%
            # (n=153) — postmortem root cause #2, now a hard gate.
            why_skipped.append("guardrail: contrarian cap — pick against the "
                               "market side (fair %.3f) -> no stake" % fair)
            guard_sig.append("contrarian_cap")
            tier_out = "SKIP"
        if thin_sp:
            # Hard stake-safety cap: prediction shown, money withheld.
            why_skipped = thin_sides + why_skipped
            if tier_out != "SKIP":
                why_skipped.append("tier forced SKIP: thin SP Statcast sample")
            tier_out = "SKIP"

        # Per-row odds status: distinguish "API didn't fire" from "API fired
        # but had no match for this matchup" so a downstream reader can tell
        # whether NaN fair_prob is silent failure or genuine absence.
        if pivot is None:
            row_odds_status = odds_status if odds_status != "unknown" else "unavailable"
        elif pd.isna(home_dec) or pd.isna(away_dec):
            row_odds_status = "no_match"
        elif pd.isna(fair_h) or pd.isna(fair_a):
            # Odds matched but Shin devig produced an absurd value (caught by
            # the [0.10, 0.90] sanity cap above).  Tag distinctly so we can
            # monitor how often this fires.
            row_odds_status = "fetched_capped"
        else:
            row_odds_status = "fetched"

        rows.append({
            "matchup": f"{away_abbr} @ {home_abbr}",
            # Per-game identity (2026-07-17): gamePk + gameNumber from the
            # MLB schedule. THE disambiguator for doubleheaders — every
            # matchup-string join in the pipeline has collided on DH days.
            "game_pk": (int(r["game_id"]) if pd.notna(r.get("game_id")) else None),
            "game_num": (int(r["game_num"]) if pd.notna(r.get("game_num")) else None),
            "pick": picked,
            "f5_prob": round(f5_p, 4) if pd.notna(f5_p) else None,
            "full_prob": round(full_p, 4) if pd.notna(full_p) else None,
            # f5_full_delta: absolute gap between Stage 1 (F5) and Stage 2 (FULL)
            # win probabilities.  Perspective-independent (|p_home_f5 - p_home_full|
            # equals |p_away_f5 - p_away_full|).  Large delta = the two stages
            # disagree about who wins, which historically signals bullpen
            # volatility (F5>FULL: dominant SP, shaky bullpen; F5<FULL: weak SP
            # carried by strong relief).  Surfaced here so the dashboard, the
            # Claude executive layer, and downstream calibration scripts can
            # all read it as a first-class signal rather than re-deriving it.
            "f5_full_delta": (round(abs(f5_p - full_p), 4)
                              if pd.notna(f5_p) and pd.notna(full_p) else None),
            "p_model": round(p_model, 4) if pd.notna(p_model) else None,
            # `pick_prob` is an explicit alias of `p_model` (pick-perspective).
            # Added 2026-05-02 after eval scripts confused this column for
            # home-perspective. Future readers: if you see both, they're equal
            # by construction — use whichever name you find more readable.
            "pick_prob": round(p_model, 4) if pd.notna(p_model) else None,
            # Raw model output BEFORE the 2026-07-17 probability guardrails
            # (thin-SP shrink, calibration ceiling). Calibration audits and
            # tools/model_guardrails.py read this so the guards can never
            # mask the drift they were built to detect.
            "pick_prob_raw": round(p_model_raw, 4) if pd.notna(p_model_raw) else None,
            # Phase 4 shadow (pick-perspective). NaN when shadow disabled
            # via USE_BAYESIAN_SHRINKAGE_SHADOW=False or when shrinkage
            # raised an exception. Production picks/edges/tiers do NOT
            # use this column — observability only.
            "p_model_shadow_phase4": (round(p_model_shadow, 4)
                                       if pd.notna(p_model_shadow) else None),
            "bp_min": round(bp_min, 0) if pd.notna(bp_min) else None,
            "fair_prob": round(fair, 4) if pd.notna(fair) else None,
            "edge_pp": round(edge * 100, 2) if pd.notna(edge) else None,
            "ev_per_dollar": round(ev, 4) if pd.notna(ev) else None,
            # Kelly sizing as bankroll fractions. Multiply by your actual
            # bankroll to get the recommended stake dollar amount.
            # Convention: zero when there is no positive edge.
            "kelly_full": round(kelly_full, 4),
            "kelly_quarter": round(kelly_quarter, 4),
            "kelly_eighth": round(kelly_eighth, 4),
            # Umpire effects (v13 features, also fed into the model itself).
            # These come from data/umpire_effects.parquet (rebuilt weekly by
            # the umpire-refresh workflow). Positive ump_k_pct_delta means
            # the plate umpire's strike-zone bias inflates strikeout rate
            # vs. league average; positive ump_bb_pct_delta means the umpire
            # inflates walks (tight zone). Both teams face the same umpire
            # so these are ambient features — surface them here so the
            # dashboard and Claude executive layer can see WHICH umpire is
            # behind a tight/loose pitcher projection.
            "ump_k_pct_delta": (round(float(r.get("ump_k_pct_delta")), 4)
                                if pd.notna(r.get("ump_k_pct_delta")) else None),
            "ump_bb_pct_delta": (round(float(r.get("ump_bb_pct_delta")), 4)
                                 if pd.notna(r.get("ump_bb_pct_delta")) else None),
            # ---- Lineup-shape signals (built 2026-05-12) ----
            # Concentration index = top-3 / bottom-3 mean xwOBA from the
            # per-batter list (see mlb_edge/lineup_shape.py).  Captures
            # top-heavy vs balanced lineup composition that aggregated
            # lineup_xwoba erases.  1.0 = balanced, 1.5+ = top-heavy,
            # 2.0+ = severe star-anchored shape vulnerable to bottom-of-
            # order dead zones.  Both perspectives surfaced so the
            # downstream consumer can pick the relevant side based on
            # the pick.  Currently only HOME perspective is reliably
            # computed in build_pipeline (the model is home-side); away
            # is best-effort via 1-x where the diag has the data.
            "home_lineup_concentration": (
                round(float(r.get("home_lineup_concentration_idx")), 3)
                if pd.notna(r.get("home_lineup_concentration_idx")) else None),
            "away_lineup_concentration": (
                round(float(r.get("away_lineup_concentration_idx")), 3)
                if pd.notna(r.get("away_lineup_concentration_idx")) else None),
            # ---- High-leverage bullpen quality (comparative) ----
            # Already a model feature via hl_bullpen_xwoba_gap; surfaced
            # here so Claude/dashboard can read it directly without
            # recomputing.  Lower (negative) = our bullpen is meaningfully
            # better than theirs; positive = theirs is better.  Range in
            # practice roughly [-0.060, +0.060] xwOBA units.
            "hl_bullpen_xwoba_gap": (
                round(float(r.get("hl_bullpen_xwoba_gap")), 4)
                if pd.notna(r.get("hl_bullpen_xwoba_gap")) else None),
            # ---- Bullpen-strain interaction (the "collision" signal) ----
            # opposing_hl_pen_xwoba × our_top_lineup_xwoba.  Multiplicative
            # interaction term replacing the literal "high-WHIP closer ×
            # high-OPS top-4" framing — we don't expose per-closer WHIP
            # in the diag pipeline so xwOBA stands in.  Higher = greater
            # collision risk (opposing pen bleeds against our top hitters).
            # See lineup_shape.bullpen_strain_score for thresholds.
            "pen_strain_pick_side": (
                round(float(r.get("pen_strain_pick_side")), 4)
                if pd.notna(r.get("pen_strain_pick_side")) else None),
            # ---- Pitcher-K-prop scaffolding (Top Probable Outcomes Phase 1) ----
            # SP K rates (shrunk %), SP names, SP IDs — surfaced so the
            # dashboard can compute pitcher-K prop probabilities client-side
            # and label each prop with the pitcher's name.  K rate flows
            # from point_in_time.pitcher_as_of via build_pipeline.
            "home_sp_k_pct": (round(float(r.get("home_sp_k_pct")), 2)
                              if pd.notna(r.get("home_sp_k_pct")) else None),
            "away_sp_k_pct": (round(float(r.get("away_sp_k_pct")), 2)
                              if pd.notna(r.get("away_sp_k_pct")) else None),
            "home_sp_name": (str(r.get("home_sp_name")).strip()
                             if r.get("home_sp_name") else ""),
            "away_sp_name": (str(r.get("away_sp_name")).strip()
                             if r.get("away_sp_name") else ""),
            "tier": tier_out,
            "signals": ", ".join(list(conv.signals_fired)
                                 + (["thin_sp_data"] if thin_sp else [])
                                 + guard_sig),
            "why_skipped": " | ".join(why_skipped) if why_skipped else "",
            "odds_status": row_odds_status,
        })
    return pd.DataFrame(rows)


def _flip_perspective(r: pd.Series) -> pd.Series:
    out = r.copy()
    flip_cols = ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                 "sp_siera_gap", "sp_fip_gap", "bullpen_siera_gap",
                 "bullpen_xwoba_gap", "bullpen_k_pct_gap",
                 "bullpen_bb_pct_gap", "bullpen_hardhit_gap",
                 "bullpen_fatigue_gap"]
    for c in flip_cols:
        if c in out and pd.notna(out[c]):
            out[c] = -out[c]
    out["home_sp_n_pitches"], out["away_sp_n_pitches"] = (
        out.get("away_sp_n_pitches"), out.get("home_sp_n_pitches"),
    )
    out["home_bullpen_n_pitches"], out["away_bullpen_n_pitches"] = (
        out.get("away_bullpen_n_pitches"), out.get("home_bullpen_n_pitches"),
    )
    out["home_sp_luck"], out["away_sp_luck"] = (
        out.get("away_sp_luck"), out.get("home_sp_luck"),
    )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
# =============================================================
# Locked-pick logic (2026-05-25)
#   When the bake re-runs after first pitch, the pick column
#   should NOT flip mid-game. Capture prior CSV at run start,
#   check MLB status per game, and for any game whose state is
#   past pre-game restore the LOCK_COLUMNS from the prior row.
# =============================================================
# Abbreviation canonicalization (2026-06-20 lock fix).
#   statsapi's hydrated schedule returns CWS/ATH/AZ etc., while the model's
#   diag matchups use CHW/OAK/ARI. Without normalizing, the started-game map
#   keys never matched the diag keys and the lock silently froze 0 games.
_ABBR_CANON = {
    "CWS": "CHW", "CHA": "CHW", "CHN": "CHC",
    "AZ": "ARI", "ATH": "OAK",
    "WSN": "WSH", "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC",
}


def _canon_abbr(x):
    x = str(x or "").strip().upper()
    return _ABBR_CANON.get(x, x)


def _canon_matchup(m):
    parts = [p.strip() for p in str(m or "").split("@")]
    if len(parts) == 2:
        return f"{_canon_abbr(parts[0])} @ {_canon_abbr(parts[1])}"
    return str(m or "").strip()


_LOCK_COLUMNS = (
    "pick", "p_model", "pick_prob",
    "f5_prob", "full_prob", "fair_prob", "edge_pp",
    "grade", "grade_reasons", "grade_score",
    "pre_cap_score", "pre_cap_grade",
    "tier", "signals", "why_skipped",
    "ev_per_dollar", "kelly_full", "kelly_quarter", "kelly_eighth",
)


def _load_prior_picks_for_lock(out_picks_path):
    """Read prior CSV BEFORE any to_csv overwrites it.

    Returns DataFrame or None.
    """
    if not out_picks_path:
        return None
    p = Path(out_picks_path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(p)
    except Exception as e:
        log.warning("[lock] could not read prior CSV %s: %s", p, e)
        return None


def _games_started_map(slate_date):
    """Fetch MLB schedule for the date once.

    Returns {\"{away_abbr} @ {home_abbr}\": True if game past pre-game}.
    abstractGameState == \"Preview\" means scheduled / pre-game; anything
    else (Live / Final) counts as started.
    """
    out = {}
    try:
        import urllib.request as _ur
        import json as _json
        url = (
            "https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&date={slate_date.isoformat()}&hydrate=team"
        )
        with _ur.urlopen(url, timeout=10) as resp:
            j = _json.loads(resp.read().decode("utf-8"))
        for d in j.get("dates", []) or []:
            # Doubleheader-safe (2026-07-17): one started-flag PER GAME in
            # gameNumber order. A single bool per matchup let G1 going Live
            # mark G2 "started" too, which locked G1's pick onto the G2 row.
            for g in sorted(d.get("games", []) or [],
                            key=lambda x: x.get("gameNumber") or 1):
                status = g.get("status") or {}
                state = status.get("abstractGameState", "")
                started = state in ("Live", "Final")
                teams = g.get("teams") or {}
                away = (teams.get("away") or {}).get("team") or {}
                home = (teams.get("home") or {}).get("team") or {}
                a = _canon_abbr(away.get("abbreviation") or "")
                h = _canon_abbr(home.get("abbreviation") or "")
                if a and h:
                    out.setdefault(f"{a} @ {h}", []).append(started)
                if g.get("gamePk"):
                    out["pk::%s" % g["gamePk"]] = started
    except Exception as e:
        log.warning("[lock] failed to fetch MLB schedule: %s", e)
    return out


def _apply_started_game_lock(new_df, prior_df, started_map):
    """In place: restore _LOCK_COLUMNS from prior_df for any row whose
    matchup has started AND whose prior row has a real (non-TBD) pick.

    Returns count of locked rows.
    """
    if prior_df is None or prior_df.empty:
        return 0
    if "matchup" not in new_df.columns or "matchup" not in prior_df.columns:
        return 0
    import re as _re
    # Doubleheader-safe (2026-07-17): pair the nth CSV row for a matchup
    # with the nth game of the day (schedule/gameNumber order) on BOTH
    # sides. The old first-occurrence lookup copied G1's locked pick onto
    # the G2 row the moment G1 started.
    prior_idx = {}
    for _, pr in prior_df.iterrows():
        mk = str(pr.get("matchup", "")).strip()
        if mk:
            prior_idx.setdefault(mk, []).append(pr)
    n_locked = 0
    occ_seen = {}
    for i, row in new_df.iterrows():
        mk = str(row.get("matchup", "")).strip()
        if not mk:
            continue
        occ = occ_seen.get(mk, 0)
        occ_seen[mk] = occ + 1
        # Strip any "(G2 of 3)" / "(G2)" suffix before matching the
        # MLB schedule (schedule keys are bare).
        bare = _re.sub(r"\s*\([^)]*\)\s*$", "", mk).strip()
        bare_canon = _canon_matchup(bare)
        # game_pk is the exact game identity; occurrence order is only the
        # fallback for prior CSVs written before game_pk existed.
        pk = None
        try:
            _pkv = row.get("game_pk")
            if pd.notna(_pkv):
                pk = int(float(_pkv))
        except Exception:
            pk = None
        if pk is not None and ("pk::%d" % pk) in started_map:
            started = bool(started_map["pk::%d" % pk])
        else:
            flags = (started_map.get(bare_canon)
                     or started_map.get(bare)
                     or started_map.get(mk) or [])
            started = bool(flags[occ]) if occ < len(flags) else False
        if not started:
            continue
        prior_rows = prior_idx.get(mk) or []
        prior_row = None
        if pk is not None:
            for _pr in prior_rows:
                try:
                    if pd.notna(_pr.get("game_pk")) and int(float(_pr.get("game_pk"))) == pk:
                        prior_row = _pr
                        break
                except Exception:
                    continue
        if prior_row is None:
            if occ >= len(prior_rows):
                continue
            prior_row = prior_rows[occ]
        # Only lock when the PRIOR pick was a real pick — don't freeze
        # TBD/PENDING; in that case let fresh model output stand.
        prior_pick = str(prior_row.get("pick", "")).strip().upper()
        if prior_pick in ("", "TBD", "NAN", "NONE"):
            continue
        # Copy locked columns from prior row over the fresh row.
        for col in _LOCK_COLUMNS:
            if col in new_df.columns and col in prior_row.index:
                v = prior_row[col]
                if pd.notna(v):
                    new_df.at[i, col] = v
        # Tag in stress_warnings so the lock is visible in audits.
        if "stress_warnings" in new_df.columns:
            sw_raw = new_df.at[i, "stress_warnings"]
            sw = str(sw_raw) if pd.notna(sw_raw) else ""
            if "locked_at_first_pitch" not in sw:
                new_df.at[i, "stress_warnings"] = (
                    f"{sw};locked_at_first_pitch" if sw
                    else "locked_at_first_pitch"
                )
        n_locked += 1
    return n_locked


def run(slate_date: date,
        bankroll: float = 100.0,
        model_path: str = "models/latest.pkl",
        out_picks: Optional[str] = None,
        diagnostic_table: bool = False,
        skip_auto_update: bool = False,
        skip_savant_refresh: bool = False,
        skip_news: bool = False) -> None:
    # Capture prior CSV BEFORE any to_csv overwrites it. Used at the
    # end of the grading pass to freeze picks for games already in
    # progress (avoids mid-game flips when bullpen state changes,
    # 2026-05-25 user request).
    _prior_picks_for_lock = _load_prior_picks_for_lock(out_picks)

    if not skip_savant_refresh:
        log.info("[step 0/5] savant leaderboard refresh")
        try:
            savant_scraper.refresh_all_for_today()
        except Exception as e:
            log.warning("savant refresh failed (continuing with stale data): %s", e)

    if not skip_auto_update:
        log.info("[step 1/5] auto-weight-update for yesterday")
        try:
            awu.run(slate_date - timedelta(days=1))
        except Exception as e:
            log.warning("auto-weight-update failed (continuing): %s", e)

    log.info("[step 2/5] live context (weather + lineups + bullpen)")
    ctx = gather_live_context(slate_date)

    log.info("[step 3/5] build slate + score")
    stage1, stage2 = md.load(model_path)
    # Pull the raw schedule up front so we can reconcile against the scored
    # slate later. Games whose probable SP isn't announced yet are dropped by
    # build_slate_frame (no features to compute), but we still want them on
    # the dashboard as PENDING_SP_DATA placeholders rather than disappearing
    # entirely. See append_unannounced_sp_pending_rows below.
    raw_schedule = di.fetch_schedule_mlb_api(slate_date)
    games = bp.build_slate_frame(slate_date)
    if games.empty:
        log.error("Slate frame empty for %s", slate_date)
        # Even when no game has scoreable SP data, surface every scheduled
        # matchup as PENDING_SP_DATA so the dashboard / validate step still
        # see the full slate count instead of an empty CSV.
        if raw_schedule and out_picks and diagnostic_table:
            empty_table = append_unannounced_sp_pending_rows(
                pd.DataFrame(), raw_schedule, slate_date,
            )
            if not empty_table.empty:
                Path(out_picks).parent.mkdir(parents=True, exist_ok=True)
                _atomic_to_csv(empty_table, out_picks)
                log.info("Wrote PENDING-only diagnostic table to %s "
                         "(%d games)", out_picks, len(empty_table))
        return

    games = overlay_live_features(games, ctx)
    preds = md.predict(stage1, stage2, games)
    preds = gate_sp_features(preds)

    # ------------------------------------------------------------------
    # [step 2.5/5] Bullpen meta sidecar — Phase 1 of the per-reliever
    # projection-model sprint (memory/project_bullpen_model_sprint_plan.md).
    # ------------------------------------------------------------------
    # Writes docs/data/bullpen_meta_<slate_date>.json with top 8 relievers
    # per team + rest_days + recent leverage + fatigue flag.  Best-effort:
    # if the snapshot is empty or the writer fails, the slate keeps
    # running and the dashboard's bullpen card degrades to "no data".
    try:
        teams_on_slate = sorted(set(games["home_team"].dropna().tolist()
                                    + games["away_team"].dropna().tolist()))
        from .bullpen_meta_writer import (write_bullpen_meta,
                                          META_LIST_LOOKBACK_DAYS)
        # Build a DEDICATED wider-lookback snapshot for the display sidecar so the
        # full bullpen shows (the model's ctx["bullpen"] uses a short ~3d window
        # that only surfaces a couple of recently-used arms). This snapshot is
        # display-only and never feeds the frozen model. Falls back to the model
        # snapshot if the wider build fails/empties.
        _meta_snap = ctx.get("bullpen")
        try:
            from . import bullpen_tracker as _bptrack
            _wide = _bptrack.snapshot(slate_date,
                                      lookback_days=META_LIST_LOOKBACK_DAYS,
                                      persist=False)
            _wpl = getattr(_wide, "pitch_log", None)
            if _wide is not None and _wpl is not None and not _wpl.empty:
                _meta_snap = _wide
        except Exception as _e_wide:
            log.warning("[bullpen_meta] wide snapshot failed, using model "
                        "snapshot: %s", _e_wide)
        meta_path = write_bullpen_meta(
            slate_date=slate_date,
            snapshot=_meta_snap,
            teams_on_slate=teams_on_slate,
            out_dir="docs/data",
        )
        if meta_path:
            log.info("[bullpen_meta] sidecar written: %s "
                     "(%d teams on slate)", meta_path, len(teams_on_slate))
        else:
            log.warning("[bullpen_meta] writer returned None - skipping")
    except Exception as _e_bp_meta:
        log.warning("[bullpen_meta] sidecar write failed (continuing): %s",
                    _e_bp_meta)

    # ------------------------------------------------------------------
    # [step 2.6/5] Series-meta sidecar — series-game indicator
    # ------------------------------------------------------------------
    # Writes docs/data/series_meta_<slate_date>.json with each
    # game's "G2 of 3" label so the dashboard can show users
    # which game of a multi-game series each row represents.
    # Eliminates confusion when the same matchup (e.g. TB @ NYY)
    # appears on consecutive days.  Best-effort per Rule 6.
    try:
        from .series_meta_writer import write_series_meta
        sm_path = write_series_meta(slate_date=slate_date,
                                    out_dir="docs/data")
        if sm_path:
            log.info("[series_meta] sidecar written: %s", sm_path)
        else:
            log.warning("[series_meta] writer returned None - skipping")
    except Exception as _e_sm:
        log.warning("[series_meta] sidecar write failed (continuing): %s",
                    _e_sm)

    # ------------------------------------------------------------------
    # Phase 4 — Bayesian shrinkage shadow prediction (2026-05-03)
    # ------------------------------------------------------------------
    # When USE_BAYESIAN_SHRINKAGE_SHADOW=True (default), we score a SECOND
    # copy of the slate frame with gap features shrunk by sample size,
    # and attach the result as `model_prob_shadow_phase4`. Production
    # picks/edges/tiers use the standard `model_prob` — the shadow column
    # is observability only, designed to measure live-pipeline impact of
    # shrinkage on the missing-bullpen-data inflation pattern.
    # Toggle via mlb_edge.config.USE_BAYESIAN_SHRINKAGE_SHADOW.
    try:
        from . import config as _cfg
        if _cfg.USE_BAYESIAN_SHRINKAGE_SHADOW:
            from . import bayesian_shrinkage as _bs
            log.info("[shadow] Phase 4 Bayesian shrinkage — computing shadow predictions")
            games_shadow = _bs.apply_shrinkage(games, in_place=False)
            preds_shadow = md.predict(stage1, stage2, games_shadow)
            preds_shadow = gate_sp_features(preds_shadow)
            # Align by game_id (preferred) or row order if game_id missing
            if "game_id" in preds.columns and "game_id" in preds_shadow.columns:
                shadow_map = dict(zip(preds_shadow["game_id"],
                                      preds_shadow["model_prob"]))
                preds["model_prob_shadow_phase4"] = preds["game_id"].map(shadow_map)
            else:
                preds["model_prob_shadow_phase4"] = preds_shadow["model_prob"].values[:len(preds)]
            diag = _bs.shrinkage_diagnostics(games)
            log.info("[shadow] shrinkage diagnostics: %s",
                     ", ".join(f"{k}: w={v['median_weight']:.2f} n0={v['n_zero_eff']}/{v['n_total']}"
                               for k, v in diag.items()))
    except Exception as e:
        log.warning("[shadow] Phase 4 shadow prediction failed: %s "
                    "(continuing without shadow column)", e)

    # ------------------------------------------------------------------
    # [step 3/5] Odds chain — Kalshi PRIMARY (2026-05-21)
    # ------------------------------------------------------------------
    # User directive on 2026-05-21: the Odds API subscription is cancelled.
    # Promote Kalshi (CFTC-regulated US prediction market, no-vig binary
    # contracts where two YES probs sum to ~1.00 structurally) from
    # fallback to primary for moneyline (h2h).  Keep the Odds API and ESPN
    # as fallbacks in case the Kalshi feed degrades or the Odds API
    # subscription is reactivated.
    #
    # Order: Kalshi (primary) -> Odds API (fallback if key set) -> ESPN
    #        (last-resort HTML scrape) -> empty.
    #
    # IMPORTANT scope note: Kalshi only carries moneyline contracts.
    # Totals (O/U) and F5 markets still depend on the Odds API via
    # main_totals.py / main_f5.py.  When the Odds API key is unavailable,
    # those pipelines surface clear ODDS_API_KEY_MISSING log lines (see
    # live_totals.py + live_f5.py header notes); a follow-up commit will
    # convert them to emit pred_runs/pred_f5 without market columns.
    #
    # odds_status values (kept stable for downstream consumers):
    #   "fetched"          — Kalshi primary returned rows
    #   "fetched_capped"   — set in build_diagnostic_table when a row had
    #                        Kalshi coverage but the cap fired
    #   "kalshi_empty"     — Kalshi primary empty; one of the fallbacks
    #                        populated odds_long (suffix appended)
    #   "all_empty"        — every source returned empty; diag ships with
    #                        NaN fair_prob / edge_pp / EV
    # Suffixes "+oddsapi_fallback" / "+espn_fallback" record which fallback
    # rescued the slate.
    # Per Architecture-Session Pre-Flight Prompt v1.0 Rule 6 — every
    # source call is best-effort with logged exceptions.
    def _try_fallback(label, fetch_fn):
        try:
            df = fetch_fn(slate_date)
            if df is not None and not df.empty:
                df["outcome"] = df["outcome"].apply(normalize_team)
                log.info("[odds] %s populated %d odds rows for %d games",
                         label, len(df), len(df) // 2)
                return df
            log.info("[odds] %s returned empty", label)
        except Exception as e:
            log.warning("[odds] %s raised: %s", label, e)
        return None

    def _try_backfill(label, backfill_fn, current):
        try:
            before = len(current)
            updated = backfill_fn(current, slate_date)
            if "outcome" in updated.columns:
                updated["outcome"] = updated["outcome"].apply(normalize_team)
            added = len(updated) - before
            if added > 0:
                log.info("[odds] %s backfilled %d additional rows on top "
                         "of primary", label, added)
            return updated
        except Exception as e:
            log.warning("[odds] %s backfill raised: %s", label, e)
            return current

    def _try_oddsapi():
        """Best-effort Odds API fetch; returns DataFrame or None.
        Wraps the previous primary-path logic into the same shape as the
        other source fetchers so the chain stays uniform."""
        try:
            client = di.OddsClient()
            if not client.api_key:
                log.warning("[odds] OddsAPI fallback unavailable: "
                            "ODDS_API_KEY not set (subscription cancelled)")
                return None
            df = client.current_lines()
            if df is None or df.empty:
                log.info("[odds] OddsAPI fallback returned empty "
                         "(rate-limit / quota exhausted / API outage)")
                return None
            df["outcome"] = df["outcome"].apply(normalize_team)
            log.info("[odds] OddsAPI fallback populated %d odds rows for "
                     "%d games", len(df), len(df) // 2)
            return df
        except Exception as e:
            log.warning("[odds] OddsAPI fallback raised: %s", e)
            return None

    odds_long = pd.DataFrame()
    odds_status = "fetched"

    # Primary: Kalshi.
    from . import kalshi_odds as _ko
    from . import odds_fallback as _of
    kal_df = _try_fallback("Kalshi primary", _ko.fetch_kalshi_mlb_odds)
    if kal_df is not None:
        odds_long = kal_df
    else:
        odds_status = "kalshi_empty"

    # Fallback 1: Odds API (only fires if Kalshi primary was empty).
    if odds_long.empty:
        api_df = _try_oddsapi()
        if api_df is not None:
            odds_long = api_df
            odds_status = f"{odds_status}+oddsapi_fallback"

    # Fallback 2: ESPN scraping (only fires if both above were empty).
    if odds_long.empty:
        espn_df = _try_fallback("ESPN fallback", _of.fetch_espn_mlb_odds)
        if espn_df is not None:
            odds_long = espn_df
            odds_status = f"{odds_status}+espn_fallback"

    # Backfill: when primary (Kalshi) succeeded but didn't cover the full
    # slate, fill missing games from the other two sources.
    if not odds_long.empty and odds_status == "fetched":
        # Odds API backfill — skipped silently when ODDS_API_KEY is unset
        # (the backfill_missing_odds helper in odds_fallback handles ESPN;
        # for OddsAPI we'd need a separate helper, deferred for now).
        odds_long = _try_backfill("ESPN", _of.backfill_missing_odds,
                                  odds_long)

    if odds_long.empty:
        odds_status = "all_empty"
        log.error("[odds] all sources (Kalshi, OddsAPI, ESPN) returned "
                  "empty - slate ships without fair_prob / edge_pp / EV")

        # ------------------------------------------------------------------
    # [step 3.5/5] Live-news enrichment layer (Tier 0 + Tier 1)
    # ------------------------------------------------------------------
    # Applies SP-late-scratch detection, ump bias, bullpen-short flag, and
    # line-movement signal to the scored slate. Configurable via
    # config.USE_LIVE_NEWS / config.LIVE_NEWS_CFG. Audit log is written
    # alongside the picks CSV.
    news_audit = pd.DataFrame()
    try:
        from . import config as _cfg
        from . import live_news
        if _cfg.USE_LIVE_NEWS and not skip_news:
            log.info("[step 3.5/5] live-news enrichment")
            bp_workload = (ctx.get("bullpen").workload_by_team
                           if ctx.get("bullpen") is not None else None)
            preds, news_audit = live_news.enrich_slate(
                preds, slate_date,
                odds_long=odds_long,
                bullpen_workload=bp_workload,
                cfg=_cfg.LIVE_NEWS_CFG,
            )
            if out_picks:
                # Write audit alongside slate CSV.
                live_news.write_audit_log(news_audit, slate_date)
        elif skip_news:
            log.info("[step 3.5/5] live-news enrichment skipped (--no-news)")
    except Exception as e:
        log.warning("[step 3.5/5] live-news enrichment failed: %s "
                    "(continuing with raw model output)", e)

    log.info("[step 4/5] edge calculation")
    if diagnostic_table:
        table = build_diagnostic_table(preds, odds_long, odds_status=odds_status)
        # Reconcile against the original schedule. Games whose probable SP
        # wasn't announced when the workflow fired were never in `preds`, so
        # they're missing from `table`. Add a PENDING_SP_DATA row per missing
        # matchup so the dashboard sees one row per scheduled game and the
        # validate step in daily-slate.yml can excuse expected blanks.
        table = append_unannounced_sp_pending_rows(table, raw_schedule, slate_date)
        if not table.empty:
            # ----------------------------------------------------------
            # Stress-test annotation (2026-05-03) — observability v1.
            # Adds `stress_warnings` (semicolon-joined) + `confidence_downgrade`
            # bool columns to the diag CSV per row. Production tier/stake
            # are NOT changed by this layer.
            # ----------------------------------------------------------
            try:
                from . import stress_test as _st
                stress_cols = []
                for _, drow in table.iterrows():
                    matchup = drow.get("matchup", "")
                    away_abbr, _, home_abbr = matchup.partition(" @ ")
                    pred_row = preds[
                        (preds["home_team"].apply(normalize_team) == home_abbr) &
                        (preds["away_team"].apply(normalize_team) == away_abbr)
                    ]
                    if pred_row.empty:
                        stress_cols.append(("", False)); continue
                    g = pred_row.iloc[0]
                    pick_side = "home" if g.get("model_prob", 0) >= 0.5 else "away"
                    pick_team = home_abbr if pick_side == "home" else away_abbr
                    opp_team = away_abbr if pick_side == "home" else home_abbr
                    nrow = None
                    if not news_audit.empty:
                        m = news_audit[news_audit["matchup"] == matchup]
                        if not m.empty:
                            nrow = m.iloc[0].to_dict()
                    bp_state = {
                        "home_bullpen_n_pitches": g.get("home_bullpen_n_pitches"),
                        "away_bullpen_n_pitches": g.get("away_bullpen_n_pitches"),
                        "bullpen_fatigue_gap": g.get("bullpen_fatigue_gap"),
                    }
                    weather = {
                        "wind_out_mph": g.get("wind_out_mph"),
                        "park_hr_factor": g.get("park_hr_factor"),
                    }
                    # Bug-fix 2026-05-08: surface thin_sp_sample as a stress
                    # warning whenever the SP-Savant gate fired THIN_SAMPLE
                    # for this row (signals column will contain that token).
                    # We compute this independently of audit_pick so it
                    # surfaces even when fair_prob/edge_pp is NaN (no odds).
                    extra_warnings: list[str] = []
                    sigs = str(drow.get("signals") or "")
                    if "sp_savant_gate=THIN_SAMPLE" in sigs:
                        extra_warnings.append("thin_sp_sample")
                    edge_pp = drow.get("edge_pp")
                    if edge_pp is None or pd.isna(edge_pp):
                        warn_str = ";".join(extra_warnings)
                        downgrade = bool(extra_warnings)
                        stress_cols.append((warn_str, downgrade)); continue
                    res = _st.audit_pick(
                        edge_pp=float(edge_pp),
                        tier=str(drow.get("tier", "")),
                        pick_team=pick_team, opp_team=opp_team,
                        pick_side=pick_side, target_date=slate_date,
                        bp_state=bp_state, weather=weather, news_row=nrow,
                    )
                    merged_warnings = list(res.vulnerabilities) + extra_warnings
                    stress_cols.append((";".join(merged_warnings),
                                        bool(res.confidence_downgrade) or bool(extra_warnings)))
                table["stress_warnings"] = [s[0] for s in stress_cols]
                table["confidence_downgrade"] = [s[1] for s in stress_cols]
            except Exception as e:
                log.warning("[stress_test] annotation failed: %s "
                            "(continuing without stress columns)", e)

            # ----------------------------------------------------------
            # Monte Carlo PA simulator — SHADOW MODE (Phase 1, 2026-05-23)
            # ----------------------------------------------------------
            # Adds pred_winp_mc + pred_runs_mc columns to the diag CSV.
            # XGBoost predictions (p_model, full_prob, etc.) are unchanged;
            # this is observability only. See mlb_edge/monte_carlo.py for
            # the simulator and mlb_edge/player_rates.py for the per-player
            # outcome-rate derivation. Best-effort per Rule 6 — any failure
            # leaves the columns as empty strings and the rest of the diag
            # CSV ships normally.
            try:
                from . import monte_carlo as _mc
                from .stadiums import get_stadium as _get_stadium
                # Build matchup -> GameMeta lookup from the lineup snapshot
                # already captured in `ctx`. Skip rows whose lineups aren't
                # confirmed yet OR have <9 batters posted.
                _mc_lineups = ctx.get("lineups") or []
                _mc_meta_by_matchup = {}
                for _m in _mc_lineups:
                    try:
                        _h = normalize_team(getattr(_m, "home_abbr", "") or "")
                        _a = normalize_team(getattr(_m, "away_abbr", "") or "")
                        if _h and _a:
                            _mc_meta_by_matchup[f"{_a} @ {_h}"] = _m
                    except Exception:
                        continue

                winp_mc_col = []
                runs_mc_col = []
                _mc_t0 = pd.Timestamp.utcnow()
                _mc_n_ok = 0
                _mc_n_skip = 0
                for _, _drow in table.iterrows():
                    _matchup = _drow.get("matchup", "") or ""
                    _meta = _mc_meta_by_matchup.get(_matchup)
                    if _meta is None:
                        winp_mc_col.append(""); runs_mc_col.append("")
                        _mc_n_skip += 1
                        continue
                    try:
                        _home_pids = [int(s.batter_id) for s in
                                      (getattr(_meta, "home_lineup", []) or [])
                                      if getattr(s, "batter_id", None)]
                        _away_pids = [int(s.batter_id) for s in
                                      (getattr(_meta, "away_lineup", []) or [])
                                      if getattr(s, "batter_id", None)]
                        _home_sp_id = getattr(_meta, "home_sp_id", None)
                        _away_sp_id = getattr(_meta, "away_sp_id", None)
                        if (len(_home_pids) < 9 or len(_away_pids) < 9
                                or not _home_sp_id or not _away_sp_id):
                            winp_mc_col.append(""); runs_mc_col.append("")
                            _mc_n_skip += 1
                            continue
                        # Park factor from stadium table (home team).
                        _away_abbr, _, _home_abbr = _matchup.partition(" @ ")
                        _stadium = _get_stadium(_home_abbr)
                        _park_runs = float(_stadium.get("runs", 100))
                        # SP rates from the preds row we already scored.
                        _pred_row = preds[
                            (preds["home_team"].apply(normalize_team) == _home_abbr) &
                            (preds["away_team"].apply(normalize_team) == _away_abbr)
                        ]
                        _h_k = _h_bb = _h_xw = None
                        _a_k = _a_bb = _a_xw = None
                        if not _pred_row.empty:
                            _g = _pred_row.iloc[0]
                            _h_k = _g.get("home_sp_k_pct")
                            _h_bb = _g.get("home_sp_bb_pct")
                            _h_xw = _g.get("home_sp_xwoba_allowed")
                            _a_k = _g.get("away_sp_k_pct")
                            _a_bb = _g.get("away_sp_bb_pct")
                            _a_xw = _g.get("away_sp_xwoba_allowed")
                        _ump_k = _drow.get("ump_k_pct_delta")
                        _ump_k_f = float(_ump_k) if pd.notna(_ump_k) else 0.0
                        _mc_result = _mc.simulate_slate_row(
                            date=slate_date.isoformat(),
                            home_team=_home_abbr, away_team=_away_abbr,
                            home_lineup_ids=_home_pids,
                            away_lineup_ids=_away_pids,
                            home_sp_id=int(_home_sp_id),
                            away_sp_id=int(_away_sp_id),
                            home_sp_k_pct=_h_k, home_sp_bb_pct=_h_bb,
                            home_sp_xwoba=_h_xw,
                            away_sp_k_pct=_a_k, away_sp_bb_pct=_a_bb,
                            away_sp_xwoba=_a_xw,
                            park_runs_factor=_park_runs,
                            ump_k_pct_delta=_ump_k_f,
                            n_simulations=10000,
                            rng_seed=42,  # deterministic for daily reproducibility
                        )
                        if _mc_result.get("n_simulations", 0) > 0:
                            # Convert to pick-perspective: pred_winp_mc is the
                            # MC win-prob for the *picked* team. The diag row
                            # already knows whether we picked home or away
                            # (look at `pick` column == home_abbr means home).
                            _pick = _drow.get("pick", "")
                            _winp = (_mc_result["home_winp"] if _pick == _home_abbr
                                     else _mc_result["away_winp"])
                            winp_mc_col.append(round(float(_winp), 4))
                            runs_mc_col.append(round(
                                float(_mc_result["mean_total_runs"]), 2))
                            _mc_n_ok += 1
                        else:
                            winp_mc_col.append(""); runs_mc_col.append("")
                            _mc_n_skip += 1
                    except Exception as _e_inner:
                        log.warning("[mc] simulate_slate_row failed for %s: %s",
                                    _matchup, _e_inner)
                        winp_mc_col.append(""); runs_mc_col.append("")
                        _mc_n_skip += 1
                table["pred_winp_mc"] = winp_mc_col
                table["pred_runs_mc"] = runs_mc_col
                _mc_elapsed = (pd.Timestamp.utcnow() - _mc_t0).total_seconds()
                log.info("[mc] shadow predictions: %d ok / %d skip in %.1fs",
                         _mc_n_ok, _mc_n_skip, _mc_elapsed)
            except Exception as e:
                log.warning("[mc] shadow simulator failed: %s "
                            "(continuing without MC columns)", e)
                # Best-effort: still populate empty columns so the schema
                # is stable for downstream consumers.
                if "pred_winp_mc" not in table.columns:
                    table["pred_winp_mc"] = ""
                if "pred_runs_mc" not in table.columns:
                    table["pred_runs_mc"] = ""

            # ----------------------------------------------------------
            # SOFT CAP 6.5 — calibration-suspect edge band [+18, +25]pp
            # (2026-05-14).  HARD CAP 6 fires at edge > +25pp on the
            # premise that the isotonic calibrator hallucinates in the
            # upper-tail bucket.  The +18 to +24pp band is the most
            # likely place the calibration breakdown extends downward
            # — too few losses (n=3) to justify a hard SKIP, but enough
            # asymmetric-downside risk to warrant a half-Kelly damping.
            # Surface as a stress_warnings flag so the human dashboard
            # sees the caution, and halve all three Kelly fractions so
            # standalone-bet exposure is cut even though the grade and
            # parlay-eligibility filters are unchanged (parlays already
            # exclude edge > +15pp anyway).  Cap audit can track hit
            # rate of this band separately over the next 30 picks; if
            # the band hits >=50%, the soft damping is too cautious
            # and can be relaxed.
            try:
                if "edge_pp" in table.columns:
                    for idx, _r in table.iterrows():
                        ep = _r.get("edge_pp")
                        if ep is None or pd.isna(ep):
                            continue
                        try:
                            ep_f = float(ep)
                        except (TypeError, ValueError):
                            continue
                        if 18.0 < ep_f <= 25.0:
                            for _col in ("kelly_full", "kelly_quarter",
                                          "kelly_eighth"):
                                if _col in table.columns:
                                    _v = table.at[idx, _col]
                                    if pd.notna(_v):
                                        try:
                                            table.at[idx, _col] = float(_v) * 0.5
                                        except (TypeError, ValueError):
                                            pass
                            _sw = table.at[idx, "stress_warnings"] \
                                if "stress_warnings" in table.columns else ""
                            _sw = str(_sw) if pd.notna(_sw) else ""
                            _flag = "calibration_caution_18_25pp"
                            table.at[idx, "stress_warnings"] = (
                                f"{_sw};{_flag}" if _sw else _flag
                            )
            except Exception as e:
                log.warning("[soft_cap_6.5] damping failed: %s "
                            "(continuing without flag)", e)

            print("\n=== DIAGNOSTIC TABLE - every game on slate ===")
            print(table.to_string(index=False))
            if out_picks:
                Path(out_picks).parent.mkdir(parents=True, exist_ok=True)
                _atomic_to_csv(table, out_picks)
                log.info("Wrote diagnostic table to %s", out_picks)

            # ----------------------------------------------------------
            # Parlay builder: write parlay_<date>.txt next to the slate
            # ----------------------------------------------------------
            try:
                from . import parlay_builder
                # Build {matchup: {away_sp_name, home_sp_name}} from the
                # live_news anchor file (keyed by game_pk) + games' game_id.
                anchor_path = Path(f"data/news_cache/anchors/anchor_{slate_date.isoformat()}.json")
                matchup_to_sps = {}
                if anchor_path.exists():
                    import json as _json
                    raw = _json.loads(anchor_path.read_text())
                    pk_to_sps = {int(k): v for k, v in raw.items()}
                    if "game_id" in preds.columns:
                        for _, gr in preds.iterrows():
                            try:
                                gpk = int(gr["game_id"])
                            except Exception:
                                continue
                            sps = pk_to_sps.get(gpk, {})
                            ah = gr.get("home_team", "")
                            aa = gr.get("away_team", "")
                            from .stadiums import normalize_team as _nt
                            matchup_key = f"{_nt(aa)} @ {_nt(ah)}"
                            matchup_to_sps[matchup_key] = sps
                graded = parlay_builder.grade_picks(
                    table, anchor=matchup_to_sps, slate_date=slate_date,
                )
                # Freeze picks for games that have already started.
                # Restores LOCK_COLUMNS from the prior CSV row so a
                # mid-game re-bake (e.g. bullpen-fatigue flip) never
                # changes what the user saw pre-game.
                try:
                    _started_map = _games_started_map(slate_date)
                    _n_locked = _apply_started_game_lock(
                        graded, _prior_picks_for_lock, _started_map,
                    )
                    if _n_locked:
                        log.info(
                            "[lock] preserved pre-game picks for %d "
                            "started game(s)", _n_locked,
                        )
                except Exception as _e_lock:
                    log.warning(
                        "[lock] failed (continuing without lock): %s",
                        _e_lock,
                    )
                parlay_path = Path(f"parlay_{slate_date.isoformat()}.txt")
                parlay_builder.write_parlay_report(graded, slate_date, parlay_path)
                log.info("Wrote parlay report to %s", parlay_path)
                # Persist the graded columns (grade, pre_cap_score,
                # pre_cap_grade, grade_reasons) BACK to the diag CSV.
                # Without this re-write, the diag CSV only has the
                # pre-grading table from line 800 and the weekly cap
                # audit finds zero cap-era files.
                if out_picks:
                    _atomic_to_csv(graded, out_picks)
                    log.info(
                        "Re-wrote diagnostic table with grade columns "
                        "to %s", out_picks)

                # ---- Platoon-brain MVP: attach top-5 batter JSON (2026-05-14)
                # Also collects matchup -> game_pk + SP-ID lookup tables that
                # the BvP-brain attach below reuses.
                matchup_to_pk = {}
                matchup_to_sp_hand = {}
                matchup_to_sp_ids = {}
                # Source SP IDs from lineup_meta — preds DataFrame does NOT
                # carry away_sp_id/home_sp_id as columns (build_pipeline takes
                # them as function parameters but doesn't emit them).
                # Bug fix 2026-05-20: prior inline lookup silently failed.
                # Per-matchup try/except — Rule 6 says "best-effort per row";
                # outer-wrap would let a single int() raise abort the whole
                # loop, losing all subsequent matchups.
                from .stadiums import normalize_team as _nt3
                _lineups = ctx.get("lineups") or []
                _h, _a = "", ""  # initialise so the except-handler can log them
                for _meta in _lineups:
                    try:
                        _h = _nt3(getattr(_meta, "home_abbr", "") or "")
                        _a = _nt3(getattr(_meta, "away_abbr", "") or "")
                        if not _h or not _a:
                            continue
                        _mk_sp = f"{_a} @ {_h}"
                        _hsp = getattr(_meta, "home_sp_id", None)
                        _asp = getattr(_meta, "away_sp_id", None)
                        if _hsp or _asp:
                            matchup_to_sp_ids[_mk_sp] = {
                                "away_sp_id": (int(_asp) if _asp else None),
                                "home_sp_id": (int(_hsp) if _hsp else None),
                            }
                    except Exception as _e:
                        log.warning(
                            "BvP SP-ID lookup failed for matchup %s @ %s: %s",
                            _a, _h, _e)
                log.info("BvP SP-ID lookup built from lineup_meta: %d matchups",
                         len(matchup_to_sp_ids))

                try:
                    from . import platoon_splits as _ps
                    if "game_id" in preds.columns:
                        from .stadiums import normalize_team as _nt2
                        for _, gr in preds.iterrows():
                            try:
                                gpk = int(gr["game_id"])
                            except Exception:
                                continue
                            ah = gr.get("home_team", "")
                            aa = gr.get("away_team", "")
                            mk = f"{_nt2(aa)} @ {_nt2(ah)}"
                            matchup_to_pk[mk] = gpk
                            sp_a = gr.get("away_sp_throws") or gr.get("away_sp_hand")
                            sp_h = gr.get("home_sp_throws") or gr.get("home_sp_hand")
                            if sp_a or sp_h:
                                matchup_to_sp_hand[mk] = {
                                    "away_sp_hand": sp_a,
                                    "home_sp_hand": sp_h,
                                }
                    if matchup_to_pk:
                        _ps.attach_top_5_to_diag(
                            graded, matchup_to_pk, matchup_to_sp_hand)
                        if out_picks:
                            _atomic_to_csv(graded, out_picks)
                            log.info("Attached top-5 batter JSON columns")
                except Exception as e:
                    log.warning("platoon_splits attach failed "
                                "(continuing without top-5 JSON): %s", e)

                # ---- BvP-brain MVP: attach top-5 batter BvP JSON (2026-05-19)
                # Per-batter career history vs today's opposing SP, packaged
                # as JSON-string columns for the Claude Brain layer.  Same
                # architecture as platoon-brain: keep XGBoost on aggregates,
                # let the LLM reason about per-player BvP samples.
                # Best-effort per Rule 6 — any failure here keeps the diag
                # CSV graded-and-baked, just without the BvP columns.
                try:
                    from . import bvp_brain as _bvp
                    if matchup_to_pk and matchup_to_sp_ids:
                        _bvp.attach_bvp_to_diag(
                            graded, matchup_to_pk, matchup_to_sp_ids)
                        if out_picks:
                            _atomic_to_csv(graded, out_picks)
                            log.info("Attached top-5 batter BvP JSON columns")
                    else:
                        log.info("bvp_brain skipped: no SP IDs available "
                                 "in preds (%d matchups, %d with SP IDs)",
                                 len(matchup_to_pk), len(matchup_to_sp_ids))
                except Exception as e:
                    log.warning("bvp_brain attach failed "
                                "(continuing without BvP JSON): %s", e)
            except Exception as e:
                log.warning("parlay builder failed (continuing): %s", e)
        return

    sheet = recommend_slate(preds, odds_long, bankroll=bankroll) if not odds_long.empty \
        else pd.DataFrame()

    if not sheet.empty and ctx.get("bullpen") is not None and not ctx["bullpen"].pitch_log.empty:
        try:
            sheet_for_ceiling = sheet.rename(
                columns={"team": "pick_winner", "tier": "conv_tier"}
            ).copy()
            sheet_for_ceiling["home_team"] = ""
            sheet_for_ceiling["away_team"] = ""
            capped = apply_bullpen_ceiling(
                sheet_for_ceiling, ctx["bullpen"].workload_by_team
            )
            sheet["tier"] = capped["conv_tier_v51"].values
        except Exception as e:
            log.warning("Bullpen ceiling skipped: %s", e)

    if sheet.empty:
        print("\nNo bets pass the filter for this slate.")
        return

    print("\n=== BET SHEET ===")
    print(sheet.to_string(index=False))
    print(f"\nTotal bets: {len(sheet)}, "
          f"Total risk: {sheet['stake_u'].sum():.2f} units")

    if out_picks:
        Path(out_picks).parent.mkdir(parents=True, exist_ok=True)
        _atomic_to_csv(sheet, out_picks)
        log.info("Wrote picks to %s", out_picks)


def _parse_args(argv):
    p = argparse.ArgumentParser(description="MLB-edge live prediction orchestrator")
    p.add_argument("--date", required=True,
                   type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="Slate date YYYY-MM-DD")
    p.add_argument("--bankroll", type=float, default=100.0)
    p.add_argument("--model_path", default="models/latest.pkl")
    p.add_argument("--out", help="Output CSV path")
    p.add_argument("--diagnostic-table", action="store_true",
                   help="Bypass zero-bet trigger; print table for every game.")
    p.add_argument("--skip-auto-update", action="store_true",
                   help="Skip the recursive weight update for yesterday.")
    p.add_argument("--skip-savant-refresh", action="store_true",
                   help="Skip the Savant leaderboard refresh.")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    run(args.date, bankroll=args.bankroll, model_path=args.model_path,
        out_picks=args.out, diagnostic_table=args.diagnostic_table,
        skip_auto_update=args.skip_auto_update,
        skip_savant_refresh=args.skip_savant_refresh)


if __name__ == "__main__":
    main()
