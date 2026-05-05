"""
mlb_edge/odds_fallback.py
-------------------------
ESPN odds fallback for when the-odds-api.com returns nothing.

Background: on the 2026-05-01 slate, the primary OddsClient call returned
no data (rate-limited / transient failure / network), leaving the entire
slate file with blank ``fair_prob`` columns.  Without market context the
parlay grader had no edge-vs-market check, the grader was effectively
flying blind, and the model went 4-9 with multiple overconfident A/B+
picks (LAD A- anchor lost outright).

This module provides a fallback that scrapes ESPN's free public MLB
odds page (https://www.espn.com/mlb/lines, redirects to /mlb/odds) and
reformats the moneyline data into the same long-format DataFrame schema
that ``data_ingestion._flatten_odds_payload`` produces, so the rest of
the pipeline (``edge_calculator.recommend_slate``, the ``shin`` de-vig,
the ``fair_prob`` derivation) all works unchanged.

Public API:
    fetch_espn_mlb_odds(slate_date) -> pd.DataFrame
        Returns DataFrame with columns
            game_id, commence_time, home_team, away_team,
            book, last_update, market, outcome, price, point
        suitable for passing as the ``odds`` arg to
        ``edge_calculator.recommend_slate``.

    backfill_missing_odds(primary_df, slate_date) -> pd.DataFrame
        Take the primary OddsClient DataFrame, identify games without h2h
        coverage, and append rows from the ESPN fallback for just those
        games.  Existing rows are left untouched.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

log = logging.getLogger(__name__)


ESPN_ODDS_URL = "https://www.espn.com/mlb/lines"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 30 MLB team full names exactly as ESPN renders them.  Used as anchors
# to find each team block in the parsed text.
MLB_FULL_NAMES = {
    "Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles",
    "Boston Red Sox", "Chicago Cubs", "Chicago White Sox",
    "Cincinnati Reds", "Cleveland Guardians", "Colorado Rockies",
    "Detroit Tigers", "Houston Astros", "Kansas City Royals",
    "Los Angeles Angels", "Los Angeles Dodgers", "Miami Marlins",
    "Milwaukee Brewers", "Minnesota Twins", "New York Mets",
    "New York Yankees", "Athletics", "Philadelphia Phillies",
    "Pittsburgh Pirates", "San Diego Padres", "San Francisco Giants",
    "Seattle Mariners", "St. Louis Cardinals", "Tampa Bay Rays",
    "Texas Rangers", "Toronto Blue Jays", "Washington Nationals",
}

# Some pages still use legacy "Oakland Athletics"; we'll normalize.
TEAM_NAME_ALIASES = {
    "Oakland Athletics": "Athletics",
}


# ---------------------------------------------------------------------------
# HTML → newline-delimited text
# ---------------------------------------------------------------------------
def _html_to_text(html: str) -> str:
    """Convert ESPN's HTML page into innerText-style line-broken text.

    Prefers BeautifulSoup if available (more robust); falls back to
    a stdlib html.parser-based shim that emits newlines between block
    elements.  Either way the output is suitable for line-based parsing
    below.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        # Add explicit newlines after block tags so .get_text("\n")
        # produces line-broken output similar to browser innerText.
        for tag in soup.find_all(["tr", "td", "div", "li", "br",
                                   "span", "p", "h1", "h2", "h3"]):
            tag.append("\n")
        text = soup.get_text("\n")
    except Exception:  # pragma: no cover — bs4 always present in project
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            BLOCK_TAGS = {"tr", "td", "div", "li", "br", "p", "span",
                          "h1", "h2", "h3", "h4", "section", "article"}

            def __init__(self):
                super().__init__()
                self.parts: List[str] = []

            def handle_starttag(self, tag, attrs):
                if tag in self.BLOCK_TAGS:
                    self.parts.append("\n")

            def handle_endtag(self, tag):
                if tag in self.BLOCK_TAGS:
                    self.parts.append("\n")

            def handle_data(self, data):
                self.parts.append(data)

        ex = _TextExtractor()
        ex.feed(html)
        text = "".join(ex.parts)

    # Collapse runs of blank lines, strip per-line whitespace
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------
ML_PRICE_RE = re.compile(r"^[+\-]\d{2,4}$")


