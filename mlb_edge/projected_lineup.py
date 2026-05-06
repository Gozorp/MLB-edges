"""
projected_lineup.py
===================
Heuristic projected starting lineup, derived from MLB statsapi recent games.

We use this when MLB hasn't posted the official lineup card yet (typical for a
morning auto-run that fires before the cards drop ~3 hours pre-game).  The
algorithm is the same one most public lineup-projection sites use under the
hood: look at the team's last N games, take the most-frequent starters, and
apply a platoon adjustment for the handedness of today's opposing starter.

Algorithm:
1. Pull the team's last `lookback_games` completed games from statsapi.
2. For each game, get the 9-player starting lineup from the boxscore.
3. Aggregate: for each player, count starts and track average batting order.
4. Score each player:
       score = (starts / lookback_games)              # base recency-weight
             + platoon_boost(batter_hand, sp_throws)  # +0.10 opposite-handed
                                                       # +0.05 switch-hitters
       and exclude any player flagged as injured.
5. Take the top 9 by score; reorder them by average batting position.
6. Return the projected batting-order list of 9 player IDs.

Accuracy on stable rosters: ~90%+ of the actual posted lineup, per backtests
of similar approaches at RotoWire / Lineups.com.  Drops to ~75% on teams
running 4-man platoons or aggressive load management — but BVP aggregation
across 9 players is robust to a couple of mis-projections.

No third-party scraping.  All data from statsapi /schedule and /boxscore.

Usage:
    from mlb_edge.projected_lineup import project_lineup

    ids = project_lineup(team_id=119, opposing_pitcher_throws="R")
    # ids -> [605141, 660271, 545361, 518692, ...]   (9 batter IDs in order)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"
HTTP_TIMEOUT_SECONDS = 12
CACHE_DIR = Path("data/cache/projected_lineup")
CACHE_TTL_SECONDS = 6 * 3600  # refresh every 6 hours intra-day

PLATOON_BOOST_OPP_HAND = 0.10
PLATOON_BOOST_SWITCH = 0.05


def _http_get(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "mlb_edge/1.0 (+research)"}
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        log.warning("statsapi fetch failed %s: %s", url, e)
        return None


# ----------------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------------
def _cache_path(team_id: int, sp_throws: str, date_iso: str) -> Path:
    return CACHE_DIR / f"team{team_id}_vs{sp_throws}_{date_iso}.json"


def _read_cache(team_id: int, sp_throws: str, date_iso: str) -> Optional[List[int]]:
    p = _cache_path(team_id, sp_throws, date_iso)
    if not p.exists():
        return None
    try:
        import time
        if (time.time() - p.stat().st_mtime) > CACHE_TTL_SECONDS:
            return None
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_cache(team_id: int, sp_throws: str, date_iso: str, ids: List[int]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(team_id, sp_throws, date_iso).write_text(json.dumps(ids))


# ----------------------------------------------------------------------------
# statsapi helpers
# ----------------------------------------------------------------------------
def _team_recent_final_pks(
    team_id: int, lookback_games: int, *, before_date: Optional[str] = None
) -> List[int]:
    """Return the last `lookback_games` Final game pks for `team_id`."""
    end = (
        datetime.fromisoformat(before_date).date()
        if before_date
        else datetime.utcnow().date()
    )
    # Pull a 21-day window to make sure we get N=7 games even with off-days
    start = end - timedelta(days=21)
    qs = urllib.parse.urlencode({
        "sportId": 1,
        "teamId": team_id,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    })
    j = _http_get(f"{STATSAPI_BASE}/schedule?{qs}")
    if not j:
        return []
    pks = []
    for d in j.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") == "Final":
                pks.append((g.get("gameDate", ""), int(g["gamePk"])))
    pks.sort(key=lambda t: t[0])
    return [pk for _, pk in pks[-lookback_games:]]


def _boxscore_lineup(game_pk: int, team_id: int) -> List[Dict]:
    """Return the team's actual starting batting order from this boxscore."""
    j = _http_get(f"{STATSAPI_BASE}/game/{game_pk}/boxscore")
    if not j:
        return []
    for side in ("away", "home"):
        t = (j.get("teams") or {}).get(side) or {}
        if (t.get("team") or {}).get("id") != team_id:
            continue
        order = t.get("battingOrder") or []
        players = t.get("players") or {}
        out = []
        for i, pid in enumerate(order):
            p = players.get(f"ID{pid}") or {}
            person = p.get("person") or {}
            hand = (person.get("batSide") or {}).get("code", "?") or "?"
            pos = (p.get("position") or {}).get("abbreviation", "?") or "?"
            out.append({
                "id": int(pid),
                "name": person.get("fullName", ""),
                "order": i + 1,
                "pos": pos,
                "bats": hand,
            })
        return out
    return []


