"""
auto_weight_update.py
---------------------
Daily-cron entry point that closes the recursive-learning loop without any
manual CSV uploads. Runs the morning after each slate.

Workflow:
    1. Locate yesterday's `picks_<YYYY-MM-DD>.csv` (and the associated
       audit row, which contains the conviction signals).
    2. Pull yesterday's box scores from the MLB Stats API.
    3. Build a normalized outcomes DF
        [game_id, home_team, away_team, home_R, away_R].
    4. Call apply_calibration_from_all_picks to symmetrically update the
       persisted weights state file (`data/state/weights_state.json`).
    5. Append a structured JSONL audit entry to `recalibration_log.jsonl`
       so the season-long history is tracker-readable.

The script is idempotent: re-running with the same date is a no-op once the
audit log already contains that date.
"""
from __future__ import annotations

import argparse
import json
import logging
try:
    import fcntl  # POSIX only; not present on Windows
    _HAVE_FCNTL = True
except ImportError:
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from .config import SP_WEIGHTS
from .weights_state import (
    SIGNAL_TO_FEATURES, WEIGHTS_STATE_FILE,
    get_active_weights, _load_state, _save_state, _parse_signals,
)
from .stadiums import normalize_team

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

PICKS_GLOB = "picks_{date}.csv"
PICKS_DIAG_GLOB = "picks_{date}_diag.csv"
AUDIT_GLOB = "audit_{date}.csv"
AUDIT_LOG = Path("data/state/recalibration_log.jsonl")
LOCK_PATH = Path("data/state/auto_weight_update.lock")

# ---------------------------------------------------------------------------
# Calibration-from-all-picks (learn_from_all=True) constants
# ---------------------------------------------------------------------------
TIER_LEARN_WEIGHT: Dict[str, float] = {
    "PLATINUM": 1.0,
    "DIAMOND":  1.0,
    "GOLD":     0.8,
    "SILVER":   0.5,
    "BRONZE":   0.3,
    "SKIP":     0.1,
}

CALIB_LEARN_RATE: float = 0.04

# Safeguards (2026-05-25):
#   NEW_CEILING_MULT: weights can grow modestly past their initial
#     value. Previously ceil=base hard-clipped any upward update,
#     turning the loop into a one-sided decay rule.
#   STRESS_MASK_FACTOR: down-weights games the model itself flagged
#     as low-confidence (stress_warnings non-empty OR
#     confidence_downgrade=True) so their outcomes feed back less.
#   WARMUP_THRESHOLD: minimum cumulative learned-from observations
#     across audit history before updates apply. Self-healing:
#     blowing away the audit log re-engages probation automatically.
#     IMPORTANT: do not git-clean data/state/ without thinking.
NEW_CEILING_MULT: float = 1.5
STRESS_MASK_FACTOR: float = 0.3
WARMUP_THRESHOLD: int = 30


