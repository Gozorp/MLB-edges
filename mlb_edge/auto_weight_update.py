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
    4. Reuse `recursive_weight_update.apply_blowout_penalties` to update the
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
from typing import Dict, List, Optional

import pandas as pd
import requests

from .config import SP_WEIGHTS
from .recursive_weight_update import (
    BLOWOUT_RUN_DIFF, BLOWOUT_TIERS_PENALIZED,
    PENALTY_PER_BLOWOUT, RECOVERY_PER_GOOD_DAY,
    apply_blowout_penalties, get_active_weights,
)
from .stadiums import normalize_team

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

PICKS_GLOB = "picks_{date}.csv"
AUDIT_GLOB = "audit_{date}.csv"
AUDIT_LOG = Path("data/state/recalibration_log.jsonl")
LOCK_PATH = Path("data/state/auto_weight_update.lock")


# ---------------------------------------------------------------------------
# Outcome ingestion
# ---------------------------------------------------------------------------
def fetch_outcomes(target_date: date) -> pd.DataFrame:
    """Return a DataFrame of the day's final scores.

    Schema: [game_pk, home_abbr, away_abbr, home_R, away_R, status, run_diff]
    """
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
def _picks_to_recursive_schema(picks_df: pd.DataFrame,
                               audit_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Adapt the pipeline's picks CSV into the schema the recursive
    weight-update function expects:
        [game_id, conv_tier, conv_signals, pick_winner]
    """
    if picks_df.empty:
        return pd.DataFrame(columns=["game_id", "conv_tier",
                                      "conv_signals", "pick_winner"])

    out = picks_df.copy()
    rename = {"team": "pick_winner", "tier": "conv_tier", "signals": "conv_signals"}
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    if "conv_signals" not in out.columns and audit_df is not None and not audit_df.empty:
        # Pull signals from the audit file by joining on game side
        # (audit has one row per game; picks one row per recommended bet)
        a = audit_df.rename(columns={"signals": "conv_signals"})
        out = out.merge(a[["away", "home", "conv_signals"]],
                        left_on="team", right_on="home", how="left")
    if "conv_signals" not in out.columns:
        out["conv_signals"] = ""
    keep = ["game_id", "conv_tier", "conv_signals", "pick_winner"]
    return out[[c for c in keep if c in out.columns]]


def _outcomes_to_recursive_schema(outcomes_df: pd.DataFrame,
                                  picks_df: pd.DataFrame) -> pd.DataFrame:
    """Match outcomes against picks by team — `picks_<date>.csv` does not
    typically carry game_pk, so we fall back to (date, picked_team)."""
    if outcomes_df.empty:
        return pd.DataFrame(columns=["game_id", "home_team", "away_team",
                                      "home_R", "away_R"])

    # Long → indexable by team
    home_view = outcomes_df.rename(columns={"home_abbr": "team"})
    away_view = outcomes_df.rename(columns={"away_abbr": "team"})
    long = pd.concat([
        home_view.assign(side="home"),
        away_view.assign(side="away"),
    ], ignore_index=True)

    # The picks CSV uses long names (e.g. "Texas Rangers") in some pipeline
    # versions — normalize before joining.
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


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _write_audit_entry(target_date: date,
                        picks_df: pd.DataFrame,
                        outcomes_df: pd.DataFrame,
                        prev_state: Dict[str, float],
                        new_state: Dict[str, float]) -> None:
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
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slate_date": target_date.isoformat(),
        "n_bets": int(n_bets),
        "wins": int(wins),
        "weight_deltas": deltas,
        "new_state": {k: round(v, 6) for k, v in new_state.items()},
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("Wrote audit entry for %s (n_bets=%d, wins=%d)",
             target_date, n_bets, wins)


def _already_processed(target_date: date) -> bool:
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


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run(target_date: date,
        picks_dir: Path = Path("."),
        force: bool = False) -> Dict[str, float]:
    """Process `target_date`'s slate and update weights state.

    Idempotent across concurrent invocations:
      1. Cheap pre-lock check against the audit log.
      2. Per-process flock so two cron fires that started within
         milliseconds of each other can't both pass the dedup check.
      3. Post-lock re-check in case another process won the race while
         we were waiting for the lock.
    """
    log.info("=== AUTO-WEIGHT-UPDATE: %s ===", target_date)

    # Cheap pre-lock check — most calls bail here with no lock contention.
    if _already_processed(target_date) and not force:
        log.info("Audit log already contains %s — skipping (use --force to redo)",
                 target_date)
        return get_active_weights(SP_WEIGHTS)

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_fp = LOCK_PATH.open("a+")
    try:
        if _HAVE_FCNTL:
            fcntl.flock(_lock_fp.fileno(), fcntl.LOCK_EX)

        # Post-lock re-check — another process may have slipped in between
        # the pre-lock check above and us acquiring the lock.
        if _already_processed(target_date) and not force:
            log.info("Audit log contained %s by the time we got the lock "
                     "— skipping duplicate write", target_date)
            return get_active_weights(SP_WEIGHTS)

        picks_path = picks_dir / PICKS_GLOB.format(date=target_date.isoformat())
        audit_path = picks_dir / AUDIT_GLOB.format(date=target_date.isoformat())

        if not picks_path.exists():
            log.warning("No picks file at %s — nothing to score", picks_path)
            prev = get_active_weights(SP_WEIGHTS)
            _write_audit_entry(target_date, pd.DataFrame(), pd.DataFrame(),
                               prev, prev)
            return prev

        picks_df = pd.read_csv(picks_path)
        audit_df = pd.read_csv(audit_path) if audit_path.exists() else None

        outcomes_df = fetch_outcomes(target_date)
        if outcomes_df.empty:
            log.warning("No completed games found for %s", target_date)
            prev = get_active_weights(SP_WEIGHTS)
            _write_audit_entry(target_date, picks_df, outcomes_df, prev, prev)
            return prev

        picks_norm = _picks_to_recursive_schema(picks_df, audit_df)
        outcomes_norm = _outcomes_to_recursive_schema(outcomes_df, picks_df)

        prev_state = get_active_weights(SP_WEIGHTS)
        new_state = apply_blowout_penalties(picks_norm, outcomes_norm, SP_WEIGHTS)

        _write_audit_entry(target_date, picks_df, outcomes_df,
                           prev_state, new_state)

        # Console summary
        print("\n=== WEIGHT DELTAS ===")
        base = SP_WEIGHTS
        for k in sorted(set(prev_state) | set(new_state)):
            b = base.get(k, 1.0)
            before = prev_state.get(k, b)
            after = new_state.get(k, b)
            if abs(after - before) > 1e-6:
                pct = 100.0 * (after - before) / before if before else 0.0
                print(f"  {k}: {before:.4f} -> {after:.4f}  ({pct:+.2f}%)")
        print()
        return new_state
    finally:
        try:
            if _HAVE_FCNTL:
                fcntl.flock(_lock_fp.fileno(), fcntl.LOCK_UN)
            _lock_fp.close()
        except Exception:
            pass



def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="Slate date to process (default: yesterday).")
    p.add_argument("--picks-dir", default=".",
                   help="Directory holding picks_<date>.csv files.")
    p.add_argument("--force", action="store_true",
                   help="Process even if already in the audit log.")
    args = p.parse_args()

    if args.date:
        td = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        td = date.today()

    run(td, picks_dir=Path(args.picks_dir), force=args.force)


if __name__ == "__main__":
    main()
