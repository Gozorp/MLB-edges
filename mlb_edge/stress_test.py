"""Stress-test / anomaly audit for individual game picks (2026-05-03).

Codifies the manual edge/blowout audit that's been done in narrative form on
recent slates (`full_audit_2026-04-29.md` onward) into a single pure function
that can be invoked from the audit script and from the live predict pipeline.

Two-step audit:

  STEP 1  EDGE VARIANCE CHECK
    - Thin edge: |edge_pp| < 3.5pp  →  edge_check_pass=False
    - Tier-historical noise: rolling 30d Brier residual std on this tier
      from data/state/recalibration_log.jsonl. If thin edge AND tier std > 0.35
      (or insufficient history — fail-conservative), force confidence_downgrade.

  STEP 2  BLOWOUT VULNERABILITY AUDIT  (4 dimensions)
    - acute_roster:        ≥3 IL hits on pick side, OR same-day SP scratch
    - bullpen_fatigue:     pick-side bp_min < 1500, OR fatigue_gap < -0.4
                           against pick, OR bullpen_short_<pick> news rule
    - short_term_skill:    pick-side trailing-14d team xwOBA > 0.030 below
                           season-to-date AND opponent > 0.030 above
    - external_modifiers:  wind ≥15mph blowing out in HR-park (>=1.05) toward
                           the underdog; OR plate ump K%/BB% delta > 0.5
                           favoring underdog playstyle

  Each dimension returns one of {pass, flag, unknown}. A vulnerability is
  "fired" iff its state is `flag`. `unknown` means data wasn't available
  (e.g., trailing-14d cache miss, ump unassigned) — does NOT count as a flag.

OUTPUT:
    StressAuditResult dataclass, see below.

Designed for observability v1: this module does NOT change any tier or stake.
The audit script renders it; predict.py logs the columns to diag CSV.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Module-level constants — the durable thresholds. Change these only with
# a corresponding update to stress_test_implementation.md.
EDGE_NOISE_BAND_PP = 3.5         # Step 1 thin-edge floor
TIER_NOISE_STD_FLOOR = 0.35      # Step 1 historical Brier residual std cap
ACUTE_ROSTER_IL_FLOOR = 3        # Step 2.a IL count to flag
BULLPEN_BP_MIN_FLOOR = 1500      # Step 2.b 72h pitches floor
BULLPEN_FATIGUE_GAP_FLOOR = -0.4 # Step 2.b fatigue gap floor (against pick)
ST_SKILL_DELTA = 0.030           # Step 2.c xwOBA delta vs season for "acute"
EXT_WIND_MPH = 15.0              # Step 2.d wind speed for "extreme"
EXT_HR_PARK = 1.05               # Step 2.d HR-friendly park threshold
EXT_UMP_BIAS = 0.5               # Step 2.d ump K%/BB% delta to flag

VULNERABILITY_LABELS = (
    "acute_roster", "bullpen_fatigue",
    "short_term_skill", "external_modifiers",
)


@dataclass
class StressAuditResult:
    """Output of audit_pick. All fields populated; fields whose check
    couldn't be made carry state='unknown' instead of fabricated values."""
    edge_check_pass: bool             # |edge_pp| >= 3.5
    edge_pp: float                    # the edge that was audited
    vulnerabilities: List[str] = field(default_factory=list)  # subset of
    # VULNERABILITY_LABELS, only labels in state='flag'
    confidence_downgrade: bool = False
    warning_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    # `details` carries: per-dimension state ('pass'/'flag'/'unknown'),
    # the values that drove each verdict (so audit cards can show the why).


