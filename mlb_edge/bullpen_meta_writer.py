"""
mlb_edge/bullpen_meta_writer.py
================================
Writes per-slate `docs/data/bullpen_meta_<date>.json` from the existing
`BullpenSnapshot` produced by `mlb_edge.bullpen_tracker.snapshot()`.

This is Phase 1 of the per-reliever projection-model sprint (see
memory/project_bullpen_model_sprint_plan.md).  The JSON sidecar gives
the dashboard rich per-reliever workload + rest data to render in
three places: Deep Analysis dropdowns, the Bullpen Outlook card, and
the slate-row detail panel.

Schema (versioned for downstream consumers):
{
  "schema_version": 1,
  "generated_at": "2026-05-22T01:23:45Z",
  "slate_date": "2026-05-22",
  "lookback_days": 7,
  "teams": {
    "NYY": {
      "team_summary": {
        "top3_pitch_total_72h": 287,
        "ceiling_tier": "STRAINED",
        "n_relievers_back_to_back": 2,
        "n_relievers_three_consecutive": 0,
        "avg_rest_days": 1.8,
        "n_relievers_tracked": 8
      },
      "top_relievers": [
        {
          "pitcher_id": 543037,
          "rest_days": 0,
          "consecutive_days": 2,
          "pitches_72h": 38,
          "pitches_last_appearance": 22,
          "avg_leverage_last_3": 1.9,
          "fatigue_flag": "B2B",
          "available_today": false
        }, ...
      ]
    },
    "BOS": {...}
  }
}

fatigue_flag values:
  FRESH        — rest_days >= 3 AND pitches_72h <= 20
  NORMAL       — everything else
  B2B          — consecutive_days == 2
  B2B2B        — consecutive_days >= 3 (back-to-back-to-back, unavailable today)
  OVERWORKED   — pitches_72h >= 50

ceiling_tier values (mirrored from bullpen_fatigue_blocker.compute_bullpen_workload):
  FRESH / NORMAL / STRAINED / OVERWORKED

Per Architecture-Session Pre-Flight Prompt v1.0:
  Rule 1   — probed: BullpenSnapshot already exposes pitch_log,
             rest_days_by_pitcher, workload_by_team
  Rule 5   — single-purpose writer; does NOT add new pipeline stages,
             does NOT touch parlay_builder.py, does NOT change schema
             of diag CSV
  Rule 6   — best-effort throughout; missing snapshot, empty pitch_log,
             missing team — all degrade to an empty teams[team] entry
             with `unavailable: true` rather than crashing the cron
  Rule 11  — schema_version field protects downstream consumers from
             silent schema drift; future changes bump the version
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Set

import pandas as pd

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_TOP_N_RELIEVERS = 8
RECENT_APPEARANCES_LOOKBACK_DAYS = 7
HIGH_LEVERAGE_THRESHOLD = 1.5  # mirrors bullpen_fatigue_blocker default


# --------------------------------------------------------------------------
# Per-reliever fatigue flag
# --------------------------------------------------------------------------
def _fatigue_flag(rest_days: int, consecutive_days: int,
                  pitches_72h: int) -> str:
    if consecutive_days >= 3:
        return "B2B2B"
    if consecutive_days == 2:
        return "B2B"
    if pitches_72h >= 50:
        return "OVERWORKED"
    if rest_days >= 3 and pitches_72h <= 20:
        return "FRESH"
    return "NORMAL"


def _available_today(rest_days: int, consecutive_days: int) -> bool:
    """Heuristic: a reliever pitching three consecutive days is effectively
    unavailable.  Anything else is technically available even if fatigued.
    The model's projection layer (Phases 4-6) will refine this later."""
    if consecutive_days >= 3:
        return False
    return True


