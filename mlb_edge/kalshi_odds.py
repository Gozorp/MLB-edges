"""
mlb_edge/kalshi_odds.py
-----------------------
Kalshi public-API fallback for when the-odds-api.com returns nothing.

Background: per the platoon-brain MVP commit (39aeb43, 2026-05-19),
when the primary OddsClient call fails or returns empty, the pipeline
currently falls back to ESPN's free public MLB odds page (see
``odds_fallback.py``).  ESPN is fragile — HTML scraping breaks when
ESPN changes layout, and the page mixes today/tomorrow games in ways
that have caused mis-pairing bugs (CHC/CLE swap on 5/3).

Kalshi (https://kalshi.com) is a CFTC-regulated US prediction market
that lists binary contracts on each MLB game.  Their public REST API
is read-only and unauthenticated, the series ticker for MLB is
``KXMLBGAME``, and each game has two binary markets (one YES contract
per team).  Because the contracts are binary and structurally
mutually-exclusive, the two YES prices sum to ~1.00 by construction —
no de-vig math needed.

This module slots into the existing fallback chain BEFORE ESPN:
    primary OddsClient -> Kalshi (new) -> ESPN (existing) -> empty

Per Architecture-Session Pre-Flight Prompt v1.0:
    Rule 1  — probe done (endpoint reachable, KXMLBGAME series confirmed
              with binary markets and yes_bid/yes_ask price fields).
    Rule 2  — test set: 2026-05-19 (today's closed games — sanity check
              that prices look like ~1.00 sum) + future open events.
    Rule 6  — best-effort try/except with logged exceptions throughout.
    Rule 9  — no invented thresholds.  liquidity / open_interest go into
              metadata (book column), no hard gate.
    Rule 11 — reverse-direction sanity: warn when two-team YES probs
              sum outside [0.85, 1.15].

Public API (mirrors odds_fallback.py):
    fetch_kalshi_mlb_odds(slate_date) -> pd.DataFrame
        Long-format DataFrame with the same columns produced by
        ``data_ingestion._flatten_odds_payload`` and the ESPN fallback,
        so the rest of the pipeline (``edge_calculator.recommend_slate``,
        the shin de-vig, the ``fair_prob`` derivation) all works unchanged.

    backfill_missing_odds(primary_df, slate_date) -> pd.DataFrame
        Append Kalshi rows for games not already covered in primary_df.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoint configuration
# ---------------------------------------------------------------------------
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXMLBGAME"
USER_AGENT = "mlb_edge_kalshi_fallback/1.0"

# Kalshi public API rate-limits unauthenticated callers.  Empirically
# tested 2026-05-19: 0.3s inter-request still triggered 429 on ~40% of
# calls.  1.0s reliably stays under the limit and finishes a 15-game
# slate in ~15s.  Since this is fallback code (only fires when primary
# Odds API already failed), reliability > speed.
INTER_REQUEST_DELAY_SEC = 1.0
RATE_LIMIT_RETRY_DELAY_SEC = 3.0

MONTH_ABBREV = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Sanity-check bounds for two-team YES prob sum (Rule 11 reverse-direction).
# Binary mutually-exclusive contracts MUST sum to ~1.00; tolerance covers
# normal bid-ask spread on low-liquidity markets.  Outside this band the
# row is suspect and skipped — better empty than wrong fair_prob.
SUM_SANITY_LOW = 0.85
SUM_SANITY_HIGH = 1.15


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 15,
                retry_on_429: bool = True) -> Dict:
    """GET URL, return parsed JSON.  Raises on any failure.

    If retry_on_429 and the first call returns HTTP 429 (rate-limited),
    sleep RATE_LIMIT_RETRY_DELAY_SEC and try once more before giving up.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429 and retry_on_429:
            log.info("[kalshi] 429 rate-limited, retrying once after %.1fs",
                     RATE_LIMIT_RETRY_DELAY_SEC)
            time.sleep(RATE_LIMIT_RETRY_DELAY_SEC)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        raise


