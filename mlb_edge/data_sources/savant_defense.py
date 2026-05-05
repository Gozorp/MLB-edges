"""
savant_defense.py
-----------------
Load Baseball Savant fielding leaderboard CSVs and convert them into
team-level defensive gap features.

Two distinct leaderboards are consumed:

1. Outs Above Average (OAA) — `outs-above-average_YYYYMMDD.csv`
   Columns include `display_team_name` (full club name like "Nationals"),
   `outs_above_average` (signed integer), `fielding_runs_prevented`.
   Player-level rows; group by team to get a club total.

2. Fielding Run Value (FRV) — `fielding-run-value_YYYYMMDD.csv`
   Columns include `id` (player id), `total_runs` (signed float), and
   position-specific run components (range, arm, framing, blocking, ...).
   No team field — must resolve player→team via the shared parquet cache
   that `savant_bat_tracking` maintains.

Snapshot directories (in priority order for `as_of` lookup):

  ./data/savant/outs-above-average/  (date-stamped current-season pulls)
  ./data/savant/fielding-run-value/

Historical fallback (when no date-stamped file exists for the calendar year):

  ./data/savant_2025/outs-above-average/outs-above-average_2025.csv
  ./data/savant_2025/fielding-run-value/fielding-run-value_2025.csv

Gap features (home - away, positive = home edge):
    team_oaa_gap   (sum of outs_above_average per club)
    team_frv_gap   (sum of total_runs per club, in runs prevented)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..stadiums import normalize_team
from .savant_bat_tracking import resolve_player_teams

log = logging.getLogger(__name__)


OAA_DIR = Path("./data/savant/outs-above-average")
FRV_DIR = Path("./data/savant/fielding-run-value")
OAA_2025_FALLBACK = Path("./data/savant_2025/outs-above-average/outs-above-average_2025.csv")
FRV_2025_FALLBACK = Path("./data/savant_2025/fielding-run-value/fielding-run-value_2025.csv")


# Savant's `display_team_name` uses club nicknames only ("Dodgers", "Red Sox").
# `normalize_team` is keyed on full names + abbrs and doesn't handle nicknames,
# so we keep an explicit map here.
NICKNAME_TO_ABBR: Dict[str, str] = {
    "Angels": "LAA", "Astros": "HOU", "Athletics": "OAK", "Blue Jays": "TOR",
    "Braves": "ATL", "Brewers": "MIL", "Cardinals": "STL", "Cubs": "CHC",
    "D-backs": "ARI", "Dodgers": "LAD", "Giants": "SF", "Guardians": "CLE",
    "Mariners": "SEA", "Marlins": "MIA", "Mets": "NYM", "Nationals": "WSH",
    "Orioles": "BAL", "Padres": "SD", "Phillies": "PHI", "Pirates": "PIT",
    "Rangers": "TEX", "Rays": "TB", "Red Sox": "BOS", "Reds": "CIN",
    "Rockies": "COL", "Royals": "KC", "Tigers": "DET", "Twins": "MIN",
    "White Sox": "CWS", "Yankees": "NYY",
}


def _nickname_to_abbr(name: str) -> Optional[str]:
    """Map a Savant club nickname to a 3-letter abbr; fall back to normalize_team."""
    if name in NICKNAME_TO_ABBR:
        return NICKNAME_TO_ABBR[name]
    norm = normalize_team(name)
    return norm if len(norm) <= 3 else None


# ---------------------------------------------------------------------------
# Snapshot discovery (same date-stamping pattern as savant_bat_tracking)
# ---------------------------------------------------------------------------
def _parse_date_from_name(name: str) -> Optional[date]:
    """Extract YYYYMMDD from any token in the filename."""
    stem = Path(name).stem
    for tok in stem.split("_"):
        if len(tok) == 8 and tok.isdigit():
            try:
                return datetime.strptime(tok, "%Y%m%d").date()
            except ValueError:
                continue
    return None


def _list_snapshots(root: Path) -> List[Path]:
    if not root.exists():
        return []
    paths = sorted(root.glob("*.csv"))
    def _key(p: Path):
        d = _parse_date_from_name(p.name)
        return (d is None, d or date.min, p.name)
    return sorted(paths, key=_key)


def _latest_on_or_before(as_of: date, root: Path) -> Optional[Path]:
    best: Optional[Path] = None
    for p in _list_snapshots(root):
        d = _parse_date_from_name(p.name)
        if d is None:
            continue
        if d <= as_of:
            best = p
        else:
            break
    return best


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------
def _load_oaa_csv(path: Path) -> pd.DataFrame:
    """OAA leaderboard. Columns we use: display_team_name, outs_above_average,
    fielding_runs_prevented."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "display_team_name" not in df.columns:
        log.warning("OAA CSV %s missing display_team_name", path.name)
        return pd.DataFrame()
    for c in ("outs_above_average", "fielding_runs_prevented"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _load_frv_csv(path: Path) -> pd.DataFrame:
    """FRV leaderboard. Columns we use: id, total_runs."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "id" not in df.columns or "total_runs" not in df.columns:
        log.warning("FRV CSV %s missing id/total_runs", path.name)
        return pd.DataFrame()
    df = df.dropna(subset=["id"]).copy()
    df["id"] = df["id"].astype(int)
    df["total_runs"] = pd.to_numeric(df["total_runs"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Path resolution: date-stamped snapshot OR season fallback
# ---------------------------------------------------------------------------
def _resolve_oaa_path(as_of: date) -> Optional[Path]:
    snap = _latest_on_or_before(as_of, OAA_DIR)
    if snap is not None:
        return snap
    if as_of.year >= 2025 and OAA_2025_FALLBACK.exists():
        return OAA_2025_FALLBACK
    return None


def _resolve_frv_path(as_of: date) -> Optional[Path]:
    snap = _latest_on_or_before(as_of, FRV_DIR)
    if snap is not None:
        return snap
    if as_of.year >= 2025 and FRV_2025_FALLBACK.exists():
        return FRV_2025_FALLBACK
    return None


# ---------------------------------------------------------------------------
# Team aggregation
# ---------------------------------------------------------------------------
def _aggregate_oaa_to_teams(df: pd.DataFrame) -> pd.DataFrame:
    """Sum OAA + FRP per team, normalising team names to 3-letter abbr."""
    if df.empty:
        return pd.DataFrame(columns=["team_abbr", "oaa_total", "frp_total"])
    df = df.copy()
    df["team_abbr"] = df["display_team_name"].astype(str).map(_nickname_to_abbr)
    df = df.dropna(subset=["team_abbr"])
    grp = df.groupby("team_abbr", as_index=False).agg(
        oaa_total=("outs_above_average", "sum"),
        frp_total=("fielding_runs_prevented", "sum"),
    )
    return grp


def _aggregate_frv_to_teams(df: pd.DataFrame,
                            player_team: Dict[int, str]) -> pd.DataFrame:
    """Sum total_runs per team after resolving player ids."""
    if df.empty:
        return pd.DataFrame(columns=["team_abbr", "frv_total"])
    df = df.copy()
    df["team_abbr"] = df["id"].map(player_team)
    df = df.dropna(subset=["team_abbr"])
    grp = df.groupby("team_abbr", as_index=False).agg(
        frv_total=("total_runs", "sum"),
    )
    return grp


# ---------------------------------------------------------------------------
# Cached team tables
# ---------------------------------------------------------------------------
@lru_cache(maxsize=64)
def team_oaa_table_as_of(as_of: date) -> pd.DataFrame:
    path = _resolve_oaa_path(as_of)
    if path is None:
        log.info("No OAA snapshot on or before %s", as_of)
        return pd.DataFrame()
    log.info("Using OAA snapshot: %s", path.name)
    df = _load_oaa_csv(path)
    return _aggregate_oaa_to_teams(df)


@lru_cache(maxsize=64)
def team_frv_table_as_of(as_of: date) -> pd.DataFrame:
    path = _resolve_frv_path(as_of)
    if path is None:
        log.info("No FRV snapshot on or before %s", as_of)
        return pd.DataFrame()
    log.info("Using FRV snapshot: %s", path.name)
    df = _load_frv_csv(path)
    if df.empty:
        return pd.DataFrame()
    pid_map = resolve_player_teams(df["id"].tolist())
    return _aggregate_frv_to_teams(df, pid_map)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def defense_gap_features(home_team: str,
                         away_team: str,
                         as_of: date) -> Dict[str, float]:
    """Return {feature: home - away} defensive gaps. Positive = home advantage."""
    home = normalize_team(home_team)
    away = normalize_team(away_team)

    feats = _zero_gaps()

    oaa_tbl = team_oaa_table_as_of(as_of)
    if not oaa_tbl.empty:
        idx = oaa_tbl.set_index("team_abbr")
        if home in idx.index and away in idx.index:
            h_oaa = float(idx.loc[home, "oaa_total"])
            a_oaa = float(idx.loc[away, "oaa_total"])
            if np.isfinite(h_oaa) and np.isfinite(a_oaa):
                feats["team_oaa_gap"] = h_oaa - a_oaa
            h_frp = float(idx.loc[home, "frp_total"])
            a_frp = float(idx.loc[away, "frp_total"])
            if np.isfinite(h_frp) and np.isfinite(a_frp):
                feats["team_frp_gap"] = h_frp - a_frp

    frv_tbl = team_frv_table_as_of(as_of)
    if not frv_tbl.empty:
        idx = frv_tbl.set_index("team_abbr")
        if home in idx.index and away in idx.index:
            h_frv = float(idx.loc[home, "frv_total"])
            a_frv = float(idx.loc[away, "frv_total"])
            if np.isfinite(h_frv) and np.isfinite(a_frv):
                feats["team_frv_gap"] = h_frv - a_frv

    return feats


def _zero_gaps() -> Dict[str, float]:
    return {
        "team_oaa_gap": 0.0,
        "team_frp_gap": 0.0,
        "team_frv_gap": 0.0,
    }


DEFENSE_FEATURES: List[str] = list(_zero_gaps().keys())