# --------------------------------------------------------------------------
# Per-team aggregation
# --------------------------------------------------------------------------
def _per_team_block(team: str,
                    pitch_log: pd.DataFrame,
                    rest_df: pd.DataFrame,
                    workload_df: pd.DataFrame,
                    slate_ts: pd.Timestamp,
                    top_n: int = DEFAULT_TOP_N_RELIEVERS) -> Dict:
    """Build the per-team JSON block.  Returns the schema described in
    the module docstring; degrades to {unavailable: true} on data gaps."""
    try:
        team_rel = pitch_log[(pitch_log["team"] == team)
                             & (~pitch_log["is_starter"])].copy()
        if team_rel.empty:
            return {"unavailable": True,
                    "reason": "no recent reliever appearances in lookback"}
        team_rel["game_date"] = pd.to_datetime(team_rel["game_date"])

        # 72-hour window for the pitches_72h field
        cutoff_72h = slate_ts - pd.Timedelta(hours=72)
        recent_72h = team_rel[team_rel["game_date"] >= cutoff_72h]

        # Per-pitcher aggregates
        per_pitcher = (recent_72h.groupby("pitcher_id")
                       .agg(pitches_72h=("pitches", "sum"))
                       .reset_index())

        # Last-appearance pitch count (any appearance in the lookback window)
        last_appearance_pitches = (team_rel.sort_values(["pitcher_id", "game_date"])
                                   .groupby("pitcher_id").tail(1)
                                   [["pitcher_id", "pitches"]]
                                   .rename(columns={"pitches":
                                                    "pitches_last_appearance"}))

        # Average leverage index over last 3 appearances
        per_pitcher_lev = []
        for pid, grp in team_rel.sort_values("game_date").groupby("pitcher_id"):
            last3 = grp.tail(3)
            avg_lev = float(last3["leverage_index"].mean()) if len(last3) else 0.0
            per_pitcher_lev.append({
                "pitcher_id": int(pid),
                "avg_leverage_last_3": round(avg_lev, 2),
            })
        lev_df = pd.DataFrame(per_pitcher_lev)

        # Combine with rest_df (per-pitcher rest_days + consecutive_days)
        team_rest = rest_df[rest_df["team"] == team][
            ["pitcher_id", "rest_days", "consecutive_days"]
        ].copy()

        merged = (per_pitcher
                  .merge(last_appearance_pitches, on="pitcher_id", how="left")
                  .merge(lev_df, on="pitcher_id", how="left")
                  .merge(team_rest, on="pitcher_id", how="left"))
        # Defensive defaults for any missing columns
        for col, default in [("rest_days", 99), ("consecutive_days", 1),
                             ("pitches_72h", 0), ("pitches_last_appearance", 0),
                             ("avg_leverage_last_3", 0.0)]:
            if col not in merged.columns:
                merged[col] = default
            else:
                merged[col] = merged[col].fillna(default)

        # Rank by 72h workload; take top N
        merged = (merged.sort_values("pitches_72h", ascending=False)
                  .head(top_n).reset_index(drop=True))

        # Per-reliever dict
        relievers: List[Dict] = []
        for _, r in merged.iterrows():
            rest = int(r["rest_days"]) if pd.notna(r["rest_days"]) else 99
            consec = int(r["consecutive_days"]) if pd.notna(r["consecutive_days"]) else 1
            p72 = int(r["pitches_72h"]) if pd.notna(r["pitches_72h"]) else 0
            relievers.append({
                "pitcher_id":              int(r["pitcher_id"]),
                "rest_days":               rest,
                "consecutive_days":        consec,
                "pitches_72h":             p72,
                "pitches_last_appearance": int(r["pitches_last_appearance"]),
                "avg_leverage_last_3":     round(float(r["avg_leverage_last_3"]), 2),
                "fatigue_flag":            _fatigue_flag(rest, consec, p72),
                "available_today":         _available_today(rest, consec),
            })

        # Team summary
        n_b2b = sum(1 for r in relievers if r["consecutive_days"] == 2)
        n_b2b2b = sum(1 for r in relievers if r["consecutive_days"] >= 3)
        avg_rest = (sum(r["rest_days"] for r in relievers) / len(relievers)
                    if relievers else 0.0)

        # Pull team's ceiling tier from workload_df
        wl_row = workload_df[workload_df["team"] == team]
        if not wl_row.empty:
            top3_72h = int(wl_row.iloc[0].get("top3_pitch_total_72h", 0) or 0)
            tier = str(wl_row.iloc[0].get("ceiling_tier", "NORMAL"))
        else:
            top3_72h = 0
            tier = "NORMAL"

        return {
            "team_summary": {
                "top3_pitch_total_72h":           top3_72h,
                "ceiling_tier":                   tier,
                "n_relievers_back_to_back":       n_b2b,
                "n_relievers_three_consecutive":  n_b2b2b,
                "avg_rest_days":                  round(avg_rest, 1),
                "n_relievers_tracked":            len(relievers),
            },
            "top_relievers": relievers,
        }
    except Exception as e:
        log.warning("[bullpen_meta] per-team block failed for %s: %s",
                    team, e)
        return {"unavailable": True, "reason": f"computation error: {e}"}


