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
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Set

import pandas as pd

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # bumped from 1: added `name` field on each reliever
DEFAULT_TOP_N_RELIEVERS = 12   # full active pen (was 8); cap only trims call-up churn
RECENT_APPEARANCES_LOOKBACK_DAYS = 7
# Wider lookback used ONLY for the display sidecar's reliever LIST so the whole
# bullpen shows (a full pen turns over within ~2 weeks). The frozen model's own
# bullpen snapshot is built separately and is NOT affected by this.
META_LIST_LOOKBACK_DAYS = 14
HIGH_LEVERAGE_THRESHOLD = 1.5  # mirrors bullpen_fatigue_blocker default
STATSAPI_PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people"


# --------------------------------------------------------------------------
# Pitcher name resolution — single batch fetch per slate.
# --------------------------------------------------------------------------
def _resolve_pitcher_names(pitcher_ids: List[int],
                           timeout_sec: int = 15) -> Dict[int, str]:
    """Resolve a list of pitcher IDs to full names via the MLB Stats API
    `/people?personIds=...` batch endpoint.  One HTTP call per slate.

    Best-effort: returns whatever the API returned; missing IDs stay
    absent from the returned dict (caller falls back to `#<id>` rendering).
    Failure (network error, timeout, HTTP error) returns an empty dict
    rather than raising — the writer continues with name=None for every
    reliever and the dashboard's fallback rendering kicks in.
    """
    out: Dict[int, str] = {}
    if not pitcher_ids:
        return out
    # De-dup + sort for stable URLs
    unique_ids = sorted(set(int(pid) for pid in pitcher_ids if pid))
    if not unique_ids:
        return out
    # MLB Stats API accepts comma-separated personIds.  Their docs don't
    # publish a hard limit but ~100 IDs per call has worked historically
    # in this codebase (see data_sources/savant_bat_tracking.py).
    url = f"{STATSAPI_PEOPLE_URL}?personIds=" + ",".join(str(i) for i in unique_ids)
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as r:
            payload = json.loads(r.read())
        for p in payload.get("people", []) or []:
            pid = p.get("id")
            name = p.get("fullName") or p.get("lastFirstName") or None
            if pid is not None and name:
                out[int(pid)] = str(name)
        log.info("[bullpen_meta] resolved %d/%d pitcher names",
                 len(out), len(unique_ids))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, ValueError, TimeoutError) as e:
        log.warning("[bullpen_meta] name resolution failed (%d ids): %s",
                    len(unique_ids), e)
    except Exception as e:
        log.warning("[bullpen_meta] name resolution unexpected error: %s", e)
    return out


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


def _normalize_ceiling_tier(top3_pitch_total_72h: int) -> str:
    """Re-bucket the upstream betting-tier vocabulary (DIAMOND/GOLD/SKIP,
    inherited from mlb_edge.bullpen_fatigue_blocker.compute_bullpen_workload)
    into a fatigue-vocabulary that reads naturally in the dashboard's
    narrative ("X's bullpen is currently STRAINED" vs "X's bullpen is
    currently SKIP").

    Boundaries chosen to mirror the upstream's WORKLOAD_PITCH_LIMIT=40
    cliff while giving slightly finer granularity (4 buckets vs 3):

      FRESH       : top3 <= 25  (clearly under the upstream DIAMOND ceiling)
      NORMAL      : 25 < top3 <= 50  (around the upstream WORKLOAD_PITCH_LIMIT)
      STRAINED    : 50 < top3 <= 75  (upstream GOLD-equivalent)
      OVERWORKED  : top3 > 75  (well past upstream SKIP threshold of 60)

    The field name `ceiling_tier` is preserved so the schema version
    stays at 2 (only the value vocabulary changes; consumers tolerate
    unknown values via fallback colors)."""
    try:
        p = int(top3_pitch_total_72h or 0)
    except (TypeError, ValueError):
        return "NORMAL"
    if p > 75:
        return "OVERWORKED"
    if p > 50:
        return "STRAINED"
    if p > 25:
        return "NORMAL"
    return "FRESH"