# ----------------------------------------------------------------------------
# Top-level
# ----------------------------------------------------------------------------
def _get_active_roster_ids(team_id: int) -> set:
    """Return IDs currently on the team's active 26-man roster.  Used to drop
    players who have been moved to the IL since their last start (the boxscore
    projection would otherwise still treat them as starters)."""
    j = _http_get(f"{STATSAPI_BASE}/teams/{team_id}/roster?rosterType=active")
    if not j:
        return set()
    out = set()
    for entry in j.get("roster", []):
        person = entry.get("person", {})
        pid = person.get("id")
        if pid:
            out.add(int(pid))
    return out


def _fetch_handedness(player_ids) -> Dict[int, str]:
    """Batch-fetch each player's batting side ('L', 'R', 'S').  Boxscore data
    sometimes returns an empty batSide which kills our platoon adjustment;
    /people/{ids} is the authoritative source."""
    ids = list({int(p) for p in player_ids if p})
    if not ids:
        return {}
    url = (f"{STATSAPI_BASE}/people?personIds=" + ",".join(str(i) for i in ids))
    j = _http_get(url)
    if not j:
        return {}
    out = {}
    for p in j.get("people", []) or []:
        pid = p.get("id")
        if not pid:
            continue
        code = ((p.get("batSide") or {}).get("code") or "?").upper()
        out[int(pid)] = code
    return out


def _platoon_boost(bat_hand: str, sp_throws: str) -> float:
    if bat_hand == "S":
        return PLATOON_BOOST_SWITCH
    if bat_hand and sp_throws and bat_hand != sp_throws:
        return PLATOON_BOOST_OPP_HAND
    return 0.0


def project_lineup(
    team_id: int,
    opposing_pitcher_throws: str = "R",
    *,
    lookback_games: int = 7,
    exclude_injured: Optional[Set[int]] = None,
    before_date: Optional[str] = None,
    use_cache: bool = True,
    apply_active_roster_filter: bool = True,
    use_authoritative_handedness: bool = True,
) -> List[int]:
    """Return a projected batting order of 9 player IDs for `team_id`.

    The projector uses three signals layered together:
      1. **Recency-weighted start frequency** — how often each player has
         started recently, with the most-recent 3 games counted 2x so day-off
         patterns and platoon-day rotations don't get washed out by stale data.
      2. **Platoon adjustment** — opposite-handed bats and switch-hitters get
         a small boost vs the announced opposing SP.  This requires real
         handedness data, which we fetch from /people in batch (boxscore
         occasionally returns blank batSide).
      3. **Active-roster filter** — players moved to the IL since their last
         start are dropped automatically by checking the team's current active
         26-man roster.

    Args:
        team_id: MLB team ID (statsapi).
        opposing_pitcher_throws: "L" or "R" — biases toward opposite-handed
            and switch-hitters.
        lookback_games: how many recent Final games to inspect.
        exclude_injured: extra player IDs to drop (combined with the active-
            roster filter).
        before_date: ISO date — use games strictly before this date (backtest).
        use_cache: read from / write to data/cache/projected_lineup/.
        apply_active_roster_filter: drop players not on today's 26-man roster.
            Disable for backtesting against historical states.
        use_authoritative_handedness: fetch batSide from /people (slower,
            more accurate) instead of relying on boxscore.

    Returns:
        List of 9 player IDs in projected batting-order.  Returns [] on
        persistent network failure.
    """
    sp_throws = (opposing_pitcher_throws or "R").upper()
    date_key = before_date or datetime.utcnow().strftime("%Y-%m-%d")

    if use_cache:
        cached = _read_cache(team_id, sp_throws, date_key)
        if cached:
            return cached

    pks = _team_recent_final_pks(team_id, lookback_games, before_date=before_date)
    if not pks:
        log.warning("no recent games found for team %s", team_id)
        return []

    # Recency weights: most recent game = 2x, second most = 2x, third = 2x,
    # earlier games = 1x.  Captures manager rotation patterns where the last
    # 2-3 starts are the strongest signal of today's lineup.
    weights = [1.0] * len(pks)
    for i in range(min(3, len(pks))):
        weights[-(i + 1)] = 2.0
    total_weight = sum(weights)

    weighted_starts: Dict[int, float] = defaultdict(float)
    positions: Dict[int, List[int]] = defaultdict(list)
    meta: Dict[int, Dict] = {}
    for game_idx, pk in enumerate(pks):
        w = weights[game_idx]
        for entry in _boxscore_lineup(pk, team_id):
            pid = entry["id"]
            weighted_starts[pid] += w
            positions[pid].append(entry["order"])
            meta[pid] = entry

    # Pull handedness from the authoritative source if requested
    handedness: Dict[int, str] = {}
    if use_authoritative_handedness:
        handedness = _fetch_handedness(meta.keys())

    # Active-roster filter: drop anyone who's been IL'd since their last start
    active_ids: Optional[set] = None
    if apply_active_roster_filter:
        active_ids = _get_active_roster_ids(team_id)
        if not active_ids:
            log.warning("active roster fetch returned empty for team %s; "
                        "skipping IL filter for this run", team_id)
            active_ids = None

    excluded = set(exclude_injured or set())
    scored = []
    for pid, w_starts in weighted_starts.items():
        if pid in excluded:
            continue
        if active_ids is not None and pid not in active_ids:
            log.debug("dropping %s (not on active roster — likely IL)", meta[pid].get("name"))
            continue
        m = meta[pid]
        bats = handedness.get(pid) or m.get("bats", "?") or "?"
        platoon = _platoon_boost(bats, sp_throws)
        score = (w_starts / total_weight) + platoon
        avg_order = sum(positions[pid]) / len(positions[pid])
        scored.append({
            "id": pid,
            "score": score,
            "avg_order": avg_order,
            "name": m.get("name", ""),
            "pos": m.get("pos", ""),
            "bats": bats,
            "starts": int(round(w_starts)),
            "platoon_boost": round(platoon, 3),
        })

    # Take top 9 by score (start-recency + platoon), then resort by avg slot
    scored.sort(key=lambda x: -x["score"])
    top9 = scored[:9]
    top9.sort(key=lambda x: x["avg_order"])
    ids = [p["id"] for p in top9]

    if use_cache and len(ids) == 9:
        _write_cache(team_id, sp_throws, date_key, ids)
    return ids


