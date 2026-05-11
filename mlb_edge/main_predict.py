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
import logging
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
def _pending_sp_data_row(*, away_abbr: str, home_abbr: str,
                         why_skipped: str) -> dict:
    return {
        "matchup":              f"{away_abbr} @ {home_abbr}",
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
    additions = []
    for g in schedule:
        if g.get("home_sp_id") and g.get("away_sp_id"):
            continue
        home_abbr = normalize_team(g.get("home_team") or "")
        away_abbr = normalize_team(g.get("away_team") or "")
        if not home_abbr or not away_abbr:
            continue
        matchup = f"{away_abbr} @ {home_abbr}"
        if matchup in have_matchups:
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
        ))
        have_matchups.add(matchup)
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
        h_name = (r.get("home_sp_name") or "").strip()
        a_name = (r.get("away_sp_name") or "").strip()
        thin_sides: list[str] = []
        if pd.isna(h_n) or float(h_n) < SP_THIN_SAMPLE_THRESHOLD:
            label = h_name or f"{home_abbr} SP"
            n_disp = "0" if pd.isna(h_n) else str(int(h_n))
            thin_sides.append(
                f"{label} has only {n_disp} Statcast pitches season-to-date; "
                f"need {SP_THIN_SAMPLE_THRESHOLD}+ to score"
            )
        if pd.isna(a_n) or float(a_n) < SP_THIN_SAMPLE_THRESHOLD:
            label = a_name or f"{away_abbr} SP"
            n_disp = "0" if pd.isna(a_n) else str(int(a_n))
            thin_sides.append(
                f"{label} has only {n_disp} Statcast pitches season-to-date; "
                f"need {SP_THIN_SAMPLE_THRESHOLD}+ to score"
            )
        if thin_sides:
            rows.append(_pending_sp_data_row(
                away_abbr=away_abbr,
                home_abbr=home_abbr,
                why_skipped=" | ".join(thin_sides),
            ))
            continue

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
        ev = expected_value(p_model, home_dec if side == "home" else away_dec)

        why_skipped = []
        if pd.isna(p_model) or not (MIN_MODEL_PROB <= p_model <= MAX_MODEL_PROB):
            why_skipped.append(f"model_prob {p_model:.3f} outside [{MIN_MODEL_PROB},{MAX_MODEL_PROB}]")
        if pd.notna(fair) and fair < MIN_FAIR_PROB:
            why_skipped.append(f"fair_prob {fair:.3f} < {MIN_FAIR_PROB}")
        if pd.notna(edge) and (edge < MIN_EDGE_PCT or edge > MAX_EDGE_PCT):
            why_skipped.append(f"edge {edge*100:+.2f}pp outside [{MIN_EDGE_PCT*100:.0f},{MAX_EDGE_PCT*100:.0f}]pp")
        if TIER_SIZES.get(conv.tier, 0.0) == 0.0:
            why_skipped.append(f"tier {conv.tier} -> stake_mult=0")

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
            "tier": conv.tier,
            "signals": ", ".join(conv.signals_fired),
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
def run(slate_date: date,
        bankroll: float = 100.0,
        model_path: str = "models/latest.pkl",
        out_picks: Optional[str] = None,
        diagnostic_table: bool = False,
        skip_auto_update: bool = False,
        skip_savant_refresh: bool = False,
        skip_news: bool = False) -> None:
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
                empty_table.to_csv(out_picks, index=False)
                log.info("Wrote PENDING-only diagnostic table to %s "
                         "(%d games)", out_picks, len(empty_table))
        return

    games = overlay_live_features(games, ctx)
    preds = md.predict(stage1, stage2, games)
    preds = gate_sp_features(preds)

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

    # Track odds-fetch outcome explicitly so the diagnostic table can record
    # WHY fair_prob may be missing. Three failure modes we now log loudly:
    #   - no API key configured                (status = "no_api_key")
    #   - API returned empty payload           (status = "empty_payload")
    #   - exception during call                (status = "exception")
    # Bug 2 fix (2026-05-02): previously the empty-payload case silently
    # produced a diag with NaN fair_prob and no signal in the run output,
    # which is how 2026-04-30 and 2026-05-01 shipped without odds.
    odds_long = pd.DataFrame()
    odds_status = "fetched"
    try:
        client = di.OddsClient()
        if not client.api_key:
            odds_status = "no_api_key"
            log.error("[odds] ODDS_API_KEY not set — diag will write NaN "
                      "fair_prob / edge_pp / EV. Set the env var to enable "
                      "market features.")
        else:
            odds_long = client.current_lines()
            if odds_long.empty:
                odds_status = "empty_payload"
                log.error("[odds] OddsClient.current_lines() returned an empty "
                          "DataFrame. The slate diag will be written WITHOUT "
                          "fair_prob / edge_pp / EV — this is a silent failure "
                          "mode (rate limit / API outage / quota exhausted). "
                          "Inspect the previous Odds API log line for the "
                          "remaining-quota header.")
            else:
                odds_long["outcome"] = odds_long["outcome"].apply(normalize_team)
    except Exception as e:
        odds_status = "exception"
        log.error("[odds] Odds API call raised an exception: %s. Diag will "
                  "be written WITHOUT fair_prob / edge_pp / EV.", e)

    # ------------------------------------------------------------------
    # [step 3.1/5] ESPN odds fallback
    # ------------------------------------------------------------------
    # When the primary Odds API returned empty / failed / no key, try
    # the free ESPN public odds page (https://www.espn.com/mlb/lines)
    # as a backstop.  Same long-format schema as OddsClient output, so
    # downstream `recommend_slate` doesn't care which source we used.
    # Without this, slates like 5/1 ship with blank fair_prob and the
    # parlay grader has no edge-vs-market check (which is how 5/1's
    # 4-9 record happened with multiple overconfident A-/B+ picks).
    if odds_status != "fetched" or odds_long.empty:
        try:
            from . import odds_fallback as _of
            espn_df = _of.fetch_espn_mlb_odds(slate_date)
            if not espn_df.empty:
                espn_df["outcome"] = espn_df["outcome"].apply(normalize_team)
                odds_long = espn_df
                odds_status = f"{odds_status}+espn_fallback"
                log.info("[odds] ESPN fallback populated %d odds rows for %d "
                         "games", len(espn_df), len(espn_df) // 2)
            else:
                log.warning("[odds] ESPN fallback also returned empty")
        except Exception as e:
            log.warning("[odds] ESPN fallback raised: %s", e)
    elif not odds_long.empty:
        # Primary succeeded — still try to backfill any games the primary
        # missed (rare, but possible when bookmaker coverage is patchy).
        try:
            from . import odds_fallback as _of
            before = len(odds_long)
            odds_long = _of.backfill_missing_odds(odds_long, slate_date)
            if "outcome" in odds_long.columns:
                odds_long["outcome"] = odds_long["outcome"].apply(normalize_team)
            added = len(odds_long) - before
            if added > 0:
                log.info("[odds] ESPN fallback backfilled %d additional odds "
                         "rows on top of primary", added)
        except Exception as e:
            log.warning("[odds] ESPN backfill raised: %s", e)

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

            print("\n=== DIAGNOSTIC TABLE - every game on slate ===")
            print(table.to_string(index=False))
            if out_picks:
                Path(out_picks).parent.mkdir(parents=True, exist_ok=True)
                table.to_csv(out_picks, index=False)
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
                parlay_path = Path(f"parlay_{slate_date.isoformat()}.txt")
                parlay_builder.write_parlay_report(graded, slate_date, parlay_path)
                log.info("Wrote parlay report to %s", parlay_path)
            except Exception as e:
                log.warning("parlay builder failed (continuing): %s", e)
        return

    sheet = recommend_slate(preds, odds_long, bankroll=bankroll) if not odds_long.empty \
        else pd.DataFrame()

    if not sheet.empty and not ctx["bullpen"].pitch_log.empty:
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
        sheet.to_csv(out_picks, index=False)
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
