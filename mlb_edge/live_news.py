"""
mlb_edge/live_news.py
---------------------
Tier 0 + Tier 1 enrichment layer that runs *after* the model produces
probabilities and *before* the bet sheet is built.  Pulls four classes of
information that don't live in any of the eight scheduled feeds:

    A. SP late scratches   (MLB Stats API live snapshot vs. anchor cache)
    B. Plate ump bias      (data_sources.umpire — placeholder for v1)
    C. Bullpen-short flag  (bullpen_tracker.snapshot, with back-to-back
                            workload check on the team's high-leverage arms)
    D. Line movement       (the-odds-api current price vs. anchor snapshot
                            taken on the first run of the day)

The module *does not* introduce features that retrain the booster — it
applies signed nudges (`news_model_prob_delta`) plus optional tier
demotion to the existing model output.  Every override decision is logged
to ``picks_<date>_news_overrides.csv`` next to the slate file so we can
A/B "would I have won without the override?" weeks later.

Architecture
============
    games  ──(model.predict + sp_savant_gate)──►  scored DataFrame
                                                       │
                                                       ▼
                                                enrich_slate(...)
                                                       │
                                                       ▼
                              fetch  →  decide  →  (delta, rationale)  →  audit log
                              ───── ─────── ──────────── ─────── ──── ─────────
                              SPL       SPL_RULE      Δ pp                  ┐
                              UMP       UMP_RULE      Δ pp                  │
                              BP        BP_RULE       Δ pp                  ├─►  picks_<date>_news_overrides.csv
                              ODDS      ODDS_RULE     Δ pp                  │
                                                                            ┘
                                                       ▼
                                       games with model_prob adjusted
                                              & news_* cols added
                                                       │
                                                       ▼
                                          edge_calculator.recommend_slate

Backtest-safety contract
========================
- Every fetcher returns deterministic-ish data when given a fixed cache
  state.  The "anchor" snapshot for SP late-scratch and line-move
  detection is written to ``data/news_cache/anchors/<date>.json`` on the
  first run of the day and reused for the rest of that calendar day.
- No fetched value is silently merged into the booster's training frame.
  Everything stays in `news_*` columns + a manual delta on `model_prob`.
- Each override row in the audit log carries `(source, observed_at,
  rationale)` so a future "did this help?" analysis is mechanically
  reproducible.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from .data_sources import umpire as ump
from .stadiums import normalize_team

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
ANCHOR_DIR   = Path("data/news_cache/anchors")
ODDS_HISTORY_DIR = Path("data/news_cache/odds_snapshots")


# ---------------------------------------------------------------------------
# Per-game enrichment record — shape locked so consumers can rely on the
# columns existing even when fetchers fail (defaults are no-op).
# ---------------------------------------------------------------------------
@dataclass
class NewsRecord:
    game_pk: int
    matchup: str

    # SP late-scratch
    news_sp_late_scratch_home: bool = False
    news_sp_late_scratch_away: bool = False
    news_anchor_sp_home_id: Optional[int] = None
    news_anchor_sp_away_id: Optional[int] = None
    news_current_sp_home_id: Optional[int] = None
    news_current_sp_away_id: Optional[int] = None

    # Ump
    news_ump_plate_id: Optional[int] = None
    news_ump_plate_name: Optional[str] = None
    news_ump_bias_pp: float = 0.0

    # Bullpen
    news_bullpen_short_home: bool = False
    news_bullpen_short_away: bool = False

    # Line move
    news_anchor_home_decimal: Optional[float] = None
    news_current_home_decimal: Optional[float] = None
    news_line_move_home_bps: int = 0   # signed: +N => home shortened (more chalk)

    # Tier 2 — injury / lineup-scratch news (mlb_edge/injury_news.py)
    news_il_placements_home: int = 0
    news_il_placements_away: int = 0
    news_il_player_names_home: str = ""    # comma-joined for CSV friendliness
    news_il_player_names_away: str = ""
    news_lineup_scratches_home: int = 0
    news_lineup_scratches_away: int = 0
    news_regular_scratches_home: int = 0
    news_regular_scratches_away: int = 0

    # Aggregate adjustments produced by apply_rules()
    news_model_prob_delta: float = 0.0
    news_tier_demotion_steps: int = 0
    news_rationale: str = ""
    news_rules_fired: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Anchors — first-of-day snapshots used to detect "what changed since
# the model built its frame."
# ---------------------------------------------------------------------------
def _anchor_path(slate_date: date) -> Path:
    return ANCHOR_DIR / f"anchor_{slate_date.isoformat()}.json"


def _load_anchor(slate_date: date) -> Dict[str, dict]:
    p = _anchor_path(slate_date)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning("anchor read failed for %s: %s", slate_date, e)
        return {}


def _save_anchor(slate_date: date, data: Dict[str, dict]) -> None:
    try:
        ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
        _anchor_path(slate_date).write_text(json.dumps(data, indent=2,
                                                        default=str))
    except Exception as e:
        log.warning("anchor write failed for %s: %s", slate_date, e)


# ---------------------------------------------------------------------------
# Fetcher A: SP late scratch
# ---------------------------------------------------------------------------
def _fetch_current_probables(slate_date: date) -> Dict[int, dict]:
    """Return {game_pk: {home_sp_id, home_sp_name, away_sp_id, away_sp_name}}
    based on the *latest* schedule API hydrate, which updates within minutes
    of a manager announcing a swap."""
    try:
        r = requests.get(SCHEDULE_URL, params={
            "sportId": 1,
            "date": slate_date.isoformat(),
            "hydrate": "probablePitcher(note),team",
        }, timeout=12)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning("probable-pitcher fetch failed for %s: %s", slate_date, e)
        return {}

    out: Dict[int, dict] = {}
    for d in payload.get("dates", []):
        for g in d.get("games", []):
            home = g.get("teams", {}).get("home", {}) or {}
            away = g.get("teams", {}).get("away", {}) or {}
            home_pp = home.get("probablePitcher") or {}
            away_pp = away.get("probablePitcher") or {}
            out[g["gamePk"]] = {
                "home_sp_id": home_pp.get("id"),
                "home_sp_name": home_pp.get("fullName"),
                "away_sp_id": away_pp.get("id"),
                "away_sp_name": away_pp.get("fullName"),
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
    return out


def _detect_sp_scratch(rec: NewsRecord, anchor: dict, current: dict) -> None:
    """Mutates rec to set news_sp_late_scratch_{home,away} when the anchor
    SP differs from the current SP for that game."""
    a_home, a_away = anchor.get("home_sp_id"), anchor.get("away_sp_id")
    c_home, c_away = current.get("home_sp_id"), current.get("away_sp_id")
    rec.news_anchor_sp_home_id  = a_home
    rec.news_anchor_sp_away_id  = a_away
    rec.news_current_sp_home_id = c_home
    rec.news_current_sp_away_id = c_away
    # A scratch is detected only when both sides of the comparison are
    # populated *and* differ.  If anchor is missing (first run of the day)
    # or current is missing (API blip), default to False so we don't fire
    # a false positive.
    if a_home and c_home and a_home != c_home:
        rec.news_sp_late_scratch_home = True
    if a_away and c_away and a_away != c_away:
        rec.news_sp_late_scratch_away = True


# ---------------------------------------------------------------------------
# Fetcher C: bullpen short
# ---------------------------------------------------------------------------
# Threshold for "short bullpen" using the team-summary view: 60+ pitches
# across the top-3 relievers in the 72h window is roughly the 70th percentile
# of MLB-wide bullpen workload — the level where the lead-arm sequence the
# manager prefers (closer + 2 setup) starts to fragment in late innings.
BULLPEN_SHORT_TOP3_PITCHES = 60


def _compute_bullpen_short(workload: pd.DataFrame,
                           home_abbr: str, away_abbr: str) -> Tuple[bool, bool]:
    """Return (home_short, away_short) flags.

    Accepts two shapes for `workload`:
      (a) *Team summary* (default from bullpen_tracker.snapshot.workload_by_team):
          columns = [team, top3_pitch_total_72h, ceiling_tier]
          -> flag when top3_pitch_total_72h >= BULLPEN_SHORT_TOP3_PITCHES
             OR ceiling_tier in {"SKIP","GOLD"} (the existing bullpen_fatigue
             ceiling that downstream apply_bullpen_ceiling already trusts).
      (b) *Per-pitcher pitch log* (snap.pitch_log):
          columns = [game_date, team, pitcher_id, is_starter, pitches, leverage_index]
          -> flag when the team's top high-leverage reliever (max pitches in
             the 72h window, is_starter=False) pitched on BOTH yesterday and
             the day before.
    Degrades to (False, False) on missing data.
    """
    if workload is None or workload.empty:
        return False, False
    cols = set(workload.columns)

    # Shape A — team summary
    if {"team", "top3_pitch_total_72h"}.issubset(cols):
        def _team_short_A(abbr: str) -> bool:
            rows = workload[
                workload["team"].apply(normalize_team) == normalize_team(abbr)
            ]
            if rows.empty:
                return False
            tier = str(rows.iloc[0].get("ceiling_tier", ""))
            top3 = float(rows.iloc[0].get("top3_pitch_total_72h", 0))
            # Primary signal: bullpen_tracker has already produced a
            # categorical SKIP for this team — its top-3 high-leverage arms
            # are spent.  Don't double-trip on GOLD; that tier is "watch
            # carefully", not "unavailable".
            if tier == "SKIP":
                return True
            # Belt-and-suspenders: top-3 pitch count above an extreme
            # threshold (90+) catches teams the categorical missed
            # (rare but happens on day-after-extra-innings).
            if top3 >= 90:
                return True
            return False
        return _team_short_A(home_abbr), _team_short_A(away_abbr)

    # Shape B — per-pitcher pitch log
    if {"team", "pitcher_id", "game_date", "pitches", "is_starter"}.issubset(cols):
        today = date.today()
        yest  = today - timedelta(days=1)
        db4   = today - timedelta(days=2)

        def _team_short_B(abbr: str) -> bool:
            rel = workload[
                (workload["team"].apply(normalize_team) == normalize_team(abbr))
                & (~workload["is_starter"].astype(bool))
            ]
            if rel.empty:
                return False
            # Highest-workload reliever in the 72h window
            top_pid = (rel.groupby("pitcher_id")["pitches"].sum()
                          .sort_values(ascending=False).index[0])
            top = rel[rel["pitcher_id"] == top_pid]
            dates = set(pd.to_datetime(top["game_date"]).dt.date)
            return yest in dates and db4 in dates

        return _team_short_B(home_abbr), _team_short_B(away_abbr)

    # Unknown shape -> no-op
    log.debug("[bullpen_short] unknown workload shape, cols=%s", sorted(cols))
    return False, False


# ---------------------------------------------------------------------------
# Fetcher D: line movement (cached current_lines snapshots)
# ---------------------------------------------------------------------------
def _odds_snapshot_path(slate_date: date) -> Path:
    return ODDS_HISTORY_DIR / f"odds_{slate_date.isoformat()}_anchor.json"


def _load_anchor_odds(slate_date: date) -> Dict[str, float]:
    p = _odds_snapshot_path(slate_date)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_anchor_odds(slate_date: date, prices: Dict[str, float]) -> None:
    try:
        ODDS_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        _odds_snapshot_path(slate_date).write_text(json.dumps(prices, indent=2))
    except Exception as e:
        log.warning("odds anchor write failed: %s", e)


def _key(home_abbr: str, away_abbr: str) -> str:
    return f"{normalize_team(away_abbr)}@{normalize_team(home_abbr)}"


def _compute_line_movement(rec: NewsRecord, current_home_dec: Optional[float],
                            anchor_home_dec: Optional[float]) -> None:
    rec.news_anchor_home_decimal  = anchor_home_dec
    rec.news_current_home_decimal = current_home_dec
    if current_home_dec is None or anchor_home_dec is None:
        return
    # Convert decimal odds to implied prob, diff in basis points.
    cur_imp = 1.0 / current_home_dec
    anc_imp = 1.0 / anchor_home_dec
    bps = int(round((cur_imp - anc_imp) * 10000))
    rec.news_line_move_home_bps = bps


# ---------------------------------------------------------------------------
# Decision rules — Tier 0 + Tier 1 logic, all configurable in config.py
# ---------------------------------------------------------------------------
def _apply_rules(rec: NewsRecord, model_prob_home: float,
                 cfg: dict) -> None:
    """Mutates rec.news_model_prob_delta, news_tier_demotion_steps,
    news_rationale based on the populated flags.  Sign convention:
    positive delta favors HOME.
    """
    deltas: List[Tuple[str, float]] = []
    demotes: List[str] = []
    notes: List[str] = []

    # --- A. SP late scratch ---
    sp_pp = cfg.get("SP_SCRATCH_DELTA_PP", 0.04)
    if rec.news_sp_late_scratch_home:
        deltas.append(("sp_scratch_home", -sp_pp))
        demotes.append("sp_scratch_home")
        notes.append(f"home SP scratched -> {sp_pp*100:.0f}pp toward away, demote tier")
    if rec.news_sp_late_scratch_away:
        deltas.append(("sp_scratch_away", +sp_pp))
        demotes.append("sp_scratch_away")
        notes.append(f"away SP scratched -> {sp_pp*100:.0f}pp toward home, demote tier")

    # --- B. Ump bias ---
    if abs(rec.news_ump_bias_pp) > 0.0:
        deltas.append(("ump_bias", rec.news_ump_bias_pp))
        notes.append(f"ump bias {rec.news_ump_bias_pp*100:+.2f}pp")

    # --- C. Bullpen short ---
    bp_pp = cfg.get("BULLPEN_SHORT_DELTA_PP", 0.015)
    if rec.news_bullpen_short_home:
        deltas.append(("bullpen_short_home", -bp_pp))
        notes.append(f"home bullpen short -> {bp_pp*100:.1f}pp toward away")
    if rec.news_bullpen_short_away:
        deltas.append(("bullpen_short_away", +bp_pp))
        notes.append(f"away bullpen short -> {bp_pp*100:.1f}pp toward home")

    # --- E. IL placements (Tier 2) ---
    il_pp_per = cfg.get("IL_PLACEMENT_DELTA_PP", 0.012)
    if rec.news_il_placements_home > 0:
        # Cumulative but with diminishing impact: -1.2pp for first, +0.6pp
        # for each subsequent (capped at 4 placements).
        n = min(rec.news_il_placements_home, 4)
        d = -il_pp_per * (1.0 + 0.5 * (n - 1))
        deltas.append(("il_placement_home", d))
        notes.append(f"home {rec.news_il_placements_home} IL placement(s) "
                     f"({rec.news_il_player_names_home}) -> "
                     f"{d*100:+.2f}pp")
    if rec.news_il_placements_away > 0:
        n = min(rec.news_il_placements_away, 4)
        d = il_pp_per * (1.0 + 0.5 * (n - 1))
        deltas.append(("il_placement_away", d))
        notes.append(f"away {rec.news_il_placements_away} IL placement(s) "
                     f"({rec.news_il_player_names_away}) -> "
                     f"{d*100:+.2f}pp")

    # --- F. Lineup scratches (Tier 2) ---
    scratch_pp = cfg.get("LINEUP_SCRATCH_DELTA_PP", 0.015)
    if rec.news_regular_scratches_home > 0:
        d = -scratch_pp * rec.news_regular_scratches_home
        deltas.append(("scratch_home", d))
        demotes.append("scratch_home")
        notes.append(f"home regular scratch(es): "
                     f"{rec.news_regular_scratches_home} -> {d*100:+.2f}pp + demote")
    if rec.news_regular_scratches_away > 0:
        d = scratch_pp * rec.news_regular_scratches_away
        deltas.append(("scratch_away", d))
        demotes.append("scratch_away")
        notes.append(f"away regular scratch(es): "
                     f"{rec.news_regular_scratches_away} -> {d*100:+.2f}pp + demote")

    # --- D. Line movement ---
    move_thr_weak   = cfg.get("LINE_MOVE_WEAK_THRESHOLD_BPS", 25)
    move_thr_strong = cfg.get("LINE_MOVE_STRONG_THRESHOLD_BPS", 50)
    move_pp_weak    = cfg.get("LINE_MOVE_WEAK_DELTA_PP",   0.005)
    move_pp_strong  = cfg.get("LINE_MOVE_STRONG_DELTA_PP", 0.015)
    bps = rec.news_line_move_home_bps
    if abs(bps) >= move_thr_strong:
        # Strong move + model already on that side
        side_home = bps > 0  # positive = home shortened (sharps liked home)
        if (side_home and model_prob_home >= 0.5) or \
           (not side_home and model_prob_home <  0.5):
            sign = +1 if side_home else -1
            deltas.append(("line_move_strong", sign * move_pp_strong))
            notes.append(f"sharp move {bps:+d}bps confirms model side -> "
                         f"{move_pp_strong*100:+.1f}pp")
    elif abs(bps) >= move_thr_weak:
        sign = +1 if bps > 0 else -1
        deltas.append(("line_move_weak", sign * move_pp_weak))
        notes.append(f"line move {bps:+d}bps -> {move_pp_weak*100:+.1f}pp")

    rec.news_model_prob_delta     = sum(d for _, d in deltas)
    rec.news_tier_demotion_steps  = len(set(demotes))   # cap at #unique rules
    rec.news_rules_fired          = [name for name, _ in deltas]
    rec.news_rationale            = " | ".join(notes) if notes else ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
TIER_ORDER = ["DIAMOND", "PLATINUM", "GOLD", "SKIP"]


def _demote_tier(tier: str, steps: int) -> str:
    if not steps or tier not in TIER_ORDER:
        return tier
    idx = min(TIER_ORDER.index(tier) + steps, len(TIER_ORDER) - 1)
    return TIER_ORDER[idx]


def enrich_slate(games: pd.DataFrame, slate_date: date,
                 odds_long: Optional[pd.DataFrame] = None,
                 bullpen_workload: Optional[pd.DataFrame] = None,
                 cfg: Optional[dict] = None) -> Tuple[pd.DataFrame,
                                                       pd.DataFrame]:
    """Apply Tier 0 + Tier 1 enrichment to a scored slate.

    Returns (enriched_games_df, audit_df).  `audit_df` is a per-game record
    of every flag and rule firing — write it to ``picks_<date>_news_overrides.csv``.
    """
    if games.empty:
        return games, pd.DataFrame()
    cfg = cfg or {}
    out = games.copy()

    # 1. Anchor management — load or create the day's first snapshot.
    anchor = _load_anchor(slate_date)
    current = _fetch_current_probables(slate_date)
    is_first_run = not anchor
    if is_first_run and current:
        _save_anchor(slate_date, {str(k): v for k, v in current.items()})
        anchor = {str(k): v for k, v in current.items()}
    # normalize key types (json keys are str)
    anchor = {int(k): v for k, v in anchor.items()} if anchor else {}

    # 2. Anchor odds for line-movement detection.
    odds_anchor = _load_anchor_odds(slate_date)
    odds_now: Dict[str, float] = {}
    if odds_long is not None and not odds_long.empty:
        h2h = odds_long[odds_long["market"] == "h2h"].copy()
        # decimal column may need conversion if absent
        if "decimal" not in h2h.columns and "price" in h2h.columns:
            from .edge_calculator import american_to_decimal
            h2h["decimal"] = h2h["price"].apply(american_to_decimal)
        for _, row in h2h.iterrows():
            home_a = normalize_team(row["home_team"])
            away_a = normalize_team(row["away_team"])
            outcome = normalize_team(row["outcome"])
            if outcome != home_a:
                continue
            k = _key(home_a, away_a)
            odds_now[k] = float(row["decimal"])
        if not odds_anchor:
            _save_anchor_odds(slate_date, odds_now)
            odds_anchor = dict(odds_now)

    # 3. Per-game enrichment loop.
    records: List[NewsRecord] = []
    game_pks: List[int] = out["game_id"].astype(int).tolist()
    ump_assignments = ump.get_assignments_for_slate(game_pks)

    # ------------------------------------------------------------------
    # Tier 2 — injury / lineup-scratch fetchers (mlb_edge/injury_news.py)
    # ------------------------------------------------------------------
    try:
        from . import injury_news
        il_placements_by_team = injury_news.fetch_il_placements(slate_date)
        current_lineups       = injury_news.fetch_lineup_snapshot(slate_date)
        lineup_anchor         = injury_news.load_lineup_anchor(slate_date)
        # First-of-day lineup anchor — only seed when *some* lineup is posted.
        if not lineup_anchor and any(
            (sides.get("home") or sides.get("away"))
            for sides in current_lineups.values()
        ):
            injury_news.save_lineup_anchor(slate_date, current_lineups)
            lineup_anchor = current_lineups
        scratches_by_game = injury_news.detect_scratches(lineup_anchor,
                                                          current_lineups)
        pa_lookup = injury_news._load_batter_pa_lookup()
    except Exception as e:
        log.warning("Tier 2 injury fetchers failed: %s", e)
        il_placements_by_team = {}
        scratches_by_game = {}
        pa_lookup = {}

    for idx, row in out.iterrows():
        gpk = int(row["game_id"])
        home, away = row["home_team"], row["away_team"]
        rec = NewsRecord(game_pk=gpk, matchup=f"{normalize_team(away)} @ "
                                              f"{normalize_team(home)}")
        # A. Late scratch
        a = anchor.get(gpk, {}) or {}
        c = current.get(gpk, {}) or {}
        _detect_sp_scratch(rec, a, c)
        # B. Ump
        ua = ump_assignments.get(gpk)
        if ua is not None:
            rec.news_ump_plate_id   = ua.plate_ump_id
            rec.news_ump_plate_name = ua.plate_ump_name
            rec.news_ump_bias_pp    = ua.plate_ump_bias_pp
        # C. Bullpen
        h_short, a_short = _compute_bullpen_short(bullpen_workload, home, away)
        rec.news_bullpen_short_home = h_short
        rec.news_bullpen_short_away = a_short
        # D. Line move
        k = _key(home, away)
        _compute_line_movement(rec, odds_now.get(k), odds_anchor.get(k))
        # Tier 2 — populate IL + scratch fields
        home_n = injury_news.normalize_team(home) if False else                  __import__("mlb_edge.stadiums", fromlist=["normalize_team"]).normalize_team(home)
        away_n = __import__("mlb_edge.stadiums", fromlist=["normalize_team"]).normalize_team(away)
        for team_n, side in ((home_n, "home"), (away_n, "away")):
            il_recs = il_placements_by_team.get(team_n, [])
            n_il = len(il_recs)
            names = ", ".join(r.player_name for r in il_recs[:5])
            if side == "home":
                rec.news_il_placements_home   = n_il
                rec.news_il_player_names_home = names
            else:
                rec.news_il_placements_away   = n_il
                rec.news_il_player_names_away = names
        sc = scratches_by_game.get(gpk, {})
        for side in ("home", "away"):
            ids = sc.get(side, [])
            n_total = len(ids)
            n_reg   = sum(1 for pid in ids if injury_news.is_regular(pid, pa_lookup))
            if side == "home":
                rec.news_lineup_scratches_home  = n_total
                rec.news_regular_scratches_home = n_reg
            else:
                rec.news_lineup_scratches_away  = n_total
                rec.news_regular_scratches_away = n_reg

        # Apply rules
        mp = float(row.get("model_prob", 0.5))
        _apply_rules(rec, mp, cfg)
        records.append(rec)

    # 4. Materialize columns + apply deltas to model_prob and tier.
    audit_rows = [asdict(r) for r in records]
    audit_df = pd.DataFrame(audit_rows)

    out = out.merge(
        audit_df.rename(columns={"game_pk": "game_id"})[
            ["game_id"] + [c for c in audit_df.columns
                           if c.startswith("news_") and c != "news_rules_fired"]
        ],
        on="game_id", how="left",
    )

    # Apply the signed delta to model_prob (clamped to [0,1]).
    if "news_model_prob_delta" in out.columns:
        out["model_prob_pre_news"] = out["model_prob"]
        out["model_prob"] = (out["model_prob"]
                              + out["news_model_prob_delta"].fillna(0.0)
                            ).clip(0.0, 1.0)

    # Apply tier demotion if a `tier` column already exists (it is added
    # downstream by edge_calculator; here we just stash the pending demotion
    # so recommend_slate can honor it).
    if "tier" in out.columns and "news_tier_demotion_steps" in out.columns:
        out["tier"] = [
            _demote_tier(t, int(d)) for t, d in
            zip(out["tier"].fillna("SKIP"), out["news_tier_demotion_steps"].fillna(0))
        ]

    n_fired = sum(1 for r in records if r.news_rules_fired)
    log.info("[live_news] %d/%d games triggered an override (anchor=%s)",
             n_fired, len(records),
             "first-run-of-day" if is_first_run else "loaded")
    return out, audit_df


def write_audit_log(audit_df: pd.DataFrame, slate_date: date) -> Optional[Path]:
    """Persist the per-game enrichment audit alongside the picks CSV."""
    if audit_df is None or audit_df.empty:
        return None
    p = Path(f"picks_{slate_date.isoformat()}_news_overrides.csv")
    try:
        # Drop list-typed column (rules_fired) — store as semicolon-joined str.
        a = audit_df.copy()
        if "news_rules_fired" in a.columns:
            a["news_rules_fired"] = a["news_rules_fired"].apply(
                lambda v: ";".join(v) if isinstance(v, list) else (v or "")
            )
        a.to_csv(p, index=False)
        log.info("[live_news] wrote audit log to %s", p)
        return p
    except Exception as e:
        log.warning("[live_news] audit log write failed: %s", e)
        return None