# --------------------------------------------------------------------------
# Per-team aggregation
# --------------------------------------------------------------------------
def _per_team_block(team: str,
                    pitch_log: pd.DataFrame,
                    rest_df: pd.DataFrame,
                    workload_df: pd.DataFrame,
                    slate_ts: pd.Timestamp,
                    top_n: int = DEFAULT_TOP_N_RELIEVERS,
                    name_map: Optional[Dict[int, str]] = None) -> Dict:
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

        # Base population = EVERY reliever with an appearance in the FULL
        # lookback, not just the 72h window. per_pitcher (72h counts) was the
        # old merge base, which silently dropped any arm without a 72h outing
        # -- invisible in normal weeks, but after the 2026 All-Star break it
        # emptied every team (0 tracked, "bullpen list empty" on the board)
        # even though the 7/10-7/12 appearances sat in the lookback log.
        # Fresh arms now survive with pitches_72h filled as 0 below.
        base = team_rel[["pitcher_id"]].drop_duplicates()
        merged = (base
                  .merge(per_pitcher, on="pitcher_id", how="left")
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

        # Show the full pen: sort most-recently-active first (rest_days asc),
        # 72h workload as the tiebreak. head(top_n) only caps very large lists.
        # The old "pitches_72h desc" sort dropped FRESH (0-pitch, fully rested)
        # arms first — exactly the available relievers a reader cares about — so
        # with the wider meta lookback we sort by rest instead and keep them.
        merged = (merged.sort_values(["rest_days", "pitches_72h"],
                                     ascending=[True, False])
                  .head(top_n).reset_index(drop=True))

        # Per-reliever dict (name resolved from name_map if available;
        # falls back to None so the dashboard can render `#<id>` instead)
        relievers: List[Dict] = []
        for _, r in merged.iterrows():
            rest = int(r["rest_days"]) if pd.notna(r["rest_days"]) else 99
            consec = int(r["consecutive_days"]) if pd.notna(r["consecutive_days"]) else 1
            p72 = int(r["pitches_72h"]) if pd.notna(r["pitches_72h"]) else 0
            pid = int(r["pitcher_id"])
            relievers.append({
                "pitcher_id":              pid,
                "name":                    (name_map or {}).get(pid),
                "rest_days":               rest,
                "consecutive_days":        consec,
                "pitches_72h":             p72,
                "pitches_last_appearance": int(r["pitches_last_appearance"]),
                "avg_leverage_last_3":     round(float(r["avg_leverage_last_3"]), 2),
                "fatigue_flag":            _fatigue_flag(rest, consec, p72),
                "available_today":         _available_today(rest, consec),
            })

        # Team summary. Consecutive-day streaks only count while CURRENT
        # (rest_days <= 1): a back-to-back from before an off-day/break is
        # history, not tonight's availability constraint (post-ASG fix).
        n_b2b = sum(1 for r in relievers
                    if r["consecutive_days"] == 2 and r["rest_days"] <= 1)
        n_b2b2b = sum(1 for r in relievers
                      if r["consecutive_days"] >= 3 and r["rest_days"] <= 1)
        avg_rest = (sum(r["rest_days"] for r in relievers) / len(relievers)
                    if relievers else 0.0)

        # Pull team's top-3 high-leverage workload from workload_df.
        # NOTE: the upstream ceiling_tier field uses betting-tier vocabulary
        # (DIAMOND/GOLD/SKIP, inherited from bullpen_fatigue_blocker), which
        # reads awkwardly in a fatigue-narrative context ("X's bullpen is
        # currently SKIP").  We discard the upstream value and recompute via
        # _normalize_ceiling_tier() to get FRESH/NORMAL/STRAINED/OVERWORKED.
        wl_row = workload_df[workload_df["team"] == team]
        if not wl_row.empty:
            top3_72h = int(wl_row.iloc[0].get("top3_pitch_total_72h", 0) or 0)
        else:
            top3_72h = 0
        tier = _normalize_ceiling_tier(top3_72h)

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

        # Batch-resolve pitcher names for every reliever on the relevant
        # teams in a single MLB Stats API call (best-effort).  Done BEFORE
        # per-team blocks are built so names land in the JSON directly.
        relevant_pl = pitch_log[
            (~pitch_log["is_starter"])
            & (pitch_log["team"].isin(teams_on_slate))
        ]
        unique_pids = sorted(set(
            int(p) for p in relevant_pl["pitcher_id"].dropna().tolist() if p
        ))
        name_map = _resolve_pitcher_names(unique_pids)

        teams_block: Dict[str, Dict] = {}
        for team in teams_on_slate:
            teams_block[team] = _per_team_block(
                team=team,
                pitch_log=pitch_log,
                rest_df=rest_df if rest_df is not None else pd.DataFrame(),
                workload_df=workload_df if workload_df is not None else pd.DataFrame(),
                slate_ts=slate_ts,
                top_n=top_n_relievers,
                name_map=name_map,
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
    p.add_argument("--lookback", type=int, default=META_LIST_LOOKBACK_DAYS,
                   help="reliever-list lookback window in days (display only)")
    p.add_argument("--out-dir", default="docs/data")
    args = p.parse_args()
    sd = datetime.strptime(args.date, "%Y-%m-%d").date()

    from .bullpen_tracker import snapshot as bullpen_snapshot
    snap = bullpen_snapshot(sd, lookback_days=args.lookback, persist=False)
    path = write_bullpen_meta(sd, snap, top_n_relievers=args.top_n,
                              out_dir=args.out_dir)
    print(f"wrote: {path}")
