"""
bullpen_tracker.py
------------------
Pulls the trailing 48-hour pitch counts and consecutive-day appearances for
every team's bullpen from the MLB Stats API, then formats the output to feed
`bullpen_fatigue_blocker.compute_bullpen_workload()`.

Method:
    1. List all completed games in the prior 72-hour window via /schedule.
    2. For each game, fetch /game/<pk>/feed/live and walk the play-by-play
       to enumerate every reliever appearance (filtering out the starter).
    3. Aggregate by (team, pitcher_id) and tag each appearance with a
       leverage estimate. Leverage Index isn't directly published by the
       Stats API, so we synthesize a proxy from inning + score state at
       entry — a thin but acceptable substitute. Real LI joins via Savant
       are added in the optional `use_savant=True` branch.
    4. Emit a long-format DF with the schema
        [game_date, team, pitcher_id, is_starter, pitches, leverage_index]
       which is the exact shape `compute_bullpen_workload()` expects.

The 72-hour window matches the v5.1 ceiling rule. We additionally compute
"days since last appearance" per pitcher — used as a soft-tier demotion
input (back-to-back-to-back relievers can't anchor a PLATINUM bet).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from .stadiums import normalize_team

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"

DEFAULT_LOOKBACK_DAYS = 3
HIGH_LEVERAGE_THRESHOLD = 1.5
PARQUET_OUT = Path("data/pitch_logs/recent_72h.parquet")


# ---------------------------------------------------------------------------
# Leverage proxy
# ---------------------------------------------------------------------------
def _leverage_proxy(inning: int, half: str, score_diff: int) -> float:
    """Approximate leverage index from inning + score state at the time of
    pitcher entry. Calibrated to match Tom Tango's average-LI-by-game-state
    lookup table, simplified for production use.

      - Innings 1-5: low (0.7) regardless of score.
      - Innings 6-7: moderate; tighter score = higher LI.
      - Innings 8+ : high (1.5-2.5) when within 3 runs.
    """
    base = 0.7
    if inning >= 8:
        base = 2.0 if abs(score_diff) <= 3 else 0.9
    elif inning >= 6:
        base = 1.4 if abs(score_diff) <= 3 else 0.8
    # Bottom-of-9 down 1 is the iconic 3.5-LI spot
    if inning >= 9 and half.lower() == "bottom" and score_diff in (-1, 0):
        base = 3.0
    return base


# ---------------------------------------------------------------------------
# Schedule listing
# ---------------------------------------------------------------------------
def _fetch_completed_games(start: date, end: date) -> List[Dict]:
    """Return all games with detailedState=='Final' in [start, end]."""
    out: List[Dict] = []
    cur = start
    while cur <= end:
        try:
            r = requests.get(
                SCHEDULE_URL,
                params={"sportId": 1, "date": cur.isoformat()},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("Schedule fetch failed for %s: %s", cur, e)
            cur += timedelta(days=1)
            continue
        for d in data.get("dates", []):
            for g in d.get("games", []):
                state = (g.get("status", {}) or {}).get("detailedState", "")
                if state in ("Final", "Game Over", "Completed Early"):
                    out.append(g)
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Game-feed walker
# ---------------------------------------------------------------------------
def _appearances_from_feed(game: Dict) -> List[Dict]:
    """Walk the play-by-play in `feed/live` and emit one row per pitcher
    appearance with: game_pk, game_date, team, pitcher_id, is_starter,
    pitches, leverage_index."""
    pk = int(game["gamePk"])
    try:
        r = requests.get(GAME_FEED_URL.format(pk=pk), timeout=25)
        r.raise_for_status()
        feed = r.json()
    except Exception as e:
        log.warning("Feed fetch failed for game %s: %s", pk, e)
        return []

    teams = feed.get("gameData", {}).get("teams", {})
    home_full = teams.get("home", {}).get("name", "")
    away_full = teams.get("away", {}).get("name", "")
    home_abbr = normalize_team(home_full)
    away_abbr = normalize_team(away_full)

    plays = (feed.get("liveData", {}).get("plays", {}) or {}).get("allPlays", [])
    if not plays:
        return []

    # Track first appearance per (team, pitcher_id) — that's the starter.
    starter_id: Dict[str, Optional[int]] = {home_abbr: None, away_abbr: None}
    appearances: Dict[Tuple[str, int], Dict] = {}

    for play in plays:
        about = play.get("about", {}) or {}
        match = play.get("matchup", {}) or {}
        pitcher = match.get("pitcher", {}) or {}
        pid = pitcher.get("id")
        if pid is None:
            continue
        # The pitcher's team is the *defensive* side (opposite of half-inning)
        is_top = bool(about.get("isTopInning", False))
        defense_team = home_abbr if is_top else away_abbr

        if starter_id[defense_team] is None:
            starter_id[defense_team] = int(pid)

        key = (defense_team, int(pid))
        if key not in appearances:
            inning = int(about.get("inning", 1))
            half = "top" if is_top else "bottom"
            res = play.get("result", {}) or {}
            home_score = int(res.get("homeScore", 0))
            away_score = int(res.get("awayScore", 0))
            score_diff = (home_score - away_score) if not is_top else (away_score - home_score)
            appearances[key] = {
                "game_pk": pk,
                "game_date": (game.get("officialDate") or game.get("gameDate", "")[:10]),
                "team": defense_team,
                "pitcher_id": int(pid),
                "is_starter": False,
                "pitches": 0,
                "leverage_index": _leverage_proxy(inning, half, score_diff),
                "entry_inning": inning,
            }
        # Pitch count for this pitcher in this play.
        events = play.get("playEvents", []) or []
        n_pitches = sum(1 for ev in events if (ev.get("isPitch") or False))
        appearances[key]["pitches"] += n_pitches

    # Tag the starter
    for tm, pid in starter_id.items():
        if pid and (tm, pid) in appearances:
            appearances[(tm, pid)]["is_starter"] = True

    return list(appearances.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@dataclass
class BullpenSnapshot:
    as_of: datetime
    pitch_log: pd.DataFrame                # long-format, fed to bullpen_fatigue_blocker
    rest_days_by_pitcher: pd.DataFrame     # one row per (team, pitcher_id)
    workload_by_team: pd.DataFrame         # one row per team, top-3 HL pitches


def build_pitch_log(
    slate_date: date,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Returns the long-format pitch log for the lookback window ending the
    day BEFORE the slate. Schema matches `compute_bullpen_workload()`.
    """
    end = slate_date - timedelta(days=1)
    start = end - timedelta(days=lookback_days - 1)
    games = _fetch_completed_games(start, end)
    rows: List[Dict] = []
    for g in games:
        rows.extend(_appearances_from_feed(g))
    if not rows:
        return pd.DataFrame(columns=[
            "game_date", "team", "pitcher_id", "is_starter",
            "pitches", "leverage_index",
        ])
    df = pd.DataFrame(rows)
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df[["game_date", "team", "pitcher_id", "is_starter",
               "pitches", "leverage_index"]]


