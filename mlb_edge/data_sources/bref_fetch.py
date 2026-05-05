"""
Standings fetcher for the B-R loader. Writes 8 CSVs in the canonical
    {YYYYMMDD}_upto-{slug}.csv
shape the B-R loader expects (Tm, W, L, W-L%, GB, RS, RA, pythW-L%).

Data source: MLB Stats API's /standings endpoint. This is the same
unauth'd API we use for schedule. It returns wins/losses/runs scored/
runs allowed per team per division — we derive W-L% and pythW-L%
ourselves (Bill James: RS^2 / (RS^2 + RA^2)).

Why not B-R directly: baseball-reference.com is Cloudflare-protected
and 403s even with a plausible UA. MLB Stats API returns identical
standings data unauthenticated.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests

log = logging.getLogger(__name__)

STANDINGS_DIR = Path("data/bref/standings")

# Team ID -> 3-letter abbreviation (B-R convention). We already keep an
# alias map in the loader for SFG/TBR/KCR -> SF/TB/KC, so here we write
# the canonical 3-letter codes.
_TEAM_ABBR: Dict[int, str] = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KCR", 119: "LAD", 120: "WSN", 121: "NYM", 133: "ATH",
    134: "PIT", 135: "SDP", 136: "SEA", 137: "SFG", 138: "STL",
    139: "TBR", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CHW", 146: "MIA", 147: "NYY", 158: "MIL",
}

# MLB Stats API division id -> our slug.
_DIV_SLUG: Dict[int, str] = {
    201: "AL-E",
    202: "AL-C",
    200: "AL-W",
    204: "NL-E",
    205: "NL-C",
    203: "NL-W",
}

# MLB Stats API league id -> overall slug.
_LEAGUE_OVERALL: Dict[int, str] = {103: "AL-overall", 104: "NL-overall"}


def _pythagorean(rs: int, ra: int) -> float:
    if rs + ra == 0:
        return 0.0
    return round((rs ** 2) / (rs ** 2 + ra ** 2), 3)


def _row_from_tr(rec: dict) -> dict:
    tm = _TEAM_ABBR.get(rec["team"]["id"], rec["team"].get("abbreviation", "???"))
    w = int(rec.get("wins", 0))
    l = int(rec.get("losses", 0))
    rs = int(rec.get("runsScored", 0))
    ra = int(rec.get("runsAllowed", 0))
    pct = round(w / max(w + l, 1), 3)
    gb = rec.get("gamesBack", "--")
    return {
        "Tm": tm,
        "W": w,
        "L": l,
        "W-L%": f"{pct:.3f}".lstrip("0") if pct < 1 else "1.000",
        "GB": gb if gb else "--",
        "RS": rs,
        "RA": ra,
        "pythW-L%": f"{_pythagorean(rs, ra):.3f}".lstrip("0"),
    }


def _fetch_standings_json(as_of: date) -> dict:
    url = (
        "https://statsapi.mlb.com/api/v1/standings"
        f"?leagueId=103,104&season={as_of.year}"
        "&standingsTypes=regularSeason"
        f"&date={as_of.isoformat()}"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_standings(as_of: date,
                    out_dir: Path = STANDINGS_DIR) -> List[Path]:
    """
    Fetch standings for `as_of` and write 8 canonical CSVs under `out_dir`.
    Returns the list of paths written.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _fetch_standings_json(as_of)

    stamp = as_of.strftime("%Y%m%d")
    by_slug: Dict[str, List[dict]] = {}
    by_league: Dict[str, List[dict]] = {"AL-overall": [], "NL-overall": []}

    for rec in data.get("records", []):
        div_id = rec.get("division", {}).get("id")
        league_id = rec.get("league", {}).get("id")
        slug = _DIV_SLUG.get(div_id)
        overall_slug = _LEAGUE_OVERALL.get(league_id)
        if not slug:
            continue
        by_slug.setdefault(slug, [])
        for tr in rec.get("teamRecords", []):
            row = _row_from_tr(tr)
            by_slug[slug].append(row)
            if overall_slug:
                by_league[overall_slug].append(row)

    # Sort each division by wins desc, losses asc (B-R convention).
    def _sort_key(r: dict):
        return (-r["W"], r["L"])

    written: List[Path] = []
    for slug, rows in list(by_slug.items()) + list(by_league.items()):
        if not rows:
            continue
        rows = sorted(rows, key=_sort_key)
        df = pd.DataFrame(rows, columns=["Tm", "W", "L", "W-L%", "GB",
                                         "RS", "RA", "pythW-L%"])
        dest = out_dir / f"{stamp}_upto-{slug}.csv"
        df.to_csv(dest, index=False, quoting=1)
        written.append(dest)

    log.info("B-R-compatible standings written: %d files for %s",
             len(written), as_of)
    return written


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    target = date.today() if len(sys.argv) < 2 else date.fromisoformat(sys.argv[1])
    fetch_standings(target)