# ---------------------------------------------------------------------------
# Step 1 helpers — edge + tier-historical noise
# ---------------------------------------------------------------------------
def _tier_brier_residual_std(
    tier: str,
    log_path: Path = Path(r"D:\mlb_edge\mlb_edge\data\state\recalibration_log.jsonl"),
    window_days: int = 30,
) -> Optional[float]:
    """Rolling 30d std of Brier residual on this tier from the recal log.
    Returns None if insufficient history (<10 entries) — caller fail-conservative."""
    if not log_path.exists():
        return None
    try:
        cutoff = datetime.now().astimezone() - timedelta(days=window_days)
        residuals: List[float] = []
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = row.get("ts", "")
            try:
                row_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if row_ts < cutoff:
                continue
            # Tier-specific signal: if the row carries per-tier stats, use them.
            # Currently the recal log persists `n_bets` and `wins` only — not
            # per-tier — so we approximate residual from win-rate vs 0.55 baseline.
            n = row.get("n_bets") or 0
            w = row.get("wins") or 0
            if n <= 0:
                continue
            # Approximate Brier residual as |(wins/n) - 0.55|^2; this is a
            # scale-free proxy until the recal log persists per-tier Brier.
            residuals.append(abs(w / n - 0.55) ** 2)
        if len(residuals) < 10:
            return None
        return float(np.std(residuals))
    except Exception as e:  # noqa: BLE001 — defensive
        log.warning("[stress_test] tier-noise compute failed: %s", e)
        return None


def _step1_edge_variance(edge_pp: float, tier: str) -> Dict[str, Any]:
    """Two outputs:
      - thin_edge: |edge| < 3.5pp. Per spec 1.2, this alone "MUST downgrade
        confidence to Low/No Play". Drives `confidence_downgrade` in caller.
      - tier_confidence_blocked: thin_edge AND historical Brier residual std
        on this tier > 0.35. Per spec 1.3, the stricter rule forbids labeling
        the pick "Confident". Currently observability-only; production tier
        is not modified by this layer.
    """
    edge_clears_band = abs(edge_pp) >= EDGE_NOISE_BAND_PP
    tier_std = _tier_brier_residual_std(tier)
    # Fail-conservative: if we can't compute tier_std, treat as exceeding floor
    tier_std_exceeds = (tier_std is None) or (tier_std > TIER_NOISE_STD_FLOOR)
    tier_confidence_blocked = (not edge_clears_band) and tier_std_exceeds
    return {
        "edge_clears_band": edge_clears_band,
        "edge_pp": float(edge_pp),
        "tier_brier_std_30d": tier_std,
        "tier_std_exceeds_floor": tier_std_exceeds,
        "tier_confidence_blocked": tier_confidence_blocked,
    }


# ---------------------------------------------------------------------------
# Step 2.a — Acute roster
# ---------------------------------------------------------------------------
def _step2_acute_roster(
    pick_side: str,            # 'home' or 'away'
    news_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "unknown"}
    if not news_row:
        return out
    # IL placements on PICK side
    il_pick_key = f"news_il_placements_{pick_side}"
    il_opp_key = f"news_il_placements_{'away' if pick_side == 'home' else 'home'}"
    try:
        il_pick = int(news_row.get(il_pick_key) or 0)
        il_opp = int(news_row.get(il_opp_key) or 0)
    except (TypeError, ValueError):
        il_pick = il_opp = 0
    scratch_home = str(news_row.get("news_sp_late_scratch_home", "")).lower() == "true"
    scratch_away = str(news_row.get("news_sp_late_scratch_away", "")).lower() == "true"
    pick_scratched = scratch_home if pick_side == "home" else scratch_away
    opp_scratched = scratch_away if pick_side == "home" else scratch_home
    flagged = (il_pick >= ACUTE_ROSTER_IL_FLOOR) or pick_scratched or opp_scratched
    out.update({
        "state": "flag" if flagged else "pass",
        "il_pick_side": il_pick,
        "il_opp_side": il_opp,
        "sp_scratch_pick": pick_scratched,
        "sp_scratch_opp": opp_scratched,
    })
    return out