# ---------------------------------------------------------------------------
# Outcome ingestion
# ---------------------------------------------------------------------------
def fetch_outcomes(target_date: date) -> pd.DataFrame:
    """Return a DataFrame of the day's final scores."""
    try:
        r = requests.get(
            SCHEDULE_URL,
            params={
                "sportId": 1,
                "date": target_date.isoformat(),
                "hydrate": "linescore",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error("Schedule/outcomes fetch failed for %s: %s", target_date, e)
        return pd.DataFrame()

    rows: List[Dict] = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            state = (g.get("status", {}) or {}).get("detailedState", "")
            if state not in ("Final", "Game Over", "Completed Early"):
                continue
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            try:
                home_R = int(home.get("score", 0))
                away_R = int(away.get("score", 0))
            except (TypeError, ValueError):
                continue
            rows.append({
                "game_pk": int(g["gamePk"]),
                "home_abbr": normalize_team(home.get("team", {}).get("name", "")),
                "away_abbr": normalize_team(away.get("team", {}).get("name", "")),
                "home_R": home_R,
                "away_R": away_R,
                "status": state,
                "run_diff": abs(home_R - away_R),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Picks normalization
# ---------------------------------------------------------------------------
def _picks_to_recursive_schema(picks_df, audit_df):
    if picks_df.empty:
        return pd.DataFrame(columns=["game_id", "conv_tier",
                                      "conv_signals", "pick_winner"])
    out = picks_df.copy()
    rename = {"team": "pick_winner", "tier": "conv_tier", "signals": "conv_signals"}
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    if "conv_signals" not in out.columns and audit_df is not None and not audit_df.empty:
        a = audit_df.rename(columns={"signals": "conv_signals"})
        out = out.merge(a[["away", "home", "conv_signals"]],
                        left_on="team", right_on="home", how="left")
    if "conv_signals" not in out.columns:
        out["conv_signals"] = ""
    keep = ["game_id", "conv_tier", "conv_signals", "pick_winner"]
    return out[[c for c in keep if c in out.columns]]


def _outcomes_to_recursive_schema(outcomes_df, picks_df):
    if outcomes_df.empty:
        return pd.DataFrame(columns=["game_id", "home_team", "away_team",
                                      "home_R", "away_R"])
    home_view = outcomes_df.rename(columns={"home_abbr": "team"})
    away_view = outcomes_df.rename(columns={"away_abbr": "team"})
    long = pd.concat([
        home_view.assign(side="home"),
        away_view.assign(side="away"),
    ], ignore_index=True)
    if not picks_df.empty and "team" in picks_df.columns:
        picks_df = picks_df.copy()
        picks_df["team"] = picks_df["team"].apply(normalize_team)
    rows: List[Dict] = []
    for _, p in picks_df.iterrows():
        team = p.get("team") or p.get("pick_winner")
        if not isinstance(team, str):
            continue
        match = long[long["team"] == team]
        if match.empty:
            continue
        m = match.iloc[0]
        rows.append({
            "game_id": p.get("game_id", m["game_pk"]),
            "home_team": m["home_abbr"] if m["side"] == "home" else m["away_abbr"],
            "away_team": m["away_abbr"] if m["side"] == "home" else m["home_abbr"],
            "home_R": int(m["home_R"]) if m["side"] == "home" else int(m["away_R"]),
            "away_R": int(m["away_R"]) if m["side"] == "home" else int(m["home_R"]),
        })
    schema = ["game_id", "home_team", "away_team", "home_R", "away_R"]
    return pd.DataFrame(rows, columns=schema) if not rows else pd.DataFrame(rows)


def _picks_diag_to_calib_rows(picks_diag_df, outcomes_df):
    if picks_diag_df.empty or outcomes_df.empty:
        return pd.DataFrame(columns=["pick", "pick_prob", "tier",
                                      "signals", "won", "tier_weight"])
    df = picks_diag_df.copy()
    if "pick" in df.columns:
        df["pick_norm"] = df["pick"].astype(str).map(normalize_team)
    else:
        return pd.DataFrame(columns=["pick", "pick_prob", "tier",
                                      "signals", "won", "tier_weight"])
    long_rows: List[Dict] = []
    for _, o in outcomes_df.iterrows():
        h = normalize_team(o["home_abbr"])
        a = normalize_team(o["away_abbr"])
        h_won = int(o["home_R"]) > int(o["away_R"])
        long_rows.append({"team": h, "won": int(h_won),
                          "run_diff": int(o["run_diff"])})
        long_rows.append({"team": a, "won": int(not h_won),
                          "run_diff": int(o["run_diff"])})
    long = pd.DataFrame(long_rows)
    merged = df.merge(long, left_on="pick_norm", right_on="team", how="inner")
    if merged.empty:
        return pd.DataFrame(columns=["pick", "pick_prob", "tier",
                                      "signals", "won", "tier_weight"])
    merged["tier_weight"] = (merged["tier"]
                              .astype(str).str.upper()
                              .map(TIER_LEARN_WEIGHT)
                              .fillna(TIER_LEARN_WEIGHT["SKIP"]))
    out_cols = ["pick", "pick_prob", "p_model", "full_prob",
                "tier", "signals", "won", "tier_weight", "run_diff",
                "stress_warnings", "confidence_downgrade"]
    keep = [c for c in out_cols if c in merged.columns]
    return merged[keep].reset_index(drop=True)


def _total_learned_from_count() -> int:
    """Sum n_picks_used_for_learning across the entire audit log.

    Used by the warm-up gate. A missing/empty log returns 0, which
    structurally re-engages probation \u2014 desired behavior if the
    state is ever blown away. See WARMUP_THRESHOLD docstring.
    """
    if not AUDIT_LOG.exists():
        return 0
    total = 0
    try:
        with AUDIT_LOG.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    total += int(entry.get("n_picks_used_for_learning", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError:
        return 0
    return total


def apply_calibration_from_all_picks(
    picks_diag_df,
    outcomes_df,
    baseline_weights,
    learn_rate: float = CALIB_LEARN_RATE,
):
    state = _load_state(baseline_weights)
    rows = _picks_diag_to_calib_rows(picks_diag_df, outcomes_df)
    if rows.empty:
        return state, 0, 0
    # Schema evolution: pre-2026-05-04 diag CSVs have `p_model` (the ML
    # probability for the pick) but no `pick_prob`. From 2026-05-04 onward
    # both columns exist. Use whichever is present; fall back to full_prob.
    prob_col = None
    for cand in ("pick_prob", "p_model", "full_prob"):
        if cand in rows.columns:
            prob_col = cand
            break
    feature_grad: Dict[str, float] = {}
    n_with_signals = 0
    if prob_col is None:
        return state, int(len(rows)), 0
    # Warm-up gate: pass iff we have enough historical observations.
    # Backfilled audit log has ~125 obs, so this passes on day one.
    historical = _total_learned_from_count()
    audit_only = historical < WARMUP_THRESHOLD
    if audit_only:
        log.info(
            "[warmup] %d/%d learned-from obs in audit log \u2014 audit-only mode",
            historical, WARMUP_THRESHOLD,
        )

    for _, r in rows.iterrows():
        try:
            p = float(r.get(prob_col))
        except (TypeError, ValueError):
            continue
        won = int(r.get("won", 0))
        residual = won - p
        tw = float(r.get("tier_weight", TIER_LEARN_WEIGHT["SKIP"]))
        # Stress-warned mask: down-weight games the model itself
        # flagged as low-confidence. Either a non-empty
        # stress_warnings string OR confidence_downgrade=True
        # triggers the 0.3x multiplier.
        sw_raw = r.get("stress_warnings", "")
        sw = str(sw_raw).strip() if pd.notna(sw_raw) else ""
        cd_raw = r.get("confidence_downgrade", False)
        try:
            cd = bool(cd_raw) and str(cd_raw).strip().lower() not in ("false", "0", "")
        except Exception:
            cd = False
        if sw or cd:
            tw *= STRESS_MASK_FACTOR
        sigs = _parse_signals(r.get("signals", "") if pd.notna(r.get("signals", "")) else "")
        if not sigs:
            continue
        n_with_signals += 1
        for sig in sigs:
            for feat in SIGNAL_TO_FEATURES.get(sig, []):
                feature_grad[feat] = feature_grad.get(feat, 0.0) + tw * residual
    n_total = int(len(rows))
    if not feature_grad:
        return state, n_total, n_with_signals
    denom = max(1, n_with_signals)
    for feat, g in feature_grad.items():
        base = baseline_weights.get(feat, 1.0)
        floor = MIN_RELATIVE_WEIGHT * base
        # 2026-05-25: ceil bumped from `base` to `base * 1.5` so a
        # weight that was under-credited at init can recover. Prior
        # behavior was a one-sided decay rule (could shrink to 25%
        # of base, but never grow past base).
        ceil  = base * NEW_CEILING_MULT
        delta_mult = 1.0 + learn_rate * (g / denom)
        cur = state.get(feat, base)
        new = cur * delta_mult
        if new < floor: new = floor
        if new > ceil: new = ceil
        if not audit_only:
            state[feat] = new
        # If audit_only, state[feat] stays at cur and the audit
        # entry will record a zero delta. The new value is still
        # written to the proposed_state dict below for observability.
    if not audit_only:
        _save_state(state)
    return state, n_total, n_with_signals


def _write_audit_entry(target_date, picks_df, outcomes_df,
                        prev_state, new_state,
                        learn_mode="no_learn",
                        n_picks_total=None,
                        n_picks_used_for_learning=None):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    n_bets = len(picks_df) if not picks_df.empty else 0
    if n_bets and not outcomes_df.empty:
        merged = picks_df.merge(outcomes_df.rename(
            columns={"home_abbr": "home_team", "away_abbr": "away_team"}),
            left_on="team", right_on="home_team", how="left"
        )
        wins = ((merged["home_R"].fillna(-1) > merged["away_R"].fillna(-2))).sum()
    else:
        wins = 0
    deltas = {k: round(new_state.get(k, 1.0) - prev_state.get(k, 1.0), 6)
              for k in set(prev_state) | set(new_state)}
    # Safeguard observability (2026-05-25): surface the largest
    # single-weight move + any weight that grew past its baseline
    # in this update. weights_growing_past_prior should be empty
    # for the first ~10 days under the new ceil=1.5*base rule
    # since most weights are well below their priors.
    # 2026-05-26: was a try/except import from recursive_weight_update
    # which always raised (recursive_weight_update never defined
    # SP_WEIGHTS). Direct reference to the already-imported config
    # constant makes the safeguard fields actually populate.
    _BASELINES = SP_WEIGHTS
    max_change_pct = 0.0
    growing_past_prior: List[str] = []
    runaway_alarm = False
    runaway_features: List[str] = []
    for k, d in deltas.items():
        prev_v = prev_state.get(k, 1.0)
        if prev_v:
            pct = abs(d) / abs(prev_v)
            if pct > max_change_pct:
                max_change_pct = pct
        new_v = new_state.get(k, prev_v)
        base_v = _BASELINES.get(k)
        if base_v is not None and new_v > base_v:
            growing_past_prior.append(k)
        # Runaway tripwire (2026-05-25): any weight >= 1.4 * base
        # is 10pp from the new 1.5 * base ceiling and signals
        # potential signal-stacking that magnitude weighting
        # (Phase 4) would address. Flag it loudly.
        if base_v is not None and new_v >= 1.4 * base_v:
            runaway_alarm = True
            runaway_features.append(k)
    entry: Dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slate_date": target_date.isoformat(),
        "n_bets": int(n_bets),
        "wins": int(wins),
        "learn_mode": learn_mode,
        "weight_deltas": deltas,
        "max_weight_change_pct": round(max_change_pct, 6),
        "weights_growing_past_prior": growing_past_prior,
        "runaway_ceiling_alarm": runaway_alarm,
        "runaway_features": runaway_features,
        "new_state": {k: round(v, 6) for k, v in new_state.items()},
    }
    if n_picks_total is not None:
        entry["n_picks_total"] = int(n_picks_total)
    if n_picks_used_for_learning is not None:
        entry["n_picks_used_for_learning"] = int(n_picks_used_for_learning)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("Wrote audit entry for %s (mode=%s, n_bets=%d, wins=%d)",
             target_date, learn_mode, n_bets, wins)
    if runaway_alarm:
        log.warning(
            "[runaway-ceiling-alarm] %s: weights >= 1.4 * base: %s",
            target_date, runaway_features,
        )


def _already_processed(target_date):
    if not AUDIT_LOG.exists():
        return False
    needle = f'"slate_date": "{target_date.isoformat()}"'
    try:
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            if needle in line:
                return True
    except Exception:
        return False
    return False


def run(target_date,
        picks_dir=Path("."),
        force=False,
        learn_from_all=True,
        dry_run=False):
    log.info("=== AUTO-WEIGHT-UPDATE: %s (learn_from_all=%s, dry_run=%s) ===",
             target_date, learn_from_all, dry_run)

    if not dry_run and _already_processed(target_date) and not force:
        log.info("Audit log already contains %s — skipping (use --force to redo)",
                 target_date)
        return get_active_weights(SP_WEIGHTS)

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_fp = LOCK_PATH.open("a+")
    try:
        if _HAVE_FCNTL and not dry_run:
            fcntl.flock(_lock_fp.fileno(), fcntl.LOCK_EX)
        if not dry_run and _already_processed(target_date) and not force:
            log.info("Audit log contained %s by the time we got the lock — skipping", target_date)
            return get_active_weights(SP_WEIGHTS)

        picks_path = picks_dir / PICKS_GLOB.format(date=target_date.isoformat())
        diag_path = picks_dir / PICKS_DIAG_GLOB.format(date=target_date.isoformat())
        audit_path = picks_dir / AUDIT_GLOB.format(date=target_date.isoformat())

        picks_df = pd.read_csv(picks_path) if picks_path.exists() else pd.DataFrame()
        diag_df  = pd.read_csv(diag_path) if diag_path.exists() else pd.DataFrame()
        audit_df = pd.read_csv(audit_path) if audit_path.exists() else None

        if picks_df.empty and diag_df.empty:
            log.warning("No picks file at %s and no diag at %s — nothing to score",
                        picks_path, diag_path)
            prev = get_active_weights(SP_WEIGHTS)
            if not dry_run:
                _write_audit_entry(target_date, pd.DataFrame(), pd.DataFrame(),
                                   prev, prev,
                                   learn_mode="no_picks",
                                   n_picks_total=0,
                                   n_picks_used_for_learning=0)
            return prev

        outcomes_df = fetch_outcomes(target_date)
        if outcomes_df.empty:
            log.warning("No completed games found for %s", target_date)
            prev = get_active_weights(SP_WEIGHTS)
            if not dry_run:
                _write_audit_entry(target_date, picks_df, outcomes_df, prev, prev,
                                   learn_mode="no_outcomes",
                                   n_picks_total=int(len(diag_df)) if not diag_df.empty else 0,
                                   n_picks_used_for_learning=0)
            return prev

        prev_state = get_active_weights(SP_WEIGHTS)

        # 2026-05-26: legacy apply_blowout_penalties chain removed.
        # See data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/
        # for the evidence: our losses go to blowouts at 31.9% vs MLB
        # baseline 30.1% — blowouts are bullpen variance, not signal
        # failure. apply_calibration_from_all_picks is now the sole
        # learning path. The daily +/-4% gradient cap is now a hard
        # invariant (previously the blowout shock at -15% per bust
        # could exceed it on qualifying slates).
        n_picks_total = 0
        n_picks_used_for_learning = 0
        if learn_from_all and not diag_df.empty:
            if dry_run:
                _orig_state_text = None
                _WSF = WEIGHTS_STATE_FILE
                if _WSF.exists():
                    _orig_state_text = _WSF.read_text(encoding="utf-8")
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
                if _orig_state_text is not None:
                    _WSF.write_text(_orig_state_text, encoding="utf-8")
                else:
                    try: _WSF.unlink()
                    except FileNotFoundError: pass
            else:
                new_state, n_picks_total, n_picks_used_for_learning = (
                    apply_calibration_from_all_picks(
                        diag_df, outcomes_df, SP_WEIGHTS))
            learn_mode = "all_picks_tier_weighted"
        else:
            new_state = prev_state
            n_picks_total = int(len(diag_df)) if not diag_df.empty else 0
            learn_mode = "no_learn"

        if not dry_run:
            _write_audit_entry(target_date, picks_df, outcomes_df,
                               prev_state, new_state,
                               learn_mode=learn_mode,
                               n_picks_total=n_picks_total,
                               n_picks_used_for_learning=n_picks_used_for_learning)

        print(f"\n=== WEIGHT DELTAS ({learn_mode}, "
              f"n_picks_total={n_picks_total}, "
              f"n_learned_from={n_picks_used_for_learning}, "
              f"dry_run={dry_run}) ===")
        base = SP_WEIGHTS
        moved_any = False
        for k in sorted(set(prev_state) | set(new_state)):
            b = base.get(k, 1.0)
            before = prev_state.get(k, b)
            after = new_state.get(k, b)
            if abs(after - before) > 1e-6:
                moved_any = True
                pct = 100.0 * (after - before) / before if before else 0.0
                print(f"  {k}: {before:.4f} -> {after:.4f}  ({pct:+.2f}%)")
        if not moved_any:
            print("  (no weights moved)")
        print()
        return new_state
    finally:
        try:
            if _HAVE_FCNTL and not dry_run:
                fcntl.flock(_lock_fp.fileno(), fcntl.LOCK_UN)
            _lock_fp.close()
        except Exception:
            pass


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--picks-dir", default=".")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-learn-from-all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.date:
        td = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        td = date.today()
    run(td, picks_dir=Path(args.picks_dir), force=args.force,
        learn_from_all=not args.no_learn_from_all,
        dry_run=args.dry_run)


if __name__ == "__main__":
    main()
