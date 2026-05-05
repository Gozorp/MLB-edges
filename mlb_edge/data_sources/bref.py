"""
bref.py
-------
Load Baseball-Reference daily standings snapshots and expose team-level
form gaps to the pipeline.

Input files (produced by the Chrome-authenticated B-R scraper):

    ./data/bref/standings/{YYYYMMDD}_upto-{AL-E,AL-C,AL-W,NL-E,NL-C,NL-W,
                                           AL-overall,NL-overall}.csv

Each CSV has the literal column set B-R returns:

    "Tm","W","L","W-L%","GB","RS","RA","pythW-L%"

`standings-upto-*` means "standings including this date's games" — the
only variant we save. (`standings-after-*` is the same payload under a
different key and is skipped to avoid duplication.)

Gap features exposed (positive = home advantage):

    team_win_pct_gap        (home W-L% - away W-L%)
    team_run_diff_pg_gap    (home (RS-RA)/G - away (RS-RA)/G)
    team_pythagorean_gap    (home pythW-L% - away pythW-L%)
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..stadiums import normalize_team

log = logging.getLogger(__name__)


BREF_DIR = Path("./data/bref")
STANDINGS_DIR = BREF_DIR / "standings"

# B-R uses retrosheet-style codes on some pages and "modern" codes elsewhere.
# Map everything we've seen in B-R files to the canonical pipeline codes
# (the keys of `stadiums.STADIUMS` — e.g. "SF", not "SFG" or "SFN").
BREF_TO_CANONICAL: Dict[str, str] = {
    # Retrosheet-style (appear in /previews/ and /boxes/ URLs)
    "ANA": "LAA", "CHA": "CHW", "CHN": "CHC", "KCA": "KC", "LAN": "LAD",
    "NYA": "NYY", "NYN": "NYM", "SDN": "SD",  "SFN": "SF", "SLN": "STL",
    "TBA": "TB",  "WAS": "WSH",
    # "Modern" codes B-R uses on standings pages
    "ATH": "OAK", "KCR": "KC",  "SDP": "SD", "SFG": "SF", "TBR": "TB",
    "WSN": "WSH",
}

STANDINGS_FILE_RE = re.compile(
    r"^(\d{8})_upto-(AL-E|AL-C|AL-W|NL-E|NL-C|NL-W|AL-overall|NL-overall)\.csv$"
)


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def _parse_date_from_name(name: str) -> Optional[date]:
    m = STANDINGS_FILE_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def available_dates(root: Path = STANDINGS_DIR) -> List[date]:
    """Dates (deduped, sorted) for which we have at least one standings CSV."""
    if not root.exists():
        return []
    dates = set()
    for p in root.glob("*_upto-*.csv"):
        d = _parse_date_from_name(p.name)
        if d is not None:
            dates.add(d)
    return sorted(dates)


def latest_date_on_or_before(as_of: date,
                             root: Path = STANDINGS_DIR) -> Optional[date]:
    """Most recent standings snapshot date <= as_of."""
    candidates = [d for d in available_dates(root) if d <= as_of]
    return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def _read_standings_csv(path: Path) -> pd.DataFrame:
    """Read a single B-R standings CSV and cast numeric columns."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Normalize headers — B-R uses quoted strings including "W-L%"
    df.columns = [c.strip() for c in df.columns]
    for col in ("W", "L", "RS", "RA"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("W-L%", "pythW-L%"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _merge_league_tables(paths: List[Path]) -> pd.DataFrame:
    """Concat AL-overall + NL-overall (or divisional files) into one table."""
    frames = []
    for p in paths:
        try:
            frames.append(_read_standings_csv(p))
        except Exception as e:
            log.warning("Failed to read %s: %s", p, e)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # Dedupe (AL-overall + NL-overall cover the same 30 teams as the
    # divisional files — drop duplicates keeping the first occurrence).
    if "Tm" in out.columns:
        out = out.drop_duplicates(subset=["Tm"], keep="first")
    return out


# ---------------------------------------------------------------------------
# Team aggregation
# ---------------------------------------------------------------------------
def _to_canonical(abbr: str) -> str:
    """B-R team code → pipeline canonical code."""
    if abbr in BREF_TO_CANONICAL:
        return BREF_TO_CANONICAL[abbr]
    return normalize_team(abbr)


@lru_cache(maxsize=32)
def team_form_as_of(as_of: date) -> pd.DataFrame:
    """
    Team standings snapshot including all games through `as_of`.

    Returns columns: team_abbr (canonical), W, L, G, win_pct, run_diff_pg,
                     pyth_win_pct. Empty frame if no snapshot exists.
    """
    snap_date = latest_date_on_or_before(as_of)
    if snap_date is None:
        log.info("No B-R standings snapshot on or before %s", as_of)
        return pd.DataFrame()

    # Prefer the 2 overall files; fall back to 6 divisional files.
    stem = snap_date.strftime("%Y%m%d")
    overall = [
        STANDINGS_DIR / f"{stem}_upto-AL-overall.csv",
        STANDINGS_DIR / f"{stem}_upto-NL-overall.csv",
    ]
    paths = [p for p in overall if p.exists()]
    if len(paths) < 2:
        # Fall back to divisions
        paths = sorted(STANDINGS_DIR.glob(f"{stem}_upto-*.csv"))

    log.info("Using B-R standings from %s (%d files)", snap_date, len(paths))
    raw = _merge_league_tables(paths)
    if raw.empty:
        return pd.DataFrame()

    rows = []
    for _, r in raw.iterrows():
        abbr = _to_canonical(str(r.get("Tm", "")).strip())
        w = float(r.get("W", np.nan))
        l = float(r.get("L", np.nan))
        if not (np.isfinite(w) and np.isfinite(l)) or (w + l) == 0:
            continue
        g = w + l
        rs = float(r.get("RS", np.nan))
        ra = float(r.get("RA", np.nan))
        run_diff_pg = (rs - ra) / g if np.isfinite(rs) and np.isfinite(ra) else np.nan
        rows.append({
            "team_abbr":     abbr,
            "W":             int(w),
            "L":             int(l),
            "G":             int(g),
            "win_pct":       float(r.get("W-L%", w / g)),
            "run_diff_pg":   run_diff_pg,
            "pyth_win_pct":  float(r.get("pythW-L%", np.nan)),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def team_form_gap_features(home_team: str,
                           away_team: str,
                           as_of: date) -> Dict[str, float]:
    """
    {feature: home - away} gaps from the most recent B-R standings snapshot.
    Positive values = home advantage, matching the pipeline's monotone-
    constraint convention.
    """
    home = normalize_team(home_team)
    away = normalize_team(away_team)

    tbl = team_form_as_of(as_of)
    if tbl.empty:
        return _zero_gaps()

    idx = tbl.set_index("team_abbr")
    if home not in idx.index or away not in idx.index:
        log.debug("Missing B-R form rows: home=%s away=%s (have %d teams)",
                  home, away, len(idx))
        return _zero_gaps()

    h, a = idx.loc[home], idx.loc[away]

    def g(col: str) -> float:
        hv, av = float(h.get(col, np.nan)), float(a.get(col, np.nan))
        if not (np.isfinite(hv) and np.isfinite(av)):
            return 0.0
        return hv - av

    return {
        "team_win_pct_gap":      g("win_pct"),
        "team_run_diff_pg_gap":  g("run_diff_pg"),
        "team_pythagorean_gap":  g("pyth_win_pct"),
    }


def _zero_gaps() -> Dict[str, float]:
    return {
        "team_win_pct_gap":     0.0,
        "team_run_diff_pg_gap": 0.0,
        "team_pythagorean_gap": 0.0,
    }


TEAM_FORM_FEATURES: List[str] = list(_zero_gaps().keys())