# ---------------------------------------------------------------------------
# Step 2.b — Bullpen fatigue
# ---------------------------------------------------------------------------
def _step2_bullpen_fatigue(
    pick_side: str,
    bp_state: Dict[str, Any],
    news_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "unknown"}
    pick_bp_n = bp_state.get(f"{pick_side}_bullpen_n_pitches")
    fatigue_gap = bp_state.get("bullpen_fatigue_gap")  # +ve favors home
    bp_short_news = False
    if news_row:
        key = f"news_bullpen_short_{pick_side}"
        bp_short_news = str(news_row.get(key, "")).lower() == "true"
    # Convert fatigue_gap to "against pick": for home pick, gap should be +ve;
    # the rule says flag if fatigue_gap < -0.4 *against the pick side*.
    against_pick: Optional[float]
    if fatigue_gap is None or pd.isna(fatigue_gap):
        against_pick = None
    else:
        against_pick = -float(fatigue_gap) if pick_side == "home" else float(fatigue_gap)
    # State logic
    have_any_signal = (pick_bp_n is not None) or (against_pick is not None) or bp_short_news
    if not have_any_signal:
        return out
    flagged = False
    if pick_bp_n is not None and not pd.isna(pick_bp_n):
        if float(pick_bp_n) < BULLPEN_BP_MIN_FLOOR:
            flagged = True
    if against_pick is not None and against_pick < BULLPEN_FATIGUE_GAP_FLOOR:
        flagged = True
    if bp_short_news:
        flagged = True
    out.update({
        "state": "flag" if flagged else "pass",
        "pick_side_bp_n_pitches": (None if pick_bp_n is None or pd.isna(pick_bp_n)
                                    else float(pick_bp_n)),
        "fatigue_gap_against_pick": against_pick,
        "news_bullpen_short_pick": bp_short_news,
    })
    return out


# ---------------------------------------------------------------------------
# Step 2.c — Short-term skill (trailing-14d team xwOBA vs season)
# ---------------------------------------------------------------------------
_TEAM_XWOBA_CACHE: Dict[str, Dict[str, float]] = {}

# Team-abbreviation aliases for cross-source joins. Statcast uses "ATH" for
# the post-relocation Athletics in 2026 while slate frames still emit "OAK"
# in some places; we collapse both to a canonical key. Add other observed
# discrepancies here as they surface (e.g., CWS/CHW historically).
_TEAM_ALIASES: Dict[str, str] = {
    "OAK": "ATH", "ATH": "ATH",
    "CWS": "CWS", "CHW": "CWS",
    "AZ": "AZ", "ARI": "AZ",
}


def _canon_team(abbr: str) -> str:
    return _TEAM_ALIASES.get(abbr, abbr)


def _trailing_team_xwoba(
    target_date: date,
    statcast_dir: Path = Path(r"D:\mlb_edge\mlb_edge\data\statcast_cache\statcast_chunk"),
    window_days: int = 14,
) -> Dict[str, Dict[str, float]]:
    """Return {abbr: {"xwoba_14d": ..., "xwoba_season": ...}} for all teams
    with usable data. Cached per target_date."""
    cache_key = target_date.isoformat()
    if cache_key in _TEAM_XWOBA_CACHE:
        return _TEAM_XWOBA_CACHE[cache_key]
    if not statcast_dir.exists():
        _TEAM_XWOBA_CACHE[cache_key] = {}
        return {}
    season_start = date(target_date.year, 3, 20)
    window_start = target_date - timedelta(days=window_days)
    # Scan chunk files for relevant date ranges
    relevant_chunks: List[Path] = []
    for p in statcast_dir.glob("*.parquet"):
        try:
            df_dates = pd.read_parquet(p, columns=["game_date"])
            if df_dates.empty:
                continue
            min_d = pd.to_datetime(df_dates["game_date"].min()).date()
            max_d = pd.to_datetime(df_dates["game_date"].max()).date()
            if max_d < season_start or min_d > target_date:
                continue
            relevant_chunks.append(p)
        except Exception:
            continue
    if not relevant_chunks:
        _TEAM_XWOBA_CACHE[cache_key] = {}
        return {}
    cols = ["game_date", "home_team", "away_team", "estimated_woba_using_speedangle",
            "woba_value", "woba_denom", "inning_topbot"]
    frames = []
    for p in relevant_chunks:
        try:
            frames.append(pd.read_parquet(p, columns=cols))
        except Exception as e:
            log.debug("[stress_test] skip chunk %s: %s", p.name, e)
            continue
    if not frames:
        _TEAM_XWOBA_CACHE[cache_key] = {}
        return {}
    df = pd.concat(frames, ignore_index=True)
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
    df = df[(df["game_date"] >= season_start) & (df["game_date"] < target_date)].copy()
    if df.empty:
        _TEAM_XWOBA_CACHE[cache_key] = {}
        return {}
    df["bat_team"] = np.where(df["inning_topbot"] == "Top", df["away_team"], df["home_team"])
    df["bat_team"] = df["bat_team"].map(lambda t: _canon_team(str(t)))
    df["xwoba"] = pd.to_numeric(df["estimated_woba_using_speedangle"], errors="coerce")
    df["denom"] = pd.to_numeric(df["woba_denom"], errors="coerce").fillna(0)
    df = df.dropna(subset=["xwoba"])

    def _team_xwoba(slice_df: pd.DataFrame) -> Dict[str, float]:
        agg = slice_df.groupby("bat_team").apply(
            lambda g: float((g["xwoba"] * g["denom"]).sum() / max(g["denom"].sum(), 1e-9))
            if g["denom"].sum() > 0 else float("nan"))
        return {k: float(v) for k, v in agg.items() if pd.notna(v)}

    season_x = _team_xwoba(df)
    win = df[df["game_date"] >= window_start]
    win_x = _team_xwoba(win) if not win.empty else {}
    out: Dict[str, Dict[str, float]] = {}
    for abbr, sx in season_x.items():
        out[abbr] = {"xwoba_season": sx,
                     "xwoba_14d": win_x.get(abbr, float("nan"))}
    _TEAM_XWOBA_CACHE[cache_key] = out
    return out


