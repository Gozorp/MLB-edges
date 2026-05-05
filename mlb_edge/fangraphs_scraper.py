"""
mlb_edge/fangraphs_scraper.py
-----------------------------
FanGraphs SP leaderboard scraper.  Daily-refresh fallback / replacement
for Baseball Savant when Savant goes stale (which it did from 4/27 →
5/1 on this project, contributing to the 4-9 record on the 5/1 slate
when the model couldn't see fresh xERA / xwOBA estimates).

WHY FANGRAPHS
=============
- Updates daily (no freeze-mode failure)
- Has *more* advanced metrics than Savant: xERA, FIP, xFIP, SIERA,
  Stuff+, Location+, Pitching+, full plate-discipline aggregates
- Custom date-range filter (Last 7/14/30 days, Custom) — lets the
  pipeline pick up SP form trends without waiting for full-season
  averages
- Splits (vs LHH, vs RHH, Home/Away, High/Med/Low Leverage, RISP)
- No auth needed; HTML is publicly served

LIMITATIONS
===========
- FanGraphs serves HTML tables, not CSV.  We parse with BeautifulSoup.
- "Data Export" CSV download is members-only (paywall).  The public
  HTML table is the access path we use.
- Pitcher names are FanGraphs-canonical (e.g., "Cristopher Sánchez"
  with the accent).  Caller is responsible for normalization on join.

ENDPOINT INVENTORY
==================
Each endpoint is a parameterized URL + a destination cache file.
Validation is column-count-based (FanGraphs HTML structure is stable;
column count drifts indicate a layout change worth catching).

Public API:
    refresh_all(year) -> Dict[str, Optional[Path]]
    fetch_endpoint(spec, year, overwrite=False) -> Optional[Path]
    load_cached(name, year) -> Optional[pandas.DataFrame]
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "mlb_edge/v14 fangraphs-scraper"
)


# ---------------------------------------------------------------------------
# Endpoint registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FGEndpoint:
    """One FanGraphs leaderboard view we want to cache."""
    name: str                       # short token used in filenames + logs
    url_template: str               # URL with {year} placeholder
    out_dir: Path                   # destination directory
    out_filename: str               # filename template (substitutes {ymd})
    expected_cols_min: int = 12     # validation: layout sanity check
    timeout: int = 60


# Built using the parameter conventions verified live on 2026-05-03:
#   stats=pit    : pitching leaderboard
#   pos=all      : all positions
#   lg=all       : both leagues
#   qual=0       : no minimum-IP filter (we want EVERY potential SP)
#   season=YYYY  : season filter
#   season1=YYYY : multi-season range start (set equal to season for single year)
#   ind=0        : season totals (not per-game)
#   type=8       : Dashboard preset (W/L/IP/K9/BB9/ERA/xERA/FIP/xFIP/WAR/vFA)
#   type=24      : Statcast preset (xwOBA/xERA/xSLG/Hard%/Barrel%/EV/LA)
#
# NOTE: when FanGraphs changes a `type=` preset's column composition (rare
# but happens during off-season redesigns), our parser keeps working
# because we treat the table as a generic <thead>+<tbody>; only the column
# *names* change and downstream consumers re-key on header text.
ENDPOINTS: List[FGEndpoint] = [
    FGEndpoint(
        name="sp-dashboard",
        url_template=(
            "https://www.fangraphs.com/leaders/major-league?"
            "pos=all&stats=pit&lg=all&qual=0&type=8&season={year}"
            "&month=0&season1={year}&ind=0&team=0&rost=0&age=0"
            "&filter=&players=0&page=1_500"
        ),
        out_dir=Path("data/fangraphs"),
        out_filename="fg_sp-dashboard_{year}_{ymd}.csv",
        expected_cols_min=15,
    ),
    FGEndpoint(
        name="sp-statcast",
        url_template=(
            "https://www.fangraphs.com/leaders/major-league?"
            "pos=all&stats=pit&lg=all&qual=0&type=24&season={year}"
            "&month=0&season1={year}&ind=0&team=0&rost=0&age=0"
            "&filter=&players=0&page=1_500"
        ),
        out_dir=Path("data/fangraphs"),
        out_filename="fg_sp-statcast_{year}_{ymd}.csv",
        expected_cols_min=12,
    ),
    FGEndpoint(
        name="sp-plate-discipline",
        url_template=(
            "https://www.fangraphs.com/leaders/major-league?"
            "pos=all&stats=pit&lg=all&qual=0&type=5&season={year}"
            "&month=0&season1={year}&ind=0&team=0&rost=0&age=0"
            "&filter=&players=0&page=1_500"
        ),
        out_dir=Path("data/fangraphs"),
        out_filename="fg_sp-plate-discipline_{year}_{ymd}.csv",
        expected_cols_min=10,
    ),
    # Stuff+/Location+/Pitching+ models (FanGraphs presets).  These are
    # the FanGraphs analogue to Savant's swing-take/Stuff+ — directly
    # usable as the F3 conviction-signal input.
    FGEndpoint(
        name="sp-stuff-plus",
        url_template=(
            "https://www.fangraphs.com/leaders/major-league?"
            "pos=all&stats=pit&lg=all&qual=0&type=36&season={year}"
            "&month=0&season1={year}&ind=0&team=0&rost=0&age=0"
            "&filter=&players=0&page=1_500"
        ),
        out_dir=Path("data/fangraphs"),
        out_filename="fg_sp-stuff-plus_{year}_{ymd}.csv",
        expected_cols_min=8,
    ),
]


# ---------------------------------------------------------------------------
# HTML → CSV parsing
# ---------------------------------------------------------------------------
def _parse_table_to_rows(html: str) -> Optional[List[List[str]]]:
    """Extract the FanGraphs leaderboard <table> as a list-of-lists.

    The leaderboard renders as a single <table class="rgMasterTable">
    (legacy class) or wrapped in a div.leaders-major__table on newer
    pages.  We look for the largest <table> on the page that has a
    <thead> and at least 5 <tbody> rows — that's reliably the data
    table, regardless of which CSS class FanGraphs is using this week.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        log.warning("BeautifulSoup not installed; cannot parse FanGraphs HTML")
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for tbl in soup.find_all("table"):
        thead = tbl.find("thead")
        tbody = tbl.find("tbody")
        if not thead or not tbody:
            continue
        body_rows = tbody.find_all("tr")
        if len(body_rows) < 5:
            continue
        candidates.append((len(body_rows), tbl))
    if not candidates:
        return None

    # Largest data table wins
    candidates.sort(key=lambda x: x[0], reverse=True)
    tbl = candidates[0][1]

    # Build header first, identifying which column indices are
    # FanGraphs's "-- Line Break --" visual separators so we can
    # drop them from every row consistently.
    head_cells = tbl.find("thead").find_all(["th", "td"])
    raw_header = [c.get_text(strip=True) for c in head_cells]
    LINE_BREAK_TOKEN = "-- Line Break --"
    keep_idx = [i for i, h in enumerate(raw_header) if h != LINE_BREAK_TOKEN]

    def _filter(row: List[str]) -> List[str]:
        return [row[i] for i in keep_idx if i < len(row)]

    rows: List[List[str]] = [_filter(raw_header)]
    for tr in tbl.find("tbody").find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = [c.get_text(strip=True) for c in cells]
        rows.append(_filter(row))
    return rows