def _slate_date_to_kalshi_prefix(slate_date: date) -> str:
    """`date(2026, 5, 19)` -> `'KXMLBGAME-26MAY19'`.

    Kalshi event ticker format is KXMLBGAME-YYMMMDD-HHMM-AWAYHOME.
    The YYMMMDD prefix is the local-date the game is scheduled.  We
    filter events by this prefix to grab exactly the slate_date's games.
    """
    yy = slate_date.year % 100
    mmm = MONTH_ABBREV[slate_date.month - 1]
    dd = slate_date.day
    return f"KXMLBGAME-{yy:02d}{mmm}{dd:02d}"


# ---------------------------------------------------------------------------
# Events & markets fetching
# ---------------------------------------------------------------------------
def _list_events_for_date(slate_date: date,
                           max_pages: int = 5,
                           timeout: int = 15) -> List[Dict]:
    """Page through KXMLBGAME events and return those matching slate_date.

    Returns empty list if the API is unreachable or no matches found.
    Per Rule 6 — silent failure returns empty, but the exception is
    logged so it's not invisible.
    """
    prefix = _slate_date_to_kalshi_prefix(slate_date)
    cursor = ""
    matched: List[Dict] = []

    for page in range(max_pages):
        params = {
            "series_ticker": KALSHI_SERIES,
            "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor
        url = f"{KALSHI_BASE}/events?" + urllib.parse.urlencode(params)
        try:
            data = _fetch_json(url, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError) as e:
            log.warning("[kalshi] events page %d fetch failed: %s", page, e)
            return matched  # return whatever we got so far

        events = data.get("events", []) or []
        if not events:
            break

        for e in events:
            if e.get("event_ticker", "").startswith(prefix):
                matched.append(e)

        cursor = data.get("cursor", "")
        if not cursor:
            break

    log.info("[kalshi] events: matched %d for prefix %s", len(matched), prefix)
    return matched


def _list_markets_for_event(event_ticker: str,
                             timeout: int = 15) -> List[Dict]:
    """Return all markets (typically 2) for a given event ticker."""
    url = (f"{KALSHI_BASE}/markets?"
           + urllib.parse.urlencode({"event_ticker": event_ticker}))
    try:
        data = _fetch_json(url, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError) as e:
        log.warning("[kalshi] markets fetch failed for %s: %s",
                    event_ticker, e)
        return []
    return data.get("markets", []) or []


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------
def _extract_yes_price(market: Dict) -> Optional[float]:
    """Return YES-side implied probability in [0.0, 1.0], or None.

    Priority: mid(yes_bid, yes_ask) if both > 0;
              else last_price_dollars if > 0;
              else yes_ask alone if > 0;
              else None (no usable price).
    """
    def _f(key: str) -> Optional[float]:
        v = market.get(key)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if 0.0 <= f <= 1.0:
            return f
        return None

    bid = _f("yes_bid_dollars")
    ask = _f("yes_ask_dollars")
    last = _f("last_price_dollars")

    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last is not None and last > 0:
        return last
    if ask is not None and ask > 0:
        return ask
    return None


def _prob_to_american(prob: float) -> int:
    """Convert implied probability to American moneyline odds (int)."""
    if prob <= 0.0 or prob >= 1.0:
        return -10000 if prob >= 0.5 else 10000
    if prob >= 0.5:
        return int(round(-100.0 * prob / (1.0 - prob)))
    return int(round(100.0 * (1.0 - prob) / prob))


def _parse_event_ticker(event_ticker: str) -> Optional[Tuple[str, str]]:
    """Extract (away_abbrev, home_abbrev) from a Kalshi event ticker.

    Format: ``KXMLBGAME-{YYMMMDD}{HHMM}{AWAY}{HOME}`` (YYMMMDD=7 chars,
    HHMM=4 chars, so team-part starts at offset 11 of the tail).
    """
    try:
        tail = event_ticker.split("-", 1)[1]
        team_part = tail[11:]
        return _split_team_pair(team_part)
    except (IndexError, ValueError):
        return None


# All MLB team abbrevs as they appear in Kalshi tickers.  Built from
# observation of the live API (5/19 + 5/22 slates).  Includes both
# canonical (CHW, ARI, OAK, WSH) and Kalshi-source variants (CWS, AZ,
# ATH, WAS) — the downstream `stadiums.normalize_team` collapses these.
_KALSHI_TEAM_TICKERS = {
    # 2-letter
    "TB", "SD", "SF", "KC", "AZ",
    # 3-letter
    "ATL", "MIA", "NYY", "NYM", "BOS", "BAL", "TOR", "TEX", "HOU",
    "MIN", "CIN", "PHI", "PIT", "STL", "MIL", "CHC", "CHW", "CWS",
    "CLE", "DET", "OAK", "ATH", "LAA", "LAD", "SEA", "COL", "ARI",
    "WSH", "WAS",
}


def _split_team_pair(s: str) -> Optional[Tuple[str, str]]:
    """Split a concatenated AWAYHOME string by trying 2/3/4-letter splits.

    Examples:
        "BALTB"  -> ("BAL", "TB")
        "CWSSF"  -> ("CWS", "SF")
        "ATHSD"  -> ("ATH", "SD")
        "TEXLAA" -> ("TEX", "LAA")
        "TORNYY" -> ("TOR", "NYY")
    """
    for split_at in (2, 3, 4):
        away = s[:split_at]
        home = s[split_at:]
        if away in _KALSHI_TEAM_TICKERS and home in _KALSHI_TEAM_TICKERS:
            return (away, home)
    return None


# ---------------------------------------------------------------------------
# Market-to-team mapping
# ---------------------------------------------------------------------------
def _market_team_abbrev(market: Dict, event_pair: Tuple[str, str]) -> Optional[str]:
    """Given a market dict and the event's (away, home) tuple, return
    which side this market represents.

    Strategy: the market ticker is ``{event_ticker}-{TEAM}`` where TEAM
    is one of the two abbrevs from event_pair.  We split on the last "-"
    of the market ticker and match.
    """
    ticker = market.get("ticker", "")
    if "-" not in ticker:
        return None
    suffix = ticker.rsplit("-", 1)[1]
    if suffix in event_pair:
        return suffix
    return None


# ---------------------------------------------------------------------------
# DataFrame assembly
# ---------------------------------------------------------------------------
def _games_to_dataframe(games: List[Dict],
                        slate_date: date) -> pd.DataFrame:
    """Build the long-format odds DataFrame from extracted game data."""
    commence = datetime.combine(slate_date, datetime.min.time(),
                                tzinfo=timezone.utc).isoformat()
    last_update = datetime.now(timezone.utc).isoformat()

    rows: List[Dict] = []
    for idx, g in enumerate(games):
        gid = f"kalshi-fallback-{slate_date.isoformat()}-{idx:02d}"
        away_abbrev = g["away"]["abbrev"]
        home_abbrev = g["home"]["abbrev"]
        base = {
            "game_id":        gid,
            "commence_time":  commence,
            "home_team":      home_abbrev,
            "away_team":      away_abbrev,
            "book":           "kalshi",
            "last_update":    last_update,
            "market":         "h2h",
            "point":          None,
        }
        rows.append({**base, "outcome": away_abbrev,
                     "price": g["away"]["american"]})
        rows.append({**base, "outcome": home_abbrev,
                     "price": g["home"]["american"]})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_kalshi_mlb_odds(slate_date: Optional[date] = None,
                          timeout: int = 15) -> pd.DataFrame:
    """Fetch Kalshi public-API odds for ``slate_date``; return long-format DF.

    Returns empty DataFrame on any failure (network, parse error, no
    matching events).  Caller should treat empty as "Kalshi fallback
    didn't fire" and proceed to the next fallback (ESPN) or accept
    blank fair_prob for the affected rows.
    """
    if slate_date is None:
        slate_date = date.today()

    events = _list_events_for_date(slate_date, timeout=timeout)
    if not events:
        log.info("[kalshi] no events found for %s — returning empty",
                 slate_date.isoformat())
        return pd.DataFrame()

    games: List[Dict] = []
    skipped_pair = skipped_price = skipped_sanity = 0

    for ev_idx, ev in enumerate(events):
        ticker = ev.get("event_ticker", "")
        pair = _parse_event_ticker(ticker)
        if pair is None:
            log.warning("[kalshi] couldn't parse teams from ticker %s",
                        ticker)
            skipped_pair += 1
            continue
        away_abbrev, home_abbrev = pair

        # Throttle between market calls to avoid public-API rate limit
        if ev_idx > 0:
            time.sleep(INTER_REQUEST_DELAY_SEC)
        markets = _list_markets_for_event(ticker, timeout=timeout)
        if len(markets) != 2:
            log.warning("[kalshi] %s returned %d markets (expected 2)",
                        ticker, len(markets))
            skipped_price += 1
            continue

        # Map each market to its team and extract YES price.
        per_team: Dict[str, Dict] = {}
        for m in markets:
            team = _market_team_abbrev(m, pair)
            if team is None:
                continue
            prob = _extract_yes_price(m)
            if prob is None:
                continue

            try:
                liq = float(m.get("liquidity_dollars", 0) or 0)
            except (TypeError, ValueError):
                liq = 0.0
            try:
                oi = float(m.get("open_interest_fp", 0) or 0)
            except (TypeError, ValueError):
                oi = 0.0

            per_team[team] = {
                "abbrev":   team,
                "prob":     prob,
                "american": _prob_to_american(prob),
                "liquidity": liq,
                "oi":        oi,
            }

        if away_abbrev not in per_team or home_abbrev not in per_team:
            log.warning("[kalshi] %s missing per-team price (away=%s "
                        "home=%s, got %s)", ticker, away_abbrev,
                        home_abbrev, list(per_team.keys()))
            skipped_price += 1
            continue

        # Rule 11 reverse-direction check: binary contracts must sum to
        # ~1.00.  Outside [SUM_SANITY_LOW, SUM_SANITY_HIGH] is suspect.
        prob_sum = per_team[away_abbrev]["prob"] + per_team[home_abbrev]["prob"]
        if not (SUM_SANITY_LOW <= prob_sum <= SUM_SANITY_HIGH):
            log.warning("[kalshi] %s YES-prob sum=%.3f outside sanity "
                        "band [%.2f, %.2f] — skipping",
                        ticker, prob_sum, SUM_SANITY_LOW, SUM_SANITY_HIGH)
            skipped_sanity += 1
            continue

        games.append({
            "event_ticker": ticker,
            "away":         per_team[away_abbrev],
            "home":         per_team[home_abbrev],
        })

    log.info("[kalshi] %d games kept (skipped: pair=%d price=%d sanity=%d)",
             len(games), skipped_pair, skipped_price, skipped_sanity)

    if not games:
        return pd.DataFrame()
    return _games_to_dataframe(games, slate_date)


def backfill_missing_odds(primary_df: pd.DataFrame,
                          slate_date: Optional[date] = None,
                          timeout: int = 15) -> pd.DataFrame:
    """Append Kalshi rows for games not covered by ``primary_df``.

    Idempotent: if every scheduled game is already represented in
    ``primary_df`` with at least one h2h price, returns ``primary_df``
    unchanged.  Otherwise fetches Kalshi events and appends rows ONLY
    for the games missing from the primary source.
    """
    if primary_df is None:
        primary_df = pd.DataFrame()

    try:
        from .stadiums import normalize_team
    except ImportError:
        def normalize_team(t):  # type: ignore
            return t

    if not primary_df.empty:
        covered = set()
        h2h = (primary_df[primary_df.get("market") == "h2h"]
               if "market" in primary_df.columns else primary_df)
        for _, r in h2h.iterrows():
            covered.add((normalize_team(str(r.get("away_team", "")).strip()),
                         normalize_team(str(r.get("home_team", "")).strip())))
    else:
        covered = set()

    kal = fetch_kalshi_mlb_odds(slate_date, timeout=timeout)
    if kal.empty:
        return primary_df

    keep = []
    for _, r in kal.iterrows():
        key = (normalize_team(str(r.get("away_team", "")).strip()),
               normalize_team(str(r.get("home_team", "")).strip()))
        if key not in covered:
            keep.append(r)

    if not keep:
        log.info("[kalshi] all games already covered by primary, skipping")
        return primary_df

    backfill = pd.DataFrame(keep)
    log.info("[kalshi] backfilling %d games (%d odds rows)",
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
    df = fetch_kalshi_mlb_odds(sd)
    if df.empty:
        print(f"No Kalshi odds returned for {sd.isoformat()}.")
        raise SystemExit(1)
    games = df[df["market"] == "h2h"].copy()
    pivot = (games.pivot_table(index=["away_team", "home_team"],
                                columns="outcome", values="price",
                                aggfunc="first")
             .reset_index())
    print(f"Fetched {len(pivot)} games for {sd.isoformat()} from Kalshi:\n")
    print(pivot.to_string(index=False))