def _step2_short_term_skill(
    pick_team: str,
    opp_team: str,
    target_date: date,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "unknown"}
    try:
        xwobas = _trailing_team_xwoba(target_date)
    except Exception as e:
        log.warning("[stress_test] trailing-14d compute failed: %s", e)
        return out
    pick_key = _canon_team(pick_team)
    opp_key = _canon_team(opp_team)
    if not xwobas or pick_key not in xwobas or opp_key not in xwobas:
        return out
    pick = xwobas[pick_key]
    opp = xwobas[opp_key]
    if any(pd.isna(v) for v in (pick.get("xwoba_14d", float("nan")),
                                pick.get("xwoba_season", float("nan")),
                                opp.get("xwoba_14d", float("nan")),
                                opp.get("xwoba_season", float("nan")))):
        return out
    pick_delta = pick["xwoba_14d"] - pick["xwoba_season"]
    opp_delta = opp["xwoba_14d"] - opp["xwoba_season"]
    flagged = (pick_delta < -ST_SKILL_DELTA) and (opp_delta > ST_SKILL_DELTA)
    out.update({
        "state": "flag" if flagged else "pass",
        "pick_xwoba_14d": pick["xwoba_14d"],
        "pick_xwoba_season": pick["xwoba_season"],
        "pick_delta": pick_delta,
        "opp_xwoba_14d": opp["xwoba_14d"],
        "opp_xwoba_season": opp["xwoba_season"],
        "opp_delta": opp_delta,
    })
    return out