def _validate_rows(rows: Optional[List[List[str]]],
                   min_cols: int) -> Tuple[bool, str]:
    if not rows:
        return False, "no rows"
    header = rows[0]
    if len(header) < min_cols:
        return False, f"only {len(header)} columns in header (min {min_cols})"
    if len(rows) < 6:
        return False, f"only {len(rows) - 1} data rows"
    return True, "ok"


# ---------------------------------------------------------------------------
# Single-endpoint fetch
# ---------------------------------------------------------------------------
def _atomic_write_csv(rows: List[List[str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
    tmp.replace(out_path)


def fetch_endpoint(spec: FGEndpoint, year: int,
                   overwrite: bool = False) -> Optional[Path]:
    """Fetch one FanGraphs endpoint, parse to CSV, write atomically."""
    today = date.today().strftime("%Y%m%d")
    fname = spec.out_filename.format(year=year, ymd=today)
    out_path = spec.out_dir / fname

    if out_path.exists() and not overwrite:
        log.info("[%s] cached at %s — skipping", spec.name, out_path.name)
        return out_path

    url = spec.url_template.format(year=year)
    log.info("[%s] fetching %s", spec.name, fname)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        r = requests.get(url, headers=headers, timeout=spec.timeout,
                         allow_redirects=True)
        r.raise_for_status()
        html = r.text
    except requests.RequestException as e:
        log.warning("[%s] download failed: %s", spec.name, e)
        return None

    rows = _parse_table_to_rows(html)
    ok, reason = _validate_rows(rows, spec.expected_cols_min)
    if not ok:
        log.warning("[%s] parse/validation failed: %s", spec.name, reason)
        return None

    _atomic_write_csv(rows, out_path)
    log.info("[%s] OK -> %s (%d rows × %d cols)",
             spec.name, out_path, len(rows) - 1, len(rows[0]))
    return out_path


# ---------------------------------------------------------------------------
# Bulk refresh
# ---------------------------------------------------------------------------
def refresh_all(year: int,
                overwrite: bool = False,
                pause_seconds: float = 1.5) -> Dict[str, Optional[Path]]:
    """Refresh every registered FanGraphs endpoint.

    pause_seconds is more conservative than the Savant scraper because
    FanGraphs is more strict about request rate; their docs ask for
    sub-1-Hz polling on public scraping.
    """
    results: Dict[str, Optional[Path]] = {}
    for spec in ENDPOINTS:
        results[spec.name] = fetch_endpoint(spec, year, overwrite=overwrite)
        time.sleep(pause_seconds)
    n_ok = sum(1 for p in results.values() if p is not None)
    log.info("FanGraphs scrape complete: %d/%d endpoints OK",
             n_ok, len(results))
    return results


# ---------------------------------------------------------------------------
# Cached-data accessor (used by downstream code)
# ---------------------------------------------------------------------------
def load_cached(name: str, year: Optional[int] = None):
    """Load the most-recent cached snapshot of a named endpoint.

    Returns a pandas DataFrame, or None if no cache exists.
    """
    import glob
    import pandas as pd
    if year is None:
        year = date.today().year
    spec_map = {s.name: s for s in ENDPOINTS}
    if name not in spec_map:
        raise KeyError(f"unknown FanGraphs endpoint: {name!r}; "
                       f"valid: {list(spec_map)}")
    spec = spec_map[name]
    pattern = str(spec.out_dir / spec.out_filename.format(year=year, ymd="*"))
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    try:
        return pd.read_csv(matches[-1])
    except Exception as e:
        log.warning("[%s] load_cached failed for %s: %s",
                    name, matches[-1], e)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="FanGraphs SP leaderboard scraper")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--overwrite", action="store_true",
                   help="Re-download even if a cached file exists for today.")
    p.add_argument("--endpoint", help="Run a single endpoint by name.")
    p.add_argument("--list", action="store_true",
                   help="List all registered endpoints and exit.")
    args = p.parse_args(argv)

    if args.list:
        print(f"Registered endpoints ({len(ENDPOINTS)}):")
        for s in ENDPOINTS:
            print(f"  {s.name:24s} -> {s.out_dir}")
        return

    if args.endpoint:
        spec_map = {s.name: s for s in ENDPOINTS}
        if args.endpoint not in spec_map:
            raise SystemExit(f"unknown endpoint: {args.endpoint}. "
                             f"valid: {list(spec_map)}")
        path = fetch_endpoint(spec_map[args.endpoint], args.year,
                              overwrite=args.overwrite)
        print(path or "FAILED")
        return

    refresh_all(args.year, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