def _parse_ml_price(s: str) -> Optional[int]:
    """Parse '-122' or '+163' to int. Return None if not an ML price."""
    s = s.strip()
    if not ML_PRICE_RE.match(s):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_text_to_team_blocks(text: str) -> List[Dict]:
    """Walk line-broken page text and emit one record per team encountered.

    Discovered structure: a normal team block is 9 lines, anchored on the
    team's full name.  Layout:

        line 0: <Full Team Name>          (e.g. 'Texas Rangers')
        line 1: <Pitcher (W-L, ERA)>      (e.g. 'Jack Leiter (1-2, 5.17)')
        line 2: <Open value 1>            (e.g. '-105' or 'u8')
        line 3: <Open value 2>            (e.g. 'ML' or '-105')
        line 4: <Current ML price>        (e.g. '+102' or '-122')  ★
        line 5: <Current Total over/under>(e.g. 'o8.5')
        line 6: <Current Total juice>
        line 7: <RL spread>
        line 8: <RL juice>

    BUT — when one team has 'Undecided' pitcher (e.g. probable not yet
    announced), ESPN suppresses Total/RL entirely and the block collapses
    to ~6 lines:

        line 0: 'Cincinnati Reds'
        line 1: 'Undecided'
        line 2: '--'
        line 3: '+163'    ★ ML at offset +3, not +4
        line 4: '--'
        line 5: '--'

    To handle both layouts robustly: scan the next ~6 lines for the ML
    price.  In the normal case, prefer the price immediately preceding
    a 'o\\d' / 'u\\d' (current Total) marker.  In the Undecided case,
    accept any ML-shaped price.

    CRITICAL: emit a record for EVERY team encountered (even if ml is
    None).  This preserves the (away, home) pairing order in the text;
    dropping a team on missing ML caused CHC to mis-pair with CLE on
    5/3, putting the away/home identity completely wrong for the rest
    of the slate.
    """
    lines = text.split("\n")
    out: List[Dict] = []
    i = 0
    # Track which date header we're under, so caller can filter by date.
    # ESPN renders headers like "Sunday, May 3" / "Monday, May 4" before
    # each day's games.  We propagate this via the `date_header` field.
    DATE_HDR_RE = re.compile(
        r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2}$"
    )
    current_date_hdr = ""
    while i < len(lines):
        line = lines[i].strip()
        if DATE_HDR_RE.match(line):
            current_date_hdr = line
            i += 1
            continue
        name = TEAM_NAME_ALIASES.get(line, line)
        if name not in MLB_FULL_NAMES:
            i += 1
            continue

        # Look ahead up to 8 lines for the ML price.
        # Prefer a price immediately preceding an over/under marker
        # (canonical 9-line block layout).  Fall back to any ML-shaped
        # price (Undecided / collapsed block layout).
        ml: Optional[int] = None
        for k in range(1, 9):
            if i + k >= len(lines):
                break
            cand = lines[i + k].strip()
            nxt = lines[i + k + 1].strip() if i + k + 1 < len(lines) else ""
            if _parse_ml_price(cand) is not None and re.match(r"^[ou]\d", nxt):
                ml = _parse_ml_price(cand)
                break
        if ml is None:
            # Collapsed-block fallback: take the first ML-shaped price
            # in the next 6 lines that isn't part of a Total marker
            # (i.e. not preceded by '--' which indicates the open column
            # was suppressed and the next price is the current ML).
            for k in range(1, 7):
                if i + k >= len(lines):
                    break
                cand = lines[i + k].strip()
                p = _parse_ml_price(cand)
                if p is not None:
                    # Don't grab the OPEN ML price by mistake — the open
                    # comes before the 'ML' label or before the current
                    # total.  In the collapsed case the only price IS the
                    # current ML, so take it.
                    ml = p
                    break

        out.append({
            "team": name,
            "ml": ml,
            "pitcher_line": lines[i + 1].strip() if i + 1 < len(lines) else "",
            "date_header": current_date_hdr,
        })
        # Advance one line at a time — the team-name check handles dedup,
        # and fixed advances broke the Cincinnati case (collapsed 6-line
        # block + advance=9 skipped CHC entirely, mis-pairing the rest of
        # the slate).
        i += 1
    return out


def _pair_into_games(team_records: List[Dict]) -> List[Tuple[Dict, Dict]]:
    """Pair consecutive (away, home) team records into games.

    ESPN renders away team first, then home team within each game block.
    Pairing them sequentially recovers the (away, home) tuples.

    Drops any pair where either side is missing its ML — those games
    can't contribute to fair_prob anyway.
    """
    pairs: List[Tuple[Dict, Dict]] = []
    for i in range(0, len(team_records) - 1, 2):
        away, home = team_records[i], team_records[i + 1]
        if away.get("ml") is None or home.get("ml") is None:
            log.info("ESPN fallback: dropping %s @ %s (missing ML on one side)",
                     away.get("team"), home.get("team"))
            continue
        pairs.append((away, home))
    return pairs