# ---------------------------------------------------------------------------
# Step 2.d — External modifiers (wind + ump)
# ---------------------------------------------------------------------------
def _step2_external_modifiers(
    pick_side: str,
    weather: Dict[str, Any],
    ump: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": "unknown"}
    wind_out = weather.get("wind_out_mph")
    park_hr = weather.get("park_hr_factor")
    if wind_out is None or park_hr is None or pd.isna(wind_out) or pd.isna(park_hr):
        wind_flag: Optional[bool] = None
    else:
        # Wind blowing OUT (positive) ≥ 15mph in HR-friendly park favors hitters,
        # which is structurally an underdog signal (book under-prices weather
        # variance). Pick is the favorite by construction (model_prob ≥ 0.5);
        # this flag fires regardless of which side is favored — the variance
        # is the issue, not the direction.
        wind_flag = (float(wind_out) >= EXT_WIND_MPH) and (float(park_hr) >= EXT_HR_PARK)
    ump_flag: Optional[bool] = None
    if ump and ump.get("k_pct_delta") is not None:
        try:
            k = float(ump.get("k_pct_delta") or 0)
            bb = float(ump.get("bb_pct_delta") or 0)
            ump_flag = (abs(k) > EXT_UMP_BIAS) or (abs(bb) > EXT_UMP_BIAS)
        except (TypeError, ValueError):
            ump_flag = None
    # Combine: unknown if BOTH inputs are unknown; otherwise flag if either
    # signal fires.
    if wind_flag is None and ump_flag is None:
        return out
    flagged = bool(wind_flag) or bool(ump_flag)
    out.update({
        "state": "flag" if flagged else "pass",
        "wind_out_mph": (None if wind_out is None or pd.isna(wind_out) else float(wind_out)),
        "park_hr_factor": (None if park_hr is None or pd.isna(park_hr) else float(park_hr)),
        "wind_flag": wind_flag,
        "ump_k_pct_delta": (None if ump is None else ump.get("k_pct_delta")),
        "ump_bb_pct_delta": (None if ump is None else ump.get("bb_pct_delta")),
        "ump_flag": ump_flag,
    })
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def audit_pick(
    *,
    edge_pp: float,
    tier: str,
    pick_team: str,
    opp_team: str,
    pick_side: str,                          # 'home' or 'away'
    target_date: date,
    bp_state: Optional[Dict[str, Any]] = None,
    weather: Optional[Dict[str, Any]] = None,
    news_row: Optional[Dict[str, Any]] = None,
    ump: Optional[Dict[str, Any]] = None,
) -> StressAuditResult:
    """Audit a single pick. See module docstring for semantics.

    All inputs are kwargs to make wrapping robust to schema changes; missing
    inputs surface as `unknown` per-dimension rather than silent passes.
    """
    bp_state = bp_state or {}
    weather = weather or {}

    s1 = _step1_edge_variance(edge_pp, tier)
    a = _step2_acute_roster(pick_side, news_row)
    b = _step2_bullpen_fatigue(pick_side, bp_state, news_row)
    c = _step2_short_term_skill(pick_team, opp_team, target_date)
    d = _step2_external_modifiers(pick_side, weather, ump)

    per_dim = {
        "acute_roster": a, "bullpen_fatigue": b,
        "short_term_skill": c, "external_modifiers": d,
    }
    vulnerabilities = [k for k, v in per_dim.items() if v.get("state") == "flag"]
    edge_pass = bool(s1["edge_clears_band"])
    # Per spec OUTPUT REQUIREMENT: warning fires on (thin edge OR any vuln).
    # The stricter "tier_confidence_blocked" is exposed in details for callers
    # that want to apply the harder "never label Confident" rule.
    confidence_downgrade = (not edge_pass) or len(vulnerabilities) > 0
    if confidence_downgrade:
        warning = "WARNING: High Variance / Process Blindspot Detected"
    else:
        warning = ""

    return StressAuditResult(
        edge_check_pass=edge_pass,
        edge_pp=float(edge_pp),
        vulnerabilities=vulnerabilities,
        confidence_downgrade=confidence_downgrade,
        warning_message=warning,
        details={
            "step1": s1,
            "acute_roster": a, "bullpen_fatigue": b,
            "short_term_skill": c, "external_modifiers": d,
        },
    )


def format_audit_block(r: StressAuditResult) -> List[str]:
    """Render the audit as Markdown bullets for inclusion in audit cards.
    Returns a list of lines (no trailing newlines)."""
    lines = ["**Stress test:**"]
    edge_check = ("✅" if r.edge_check_pass else "⚠️ FAIL")
    edge_phrase = (f"{edge_check} {r.edge_pp:+.2f}pp "
                   f"{'clears' if r.edge_check_pass else 'inside'} "
                   f"{EDGE_NOISE_BAND_PP}pp band")
    lines.append(f"- Edge check: {edge_phrase}")
    n_known = sum(1 for d in ("acute_roster", "bullpen_fatigue",
                              "short_term_skill", "external_modifiers")
                  if r.details.get(d, {}).get("state") in ("pass", "flag"))
    n_flag = len(r.vulnerabilities)
    if r.vulnerabilities:
        lines.append(
            f"- Vulnerabilities: {', '.join(r.vulnerabilities)} "
            f"({n_flag} of 4)"
        )
    elif n_known == 4:
        lines.append("- Vulnerabilities: ✅ all 4 dimensions clear")
    else:
        # Some dimensions are 'unknown'
        unk = [d for d in VULNERABILITY_LABELS
               if r.details.get(d, {}).get("state") == "unknown"]
        if unk:
            lines.append(f"- Vulnerabilities: ✅ {n_known - n_flag} clear, "
                         f"{len(unk)} unknown ({', '.join(unk)})")
        else:
            lines.append("- Vulnerabilities: ✅ all 4 dimensions clear")
    if r.warning_message:
        lines.append(f"- {r.warning_message} — recommend No Play")
    return lines