# --------------------------------------------------------------------------
# Top-level writer
# --------------------------------------------------------------------------
def write_bullpen_meta(slate_date: date,
                       snapshot,
                       teams_on_slate: Optional[List[str]] = None,
                       out_dir: str = "docs/data",
                       top_n_relievers: int = DEFAULT_TOP_N_RELIEVERS,
                       ) -> Optional[str]:
    """Write `docs/data/bullpen_meta_<slate_date>.json` from a BullpenSnapshot.

    Args:
        slate_date: the slate's date (used in filename + payload)
        snapshot:   a BullpenSnapshot from bullpen_tracker.snapshot()
        teams_on_slate: optional list of team abbreviations (e.g. ["NYY", "BOS"]).
                        When given, only these teams get a block; missing teams
                        get `{unavailable: true, reason: "not on slate"}`.
                        When None, all teams found in the pitch log are included.
        out_dir: directory to write the JSON file
        top_n_relievers: max relievers per team in `top_relievers` list

    Returns:
        Absolute path to the written JSON file, or None on failure.

    Per Rule 6: this function NEVER raises.  All failures are logged and
    the function returns None so the calling pipeline can continue.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir,
                                f"bullpen_meta_{slate_date.isoformat()}.json")

        # Empty-snapshot fallback: write a minimal payload so downstream
        # consumers always have a file to fetch.
        if snapshot is None:
            payload = _empty_payload(slate_date, "snapshot is None")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            log.warning("[bullpen_meta] wrote empty payload (no snapshot): %s",
                        out_path)
            return out_path

        pitch_log = getattr(snapshot, "pitch_log", None)
        rest_df = getattr(snapshot, "rest_days_by_pitcher", None)
        workload_df = getattr(snapshot, "workload_by_team", None)

        if pitch_log is None or pitch_log.empty:
            payload = _empty_payload(slate_date,
                                     "pitch_log empty (no recent games)")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            log.warning("[bullpen_meta] wrote empty payload (empty pitch_log): %s",
                        out_path)
            return out_path

        slate_ts = pd.Timestamp(slate_date)
        if teams_on_slate is None:
            teams_on_slate = sorted(set(pitch_log["team"].dropna().unique()))

        teams_block: Dict[str, Dict] = {}
        for team in teams_on_slate:
            teams_block[team] = _per_team_block(
                team=team,
                pitch_log=pitch_log,
                rest_df=rest_df if rest_df is not None else pd.DataFrame(),
                workload_df=workload_df if workload_df is not None else pd.DataFrame(),
                slate_ts=slate_ts,
                top_n=top_n_relievers,
            )

        payload = {
            "schema_version":  SCHEMA_VERSION,
            "generated_at":    datetime.now(timezone.utc).isoformat()
                               .replace("+00:00", "Z"),
            "slate_date":      slate_date.isoformat(),
            "lookback_days":   RECENT_APPEARANCES_LOOKBACK_DAYS,
            "teams":           teams_block,
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        n_teams = sum(1 for v in teams_block.values()
                      if not v.get("unavailable"))
        log.info("[bullpen_meta] wrote %s (%d teams populated of %d on slate)",
                 out_path, n_teams, len(teams_block))
        return out_path
    except Exception as e:
        log.warning("[bullpen_meta] top-level write failed: %s", e)
        return None


def _empty_payload(slate_date: date, reason: str) -> Dict:
    return {
        "schema_version":  SCHEMA_VERSION,
        "generated_at":    datetime.now(timezone.utc).isoformat()
                           .replace("+00:00", "Z"),
        "slate_date":      slate_date.isoformat(),
        "lookback_days":   RECENT_APPEARANCES_LOOKBACK_DAYS,
        "teams":           {},
        "_empty_reason":   reason,
    }


# --------------------------------------------------------------------------
# CLI for ad-hoc generation
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N_RELIEVERS)
    p.add_argument("--out-dir", default="docs/data")
    args = p.parse_args()
    sd = datetime.strptime(args.date, "%Y-%m-%d").date()

    from .bullpen_tracker import snapshot as bullpen_snapshot
    snap = bullpen_snapshot(sd, persist=False)
    path = write_bullpen_meta(sd, snap, top_n_relievers=args.top_n,
                              out_dir=args.out_dir)
    print(f"wrote: {path}")