# ---------------------------------------------------------------------------
# DataFrame assembly
# ---------------------------------------------------------------------------
def _records_to_dataframe(pairs: List[Tuple[Dict, Dict]],
                          slate_date: date) -> pd.DataFrame:
    """Build a long-format DataFrame matching `_flatten_odds_payload`."""
    # Approximate commence_time: midnight ET on the slate date.  The
    # downstream code only uses the *date* derived from this field
    # (see edge_calculator.recommend_slate, line ~347), so the time
    # itself doesn't need to be precise.
    commence = datetime.combine(slate_date, datetime.min.time(),
                                 tzinfo=timezone.utc).isoformat()
    last_update = datetime.now(timezone.utc).isoformat()

    rows: List[Dict] = []
    for idx, (away, home) in enumerate(pairs):
        gid = f"espn-fallback-{slate_date.isoformat()}-{idx:02d}"
        base = {
            "game_id":        gid,
            "commence_time":  commence,
            "home_team":      home["team"],
            "away_team":      away["team"],
            "book":           "espn_consensus",
            "last_update":    last_update,
            "market":         "h2h",
            "point":          None,
        }
        # Two outcome rows per game (away then home), price = American ML
        rows.append({**base, "outcome": away["team"], "price": away["ml"]})
        rows.append({**base, "outcome": home["team"], "price": home["ml"]})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_espn_mlb_odds(slate_date: Optional[date] = None,
                        timeout: int = 15) -> pd.DataFrame:
    """Fetch + parse ESPN's MLB odds page; return long-format odds DF.

    Returns empty DataFrame on any failure (network, parse error,
    no games found).  Caller should treat empty as "fallback didn't
    fire" and proceed without market data.
    """
    if slate_date is None:
        slate_date = date.today()

    try:
        r = requests.get(ESPN_ODDS_URL,
                         headers={"User-Agent": USER_AGENT},
                         timeout=timeout)
        if r.status_code != 200:
            log.warning("ESPN fallback HTTP %s", r.status_code)
            return pd.DataFrame()
        html = r.text
    except requests.RequestException as e:
        log.warning("ESPN fallback request failed: %s", e)
        return pd.DataFrame()

    text = _html_to_text(html)
    teams = _parse_text_to_team_blocks(text)
    if len(teams) < 2 or len(teams) % 2 != 0:
        log.warning("ESPN fallback parsed %d team records — not a clean "
                    "pairing, returning empty", len(teams))
        return pd.DataFrame()

    # Filter teams to ONLY those under the requested slate_date's header.
    # ESPN's page mixes today + tomorrow's games (especially after some of
    # today's games have started).  Without the filter we'd silently mis-
    # populate fair_prob for the wrong date — downstream merge would drop
    # the wrong-date rows anyway, but the log message is misleading.
    # Build the header manually because Windows strftime doesn't accept %-d.
    expected_hdr = slate_date.strftime("%A, %B ") + str(slate_date.day)
    matched = [t for t in teams if t.get("date_header") == expected_hdr]
    if matched:
        teams = matched
        log.info("ESPN fallback: filtered to %d team records matching "
                 "date header %r", len(teams), expected_hdr)
    else:
        log.warning("ESPN fallback: no teams matched date header %r — "
                    "ESPN may not have posted %s odds yet, or all of "
                    "%s's games already started", expected_hdr,
                    slate_date.isoformat(), slate_date.isoformat())
        return pd.DataFrame()

    pairs = _pair_into_games(teams)
    df = _records_to_dataframe(pairs, slate_date)
    log.info("ESPN fallback returned %d games (%d odds rows)",
             len(pairs), len(df))
    return df


def backfill_missing_odds(primary_df: pd.DataFrame,
                          slate_date: Optional[date] = None) -> pd.DataFrame:
    """Append ESPN fallback rows for games not covered by `primary_df`.

    Idempotent: if every scheduled game is already represented in
    `primary_df` with at least one h2h price, returns `primary_df`
    unchanged.  Otherwise fetches ESPN odds and appends rows ONLY for
    the games missing from the primary source.
    """
    if primary_df is None:
        primary_df = pd.DataFrame()

    if not primary_df.empty:
        # Identify games already covered (by full team-name pair)
        covered = set()
        h2h = primary_df[primary_df.get("market") == "h2h"] \
            if "market" in primary_df.columns else primary_df
        for _, r in h2h.iterrows():
            covered.add((str(r.get("away_team", "")).strip(),
                         str(r.get("home_team", "")).strip()))
    else:
        covered = set()

    espn = fetch_espn_mlb_odds(slate_date)
    if espn.empty:
        return primary_df

    # Filter ESPN rows to only games NOT already covered
    keep = []
    for _, r in espn.iterrows():
        key = (str(r.get("away_team", "")).strip(),
               str(r.get("home_team", "")).strip())
        if key not in covered:
            keep.append(r)

    if not keep:
        log.info("ESPN fallback: all games already covered by primary, skipping")
        return primary_df

    backfill = pd.DataFrame(keep)
    log.info("ESPN fallback: backfilling %d games (%d odds rows)",
             len(backfill) // 2, len(backfill))

    if primary_df.empty:
        return backfill
    return pd.concat([primary_df, backfill], ignore_index=True)


# ---------------------------------------------------------------------------
# CLI for ad-hoc verification
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sd = (date.fromisoformat(sys.argv[1])
          if len(sys.argv) > 1 else date.today())
    df = fetch_espn_mlb_odds(sd)
    if df.empty:
        print("No odds returned.")
        raise SystemExit(1)
    # Pretty-print the games
    games = df[df["market"] == "h2h"].copy()
    pivot = (games.pivot_table(index=["away_team", "home_team"],
                                columns="outcome", values="price",
                                aggfunc="first")
             .reset_index())
    print(f"Fetched {len(pivot)} games for {sd.isoformat()}:\n")
    print(pivot.to_string(index=False))