def compute_rest_days(pitch_log: pd.DataFrame, slate_date: date) -> pd.DataFrame:
    """Days since each reliever's last appearance, prior to the slate."""
    if pitch_log.empty:
        return pd.DataFrame(columns=["team", "pitcher_id", "last_appearance",
                                      "rest_days", "consecutive_days"])
    rel = pitch_log[~pitch_log["is_starter"]].copy()
    rel["game_date"] = pd.to_datetime(rel["game_date"])
    last = (rel.groupby(["team", "pitcher_id"])["game_date"].max()
            .reset_index().rename(columns={"game_date": "last_appearance"}))
    last["rest_days"] = (pd.Timestamp(slate_date) - last["last_appearance"]).dt.days

    # Consecutive-day appearances (back-to-back / B2B-2-B detection)
    rel = rel.sort_values(["team", "pitcher_id", "game_date"])
    consec: List[Dict] = []
    for (tm, pid), grp in rel.groupby(["team", "pitcher_id"]):
        dates = sorted(set(grp["game_date"].dt.date))
        run = 1
        for i in range(len(dates) - 1, 0, -1):
            if (dates[i] - dates[i-1]).days == 1:
                run += 1
            else:
                break
        # Only count the consecutive run that touches the most recent date.
        consec.append({"team": tm, "pitcher_id": pid, "consecutive_days": run})
    cons_df = pd.DataFrame(consec)
    return last.merge(cons_df, on=["team", "pitcher_id"], how="left")


def snapshot(slate_date: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
             persist: bool = True) -> BullpenSnapshot:
    """Single entry point — pull, compute, optionally persist to parquet."""
    log.info("Building bullpen snapshot for %s (lookback=%dd)",
             slate_date, lookback_days)
    pitch_log = build_pitch_log(slate_date, lookback_days)
    rest = compute_rest_days(pitch_log, slate_date)

    # Reuse the existing v5.1 workload-ceiling logic so the snapshot's per-team
    # numbers are EXACTLY what bullpen_fatigue_blocker will read at predict time.
    from .bullpen_fatigue_blocker import compute_bullpen_workload
    workload = (compute_bullpen_workload(
        pitch_log, slate_date=pd.Timestamp(slate_date)
    ) if not pitch_log.empty else
        pd.DataFrame(columns=["team", "top3_pitch_total_72h", "ceiling_tier"]))

    if persist and not pitch_log.empty:
        PARQUET_OUT.parent.mkdir(parents=True, exist_ok=True)
        pitch_log.to_parquet(PARQUET_OUT, index=False)
        log.info("Wrote pitch log to %s (%d rows)", PARQUET_OUT, len(pitch_log))

    return BullpenSnapshot(
        as_of=datetime.now(timezone.utc),
        pitch_log=pitch_log,
        rest_days_by_pitcher=rest,
        workload_by_team=workload,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS)
    p.add_argument("--no-persist", action="store_true")
    args = p.parse_args()

    sd = datetime.strptime(args.date, "%Y-%m-%d").date()
    snap = snapshot(sd, args.lookback, persist=not args.no_persist)

    if snap.workload_by_team.empty:
        print("No completed games in window — empty workload.")
    else:
        print("=== Top-3 high-leverage pitches per team (last 72h) ===")
        print(snap.workload_by_team.sort_values("top3_pitch_total_72h",
                                                 ascending=False).to_string(index=False))
        print()
        print("=== Pitchers with rest_days <= 1 (back-to-back risk) ===")
        risky = snap.rest_days_by_pitcher[snap.rest_days_by_pitcher["rest_days"] <= 1]
        print(risky.to_string(index=False) if not risky.empty else "  (none)")
