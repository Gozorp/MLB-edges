"""
savant_bat_tracking.py
----------------------
Load Baseball Savant bat-tracking leaderboard CSVs and convert them into
team-level gap features that the pipeline can consume.

The CSV is the exact payload from:
    https://baseballsavant.mlb.com/leaderboard/bat-tracking?...&csv=true

It's player-level. Columns (see `BAT_TRACKING_COLS`) include:
    id, name, swings_competitive, percent_swings_competitive, contact,
    avg_bat_speed, hard_swing_rate, squared_up_per_bat_contact,
    squared_up_per_swing, blast_per_bat_contact, blast_per_swing,
    swing_length, swords, batter_run_value, whiffs, whiff_per_swing,
    batted_ball_events, batted_ball_event_per_swing

The leaderboard URL filters to `minSwings=q` (qualified) so rows are
roster regulars — a reasonable proxy for each club's top-of-lineup quality.

We resolve `id` → team via MLB Stats API `/people/{id}?hydrate=currentTeam`,
cache the mapping in parquet so we don't re-hit the API, then aggregate
competitive-swing-weighted averages to the team level.

Gap features exposed to the pipeline (home - away, positive = home edge):
    team_bat_speed_gap          (mph)
    team_squared_up_swing_gap   (rate)
    team_blast_swing_gap        (rate)
    team_batter_run_value_gap   (per-PA-ish RV sum)
    team_whiff_rate_gap         (rate, SIGN-FLIPPED so + = home edge)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests

from ..config import DATA
from ..stadiums import normalize_team

log = logging.getLogger(__name__)


BAT_TRACKING_DIR = Path("./data/savant_bat_tracking")
PLAYER_TEAM_CACHE = Path(DATA.statcast_cache_dir) / "player_team_map.parquet"

# Columns we care about downstream. Everything else is passed through but
# not used — the CSV schema occasionally shifts, so be lenient on extras.
NUMERIC_COLS = [
    "swings_competitive",
    "avg_bat_speed",
    "hard_swing_rate",
    "squared_up_per_swing",
    "blast_per_swing",
    "swing_length",
    "batter_run_value",
    "whiff_per_swing",
]


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def _parse_date_from_name(name: str) -> Optional[date]:
    """Extract YYYYMMDD from 'bat_tracking_2026_20260423.csv' → date(2026,4,23)."""
    stem = Path(name).stem
    for tok in stem.split("_"):
        if len(tok) == 8 and tok.isdigit():
            try:
                return datetime.strptime(tok, "%Y%m%d").date()
            except ValueError:
                continue
    return None


def list_available_snapshots(root: Path = BAT_TRACKING_DIR) -> List[Path]:
    """All CSVs in the directory, sorted oldest → newest by filename date."""
    root = Path(root)
    if not root.exists():
        return []
    paths = sorted(root.glob("*.csv"))
    # Filter to ones we can date-stamp; unparseable names sort last.
    def _key(p: Path):
        d = _parse_date_from_name(p.name)
        return (d is None, d or date.min, p.name)
    return sorted(paths, key=_key)


def load_csv(path: Path) -> pd.DataFrame:
    """Read one Savant CSV, coerce numeric columns, drop rows without id."""
    df = pd.read_csv(path)
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def latest_snapshot_on_or_before(as_of: date,
                                  root: Path = BAT_TRACKING_DIR) -> Optional[Path]:
    """
    The most recent CSV whose filename-date is <= `as_of`.

    We key snapshots to the calendar date the pull was taken, so this preserves
    the "no future data" invariant used everywhere else in the pipeline.
    """
    snaps = list_available_snapshots(root)
    best: Optional[Path] = None
    for p in snaps:
        d = _parse_date_from_name(p.name)
        if d is None:
            continue
        if d <= as_of:
            best = p
        else:
            break
    return best


# ---------------------------------------------------------------------------
# Player → team resolution (MLB Stats API)
# ---------------------------------------------------------------------------
def _read_team_cache() -> pd.DataFrame:
    if PLAYER_TEAM_CACHE.exists():
        try:
            return pd.read_parquet(PLAYER_TEAM_CACHE)
        except Exception as e:
            log.warning("player_team cache unreadable (%s); rebuilding", e)
    return pd.DataFrame(columns=["player_id", "team_abbr", "updated_at"])


def _write_team_cache(df: pd.DataFrame) -> None:
    PLAYER_TEAM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(PLAYER_TEAM_CACHE, index=False)
    except Exception as e:
        log.warning("player_team cache write failed: %s", e)


def _lookup_team(player_id: int) -> Optional[str]:
    """
    One-off MLB Stats API call. Returns canonical 3-letter abbr or None.

    The `/people/{id}?hydrate=currentTeam` endpoint ONLY returns
    {id, name, link} for the team — no abbreviation. We feed the full name
    through `normalize_team` (which knows every full name -> abbr via
    TEAM_ALIASES), avoiding a second round-trip to /teams/{id}.
    """
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}"
    try:
        r = requests.get(url, params={"hydrate": "currentTeam"}, timeout=10)
        r.raise_for_status()
        people = r.json().get("people", [])
        if not people:
            return None
        team = people[0].get("currentTeam") or {}
        # Prefer explicit abbreviation when present (future-proof), fall
        # back to full-name lookup (current API shape).
        abbr = team.get("abbreviation") or team.get("name")
        if not abbr:
            return None
        norm = normalize_team(abbr)
        # normalize_team returns its input unchanged when unknown; guard
        # against accepting a full team name as the abbreviation.
        return norm if len(norm) <= 3 else None
    except Exception as e:
        log.warning("team lookup failed for %s: %s", player_id, e)
        return None


def resolve_player_teams(player_ids: List[int],
                         refresh: bool = False) -> Dict[int, str]:
    """
    Return {player_id: team_abbr}. Caches on disk to avoid re-hitting the API.

    `refresh=True` forces a re-lookup for every id — use sparingly (deadline
    day roster churn, trade deadline, etc.).
    """
    cache = _read_team_cache()
    known = {} if refresh else dict(zip(cache["player_id"], cache["team_abbr"]))

    missing = [pid for pid in player_ids if pid not in known]
    if not missing:
        return {pid: known[pid] for pid in player_ids if pid in known}

    log.info("Resolving %d new player→team mappings", len(missing))
    new_rows = []
    now = pd.Timestamp.utcnow()
    for pid in missing:
        abbr = _lookup_team(pid)
        if abbr:
            known[pid] = abbr
            new_rows.append({"player_id": pid, "team_abbr": abbr,
                             "updated_at": now})

    if new_rows:
        updated = pd.concat([cache, pd.DataFrame(new_rows)],
                            ignore_index=True).drop_duplicates(
            subset=["player_id"], keep="last")
        _write_team_cache(updated)

    return {pid: known[pid] for pid in player_ids if pid in known}


# ---------------------------------------------------------------------------
# Team-level aggregation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TeamBatTracking:
    team_abbr: str
    n_hitters: int
    avg_bat_speed: float
    hard_swing_rate: float
    squared_up_per_swing: float
    blast_per_swing: float
    swing_length: float
    batter_run_value: float
    whiff_per_swing: float


def aggregate_to_teams(df: pd.DataFrame,
                       player_team: Dict[int, str]) -> pd.DataFrame:
    """
    Competitive-swing-weighted averages per team.

    Weighting by `swings_competitive` matches how Savant presents leaderboard
    values (per-swing rates) — a hitter with 200 swings gets ~4× the weight of
    a rookie with 50. Sum metrics (`batter_run_value`) are summed instead.
    """
    if df.empty:
        return pd.DataFrame(columns=["team_abbr"])

    df = df.copy()
    df["team_abbr"] = df["id"].map(player_team)
    df = df.dropna(subset=["team_abbr"])
    df["swings_competitive"] = df["swings_competitive"].fillna(0.0)

    rows = []
    for team, grp in df.groupby("team_abbr"):
        w = grp["swings_competitive"].to_numpy(dtype=float)
        w_sum = w.sum()
        if w_sum <= 0:
            continue

        def wmean(col: str) -> float:
            vals = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
            mask = np.isfinite(vals) & (w > 0)
            if not mask.any():
                return float("nan")
            return float(np.average(vals[mask], weights=w[mask]))

        rows.append({
            "team_abbr":            team,
            "n_hitters":            int(len(grp)),
            "avg_bat_speed":        wmean("avg_bat_speed"),
            "hard_swing_rate":      wmean("hard_swing_rate"),
            "squared_up_per_swing": wmean("squared_up_per_swing"),
            "blast_per_swing":      wmean("blast_per_swing"),
            "swing_length":         wmean("swing_length"),
            "batter_run_value":     float(pd.to_numeric(
                grp["batter_run_value"], errors="coerce").sum(skipna=True)),
            "whiff_per_swing":      wmean("whiff_per_swing"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def team_table_as_of(as_of: date) -> pd.DataFrame:
    """
    Team-aggregate bat-tracking table for the snapshot on or before `as_of`.
    Returns an empty DataFrame if no snapshot exists — callers fall back to
    league mean (handled in `bat_tracking_gap_features`).
    """
    snap = latest_snapshot_on_or_before(as_of)
    if snap is None:
        log.info("No bat-tracking snapshot on or before %s", as_of)
        return pd.DataFrame()

    log.info("Using bat-tracking snapshot: %s", snap.name)
    df = load_csv(snap)
    pid_map = resolve_player_teams(df["id"].tolist())
    return aggregate_to_teams(df, pid_map)


def bat_tracking_gap_features(home_team: str,
                              away_team: str,
                              as_of: date) -> Dict[str, float]:
    """
    Return {feature_name: home - away} gaps for the current bat-tracking
    snapshot. All gaps are signed so that POSITIVE = home advantage —
    important because the model's monotone constraints assume this sign.

    Note `whiff_per_swing` is inverted: lower whiff rate is better, so the
    gap is (away - home) to keep positive = home edge.
    """
    home = normalize_team(home_team)
    away = normalize_team(away_team)

    tbl = team_table_as_of(as_of)
    if tbl.empty:
        return _zero_gaps()

    idx = tbl.set_index("team_abbr")
    if home not in idx.index or away not in idx.index:
        log.debug("Missing bat-tracking rows: home=%s away=%s (have %d teams)",
                  home, away, len(idx))
        return _zero_gaps()

    h, a = idx.loc[home], idx.loc[away]

    def g(col: str, invert: bool = False) -> float:
        hv, av = float(h.get(col, np.nan)), float(a.get(col, np.nan))
        if not (np.isfinite(hv) and np.isfinite(av)):
            return 0.0
        return av - hv if invert else hv - av

    return {
        "team_bat_speed_gap":          g("avg_bat_speed"),
        "team_squared_up_swing_gap":   g("squared_up_per_swing"),
        "team_blast_swing_gap":        g("blast_per_swing"),
        "team_batter_run_value_gap":   g("batter_run_value"),
        "team_whiff_rate_gap":         g("whiff_per_swing", invert=True),
    }


def _zero_gaps() -> Dict[str, float]:
    """Neutral (no-signal) gaps when data is missing."""
    return {
        "team_bat_speed_gap":        0.0,
        "team_squared_up_swing_gap": 0.0,
        "team_blast_swing_gap":      0.0,
        "team_batter_run_value_gap": 0.0,
        "team_whiff_rate_gap":       0.0,
    }


BAT_TRACKING_FEATURES: List[str] = list(_zero_gaps().keys())