def project_lineup_with_detail(
    team_id: int, opposing_pitcher_throws: str = "R", *, lookback_games: int = 7,
    exclude_injured: Optional[Set[int]] = None,
) -> List[Dict]:
    """Same heuristic as project_lineup but returns enriched dicts ordered by
    score (debug / display).  Uses the same recency-weighted scoring + active-
    roster filter + authoritative handedness as the production path."""
    sp_throws = (opposing_pitcher_throws or "R").upper()
    pks = _team_recent_final_pks(team_id, lookback_games)
    if not pks:
        return []

    # Recency weights: last 3 games count 2x, earlier games 1x
    weights = [1.0] * len(pks)
    for k in range(min(3, len(pks))):
        weights[-(k + 1)] = 2.0
    total_weight = sum(weights)

    weighted_starts: Dict[int, float] = defaultdict(float)
    positions: Dict[int, List[int]] = defaultdict(list)
    meta: Dict[int, Dict] = {}
    for game_idx, pk in enumerate(pks):
        w = weights[game_idx]
        for e in _boxscore_lineup(pk, team_id):
            pid = e["id"]
            weighted_starts[pid] += w
            positions[pid].append(e["order"])
            meta[pid] = e

    handedness = _fetch_handedness(meta.keys())
    active_ids = _get_active_roster_ids(team_id) or None
    excluded = set(exclude_injured or set())

    scored = []
    for pid, ws in weighted_starts.items():
        if pid in excluded:
            continue
        if active_ids is not None and pid not in active_ids:
            continue
        m = meta[pid]
        bats = handedness.get(pid) or m.get("bats", "?") or "?"
        platoon = _platoon_boost(bats, sp_throws)
        scored.append({
            "id": pid,
            "name": m.get("name", ""),
            "pos": m.get("pos", ""),
            "bats": bats,
            "starts": int(round(ws)),
            "avg_order": round(sum(positions[pid]) / len(positions[pid]), 2),
            "score": round((ws / total_weight) + platoon, 3),
            "platoon": round(platoon, 3),
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:9]


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Heuristic projected lineup")
    p.add_argument("--team", type=int, required=True, help="MLB team ID")
    p.add_argument("--throws", default="R", help="Opposing SP handedness L/R")
    p.add_argument("--lookback", type=int, default=7)
    args = p.parse_args()
    detail = project_lineup_with_detail(args.team, args.throws, lookback_games=args.lookback)
    print(f"=== team {args.team} projected vs {args.throws}HP ===")
    for i, p in enumerate(detail, 1):
        print(f"  {i}. {p['name']:25} ({p['pos']:3}, bats {p['bats']})  "
              f"starts={p['starts']}/{args.lookback}  avg_order={p['avg_order']}  "
              f"score={p['score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
