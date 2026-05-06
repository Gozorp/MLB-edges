"""
batter_vs_pitcher.py
====================
Fetches career batter-vs-pitcher splits from MLB statsapi and aggregates them
into team-level features for the daily slate.

The hypothesis: lineup-level career performance against today's opposing
starter is signal that doesn't show up in season-aggregated batting and
pitching numbers.  Some hitters truly own certain pitchers; others can't read
their stuff.  When most of the lineup has a .800+ career OPS against an SP,
that's an edge the model's F2 (xwOBA gap) and F3 (swing-take gap) layers
should be sharpening on.

Output features per side (suffixed `_away` and `_home` in the per-game row):
  bvp_n_pa            total career PAs in the lineup vs the opposing starter
  bvp_ops_shrunk      PA-weighted average OPS, Bayesian-shrunk to season
  bvp_avg_shrunk      PA-weighted average AVG, shrunk to season
  bvp_k_pct           K rate (career, lineup-summed)
  bvp_bb_pct          BB rate (career, lineup-summed)
  bvp_hr_per_pa       HR rate (career, lineup-summed)
  bvp_strong_owners   N batters with PA>=10 AND raw OPS>0.900 vs this SP
  bvp_weak_vs         N batters with PA>=10 AND raw OPS<0.500 vs this SP
  bvp_signal_strength bvp_n_pa / 9 — proxy for how reliable the lineup-level
                      signal is (1 PA per lineup spot is noise; 10 PA per spot
                      is meaningful)

Per-game derivative:
  bvp_home_edge_ops   home_lineup_bvp_ops - away_lineup_bvp_ops
                      Positive = home offense has historically out-hit the
                      visiting SP relative to how the visiting offense has
                      hit the home SP.  Direct alpha on the model's pick.

Data source: MLB statsapi `/api/v1/people/{batterId}/stats?stats=vsPlayer`.
No third-party scraping; this is the official feed.

Cache: each (batter_id, pitcher_id) pair is cached at
data/cache/bvp/{batter}_vs_{pitcher}.json with a 24-hour TTL.  BVP data is
career-static within a season except when the two players actually face off,
so a daily refresh is plenty.

Usage:
    from mlb_edge.batter_vs_pitcher import build_bvp_features

    feats = build_bvp_features(
        away_lineup_ids=[605141, 660271, 545361, ...],
        home_sp_id=621121,
        home_lineup_ids=[642715, 668939, ...],
        away_sp_id=607208,
        season_ops_lookup={605141: 0.832, 660271: 1.057, ...},  # optional
    )
    # feats == {"bvp_n_pa_away": 87, "bvp_ops_shrunk_away": 0.742, ...}

CLI:
    python -m mlb_edge.batter_vs_pitcher --date 2026-05-06
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"
CACHE_DIR = Path("data/cache/bvp")
CACHE_TTL_SECONDS = 24 * 3600     # career data, daily refresh is plenty
HTTP_TIMEOUT_SECONDS = 12

# Bayesian shrinkage prior strength.  At 50 "ghost PAs" of the season-average
# OPS, a raw 3-PA sample carries 3/(3+50) ≈ 6% of the weight; a 50-PA sample
# carries half.  Tuned so very small BVP samples don't dominate features.
SHRINKAGE_PRIOR_PA = 50

# League-average OPS used as ultimate fallback when we can't get a season prior
LEAGUE_AVG_OPS = 0.730
LEAGUE_AVG_AVG = 0.245
LEAGUE_AVG_K_PCT = 0.226
LEAGUE_AVG_BB_PCT = 0.085
LEAGUE_AVG_HR_PER_PA = 0.030


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------
@dataclass
class BvpStat:
    """Career batter-vs-pitcher line."""
    batter_id: int
    pitcher_id: int
    pa: int
    ab: int
    h: int
    hr: int
    bb: int
    k: int
    avg: float
    obp: float
    slg: float
    ops: float

    @property
    def k_pct(self) -> float:
        return self.k / self.pa if self.pa > 0 else 0.0

    @property
    def bb_pct(self) -> float:
        return self.bb / self.pa if self.pa > 0 else 0.0

    @property
    def hr_per_pa(self) -> float:
        return self.hr / self.pa if self.pa > 0 else 0.0


# ----------------------------------------------------------------------------
# Cache helpers
# ----------------------------------------------------------------------------
def _cache_path(batter_id: int, pitcher_id: int) -> Path:
    return CACHE_DIR / f"{batter_id}_vs_{pitcher_id}.json"


def _read_cache(batter_id: int, pitcher_id: int) -> Optional[BvpStat]:
    p = _cache_path(batter_id, pitcher_id)
    if not p.exists():
        return None
    try:
        if (time.time() - p.stat().st_mtime) > CACHE_TTL_SECONDS:
            return None
        return BvpStat(**json.loads(p.read_text()))
    except Exception as e:                    # corrupt cache
        log.debug("cache read failed %s: %s", p, e)
        return None


def _write_cache(stat: BvpStat) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(stat.batter_id, stat.pitcher_id).write_text(
        json.dumps(asdict(stat), indent=2)
    )


# ----------------------------------------------------------------------------
# HTTP fetch
# ----------------------------------------------------------------------------
def _http_get(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "mlb_edge/1.0 (+research)"}
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        log.warning("statsapi fetch failed %s: %s", url, e)
        return None
    except Exception as e:
        log.warning("statsapi fetch error %s: %s", url, e)
        return None


def _fetch_bvp_total(batter_id: int, pitcher_id: int) -> Optional[BvpStat]:
    """Pull career batter-vs-pitcher line from MLB statsapi."""
    qs = urllib.parse.urlencode({
        "stats": "vsPlayer",
        "group": "hitting",
        "opposingPlayerId": pitcher_id,
        "sportId": 1,
    })
    url = f"{STATSAPI_BASE}/people/{batter_id}/stats?{qs}"
    j = _http_get(url)
    if not j:
        return None
    # The response has multiple splits — we want `vsPlayerTotal`, the career row
    for sg in j.get("stats", []):
        if (sg.get("type") or {}).get("displayName") != "vsPlayerTotal":
            continue
        splits = sg.get("splits") or []
        if not splits:
            continue
        st = splits[0].get("stat") or {}
        try:
            return BvpStat(
                batter_id=batter_id,
                pitcher_id=pitcher_id,
                pa=int(st.get("plateAppearances", 0) or 0),
                ab=int(st.get("atBats", 0) or 0),
                h=int(st.get("hits", 0) or 0),
                hr=int(st.get("homeRuns", 0) or 0),
                bb=int(st.get("baseOnBalls", 0) or 0),
                k=int(st.get("strikeOuts", 0) or 0),
                avg=float(st.get("avg") or 0.0),
                obp=float(st.get("obp") or 0.0),
                slg=float(st.get("slg") or 0.0),
                ops=float(st.get("ops") or 0.0),
            )
        except (TypeError, ValueError) as e:
            log.warning("BVP parse error for %s vs %s: %s", batter_id, pitcher_id, e)
            return None
    # No vsPlayerTotal split — usually means they've never faced
    return BvpStat(
        batter_id=batter_id, pitcher_id=pitcher_id,
        pa=0, ab=0, h=0, hr=0, bb=0, k=0,
        avg=0.0, obp=0.0, slg=0.0, ops=0.0,
    )


def fetch_bvp(batter_id: int, pitcher_id: int) -> Optional[BvpStat]:
    """Cached BVP fetch.  Returns None only on persistent network failure."""
    cached = _read_cache(batter_id, pitcher_id)
    if cached is not None:
        return cached
    fresh = _fetch_bvp_total(batter_id, pitcher_id)
    if fresh is not None:
        _write_cache(fresh)
    return fresh


# ----------------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------------
def _shrunk_ops(raw_ops: float, pa: int, prior_ops: float) -> float:
    """Bayesian shrinkage toward a prior (the batter's season OPS or league avg).

    Weight: PA / (PA + SHRINKAGE_PRIOR_PA).  At 0 PA, output is the prior.
    At 50 PA, output is 50% raw + 50% prior.  At 200 PA, output is 80% raw.
    """
    if pa <= 0:
        return prior_ops
    w_raw = pa / (pa + SHRINKAGE_PRIOR_PA)
    return w_raw * raw_ops + (1 - w_raw) * prior_ops


def _aggregate_lineup_vs_sp(
    lineup_ids: Iterable[int],
    sp_id: int,
    season_ops_lookup: Optional[Dict[int, float]] = None,
) -> Dict[str, float]:
    """For one side: pull each batter's career line vs the opposing SP, return
    aggregate features.  `season_ops_lookup` maps batter_id → season OPS for
    shrinkage; missing batters fall back to league average."""
    stats: List[BvpStat] = []
    for bid in lineup_ids:
        s = fetch_bvp(int(bid), int(sp_id))
        if s is not None:
            stats.append(s)

    total_pa = sum(s.pa for s in stats)

    # PA-weighted shrunk OPS across the lineup
    if stats:
        shrunk_terms = []
        for s in stats:
            prior = LEAGUE_AVG_OPS
            if season_ops_lookup and s.batter_id in season_ops_lookup:
                prior = float(season_ops_lookup[s.batter_id])
            shrunk_terms.append(_shrunk_ops(s.ops, s.pa, prior))
        # Average over lineup spots (not PA-weighted, since each spot bats
        # ~equally often during the game we're modeling)
        bvp_ops_shrunk = sum(shrunk_terms) / len(shrunk_terms)
    else:
        bvp_ops_shrunk = LEAGUE_AVG_OPS

    # Lineup-summed rate stats from career raw numbers
    if total_pa > 0:
        bvp_avg_shrunk = sum(s.h for s in stats) / max(sum(s.ab for s in stats), 1)
        bvp_k_pct = sum(s.k for s in stats) / total_pa
        bvp_bb_pct = sum(s.bb for s in stats) / total_pa
        bvp_hr_per_pa = sum(s.hr for s in stats) / total_pa
    else:
        bvp_avg_shrunk = LEAGUE_AVG_AVG
        bvp_k_pct = LEAGUE_AVG_K_PCT
        bvp_bb_pct = LEAGUE_AVG_BB_PCT
        bvp_hr_per_pa = LEAGUE_AVG_HR_PER_PA

    strong_owners = sum(1 for s in stats if s.pa >= 10 and s.ops > 0.900)
    weak_vs       = sum(1 for s in stats if s.pa >= 10 and s.ops < 0.500)

    return {
        "bvp_n_pa": float(total_pa),
        "bvp_ops_shrunk": round(bvp_ops_shrunk, 4),
        "bvp_avg_shrunk": round(bvp_avg_shrunk, 4),
        "bvp_k_pct": round(bvp_k_pct, 4),
        "bvp_bb_pct": round(bvp_bb_pct, 4),
        "bvp_hr_per_pa": round(bvp_hr_per_pa, 4),
        "bvp_strong_owners": float(strong_owners),
        "bvp_weak_vs": float(weak_vs),
        "bvp_signal_strength": round(total_pa / 9.0, 3),
    }


def build_bvp_features(
    *,
    away_lineup_ids: Iterable[int],
    home_sp_id: Optional[int],
    home_lineup_ids: Iterable[int],
    away_sp_id: Optional[int],
    season_ops_lookup: Optional[Dict[int, float]] = None,
) -> Dict[str, float]:
    """Top-level feature builder.  Returns a flat feature dict keyed with
    `_away` / `_home` suffixes plus the cross-side derivative."""
    feats: Dict[str, float] = {}

    if home_sp_id:
        away_vs_home_sp = _aggregate_lineup_vs_sp(
            away_lineup_ids, int(home_sp_id), season_ops_lookup
        )
        for k, v in away_vs_home_sp.items():
            feats[f"{k}_away"] = v
    else:
        for k in ("bvp_n_pa", "bvp_ops_shrunk", "bvp_avg_shrunk",
                  "bvp_k_pct", "bvp_bb_pct", "bvp_hr_per_pa",
                  "bvp_strong_owners", "bvp_weak_vs", "bvp_signal_strength"):
            feats[f"{k}_away"] = 0.0

    if away_sp_id:
        home_vs_away_sp = _aggregate_lineup_vs_sp(
            home_lineup_ids, int(away_sp_id), season_ops_lookup
        )
        for k, v in home_vs_away_sp.items():
            feats[f"{k}_home"] = v
    else:
        for k in ("bvp_n_pa", "bvp_ops_shrunk", "bvp_avg_shrunk",
                  "bvp_k_pct", "bvp_bb_pct", "bvp_hr_per_pa",
                  "bvp_strong_owners", "bvp_weak_vs", "bvp_signal_strength"):
            feats[f"{k}_home"] = 0.0

    # Cross-side: positive = home lineup has historically out-hit the visiting
    # SP, relative to the visiting lineup vs the home SP.  Direct alpha on a
    # home pick when this is positive, on a visiting pick when negative.
    feats["bvp_home_edge_ops"] = round(
        feats["bvp_ops_shrunk_home"] - feats["bvp_ops_shrunk_away"], 4
    )
    feats["bvp_home_edge_k"]   = round(
        feats["bvp_k_pct_away"] - feats["bvp_k_pct_home"], 4
    )

    return feats


# ----------------------------------------------------------------------------
# CLI / smoke test — given a date, print BVP features for every game
# ----------------------------------------------------------------------------
def _fetch_slate(date: str) -> List[dict]:
    """Pull the day's games with probable pitchers + posted lineups."""
    qs = urllib.parse.urlencode({
        "sportId": 1,
        "date": date,
        "hydrate": "probablePitcher,lineups,team",
    })
    j = _http_get(f"{STATSAPI_BASE}/schedule?{qs}")
    if not j:
        return []
    games = []
    for d in j.get("dates", []):
        for g in d.get("games", []):
            games.append(g)
    return games


def _extract_lineup_ids(team_block: dict) -> List[int]:
    """Pull starting lineup IDs from the schedule's lineups hydrate.  Returns
    empty list if the lineup card hasn't been posted yet."""
    lineups = (team_block or {}).get("lineups") or {}
    # Posted lineup arrives as `awayPlayers` / `homePlayers` lists in older
    # schemas, or as `battingOrder` ID list in newer ones.
    for key in ("battingOrder", "players"):
        ids = lineups.get(key)
        if ids:
            return [int(p["id"]) if isinstance(p, dict) else int(p) for p in ids]
    return []


def _fetch_pitcher_throws(pitcher_id: int) -> str:
    """Quick lookup: returns 'L' or 'R' for the pitcher's throwing hand."""
    if not pitcher_id:
        return "R"
    j = _http_get(f"{STATSAPI_BASE}/people/{pitcher_id}")
    if not j:
        return "R"
    person = (j.get("people") or [{}])[0]
    return ((person.get("pitchHand") or {}).get("code") or "R").upper()


def _resolve_lineup(
    team_id: int, sched_team_block: dict, sched_top_lineups: dict,
    side: str, opposing_sp_id: Optional[int],
) -> Tuple[List[int], str]:
    """Get the team's batting-order lineup for this game.

    Tries posted lineups first (statsapi `hydrate=lineups`), then falls back
    to the heuristic projected lineup from `projected_lineup.project_lineup`.

    Returns (lineup_ids, source) where source is 'posted' or 'projected'.
    """
    # Posted: top-level lineups.{away,home}Players first
    posted_key = f"{side}Players"
    posted = sched_top_lineups.get(posted_key)
    if posted:
        return [int(p["id"]) for p in posted], "posted"
    # Posted: per-team battingOrder
    bo = (sched_team_block or {}).get("battingOrder") or []
    if bo:
        return [int(p) for p in bo], "posted"
    # Heuristic fallback
    try:
        from mlb_edge.projected_lineup import project_lineup
        sp_throws = _fetch_pitcher_throws(opposing_sp_id) if opposing_sp_id else "R"
        proj = project_lineup(team_id, sp_throws)
        if proj:
            return proj, "projected"
    except Exception as e:
        log.warning("projected_lineup fallback failed for team %s: %s", team_id, e)
    return [], "none"


def cli(date: str, *, verbose: bool = False) -> None:
    """Print per-game BVP feature rows for every game on the slate."""
    games = _fetch_slate(date)
    if not games:
        print(f"no games found for {date}")
        return

    print(f"=== BVP features for {date} ({len(games)} games) ===")
    for g in games:
        a = g["teams"]["away"]; h = g["teams"]["home"]
        a_abbr = a["team"].get("abbreviation") or a["team"]["name"][:3]
        h_abbr = h["team"].get("abbreviation") or h["team"]["name"][:3]
        a_id = a["team"]["id"]; h_id = h["team"]["id"]
        a_sp = (a.get("probablePitcher") or {}).get("id")
        h_sp = (h.get("probablePitcher") or {}).get("id")
        a_sp_name = (a.get("probablePitcher") or {}).get("fullName", "TBD")
        h_sp_name = (h.get("probablePitcher") or {}).get("fullName", "TBD")

        sched_lineups = g.get("lineups") or {}
        a_lineup, a_src = _resolve_lineup(a_id, a, sched_lineups, "away", h_sp)
        h_lineup, h_src = _resolve_lineup(h_id, h, sched_lineups, "home", a_sp)

        print(f"\n{a_abbr} ({a_sp_name}) @ {h_abbr} ({h_sp_name})")
        print(f"  lineup sources: away={a_src} ({len(a_lineup)}), home={h_src} ({len(h_lineup)})")
        if not (a_sp and h_sp):
            print("  (TBD pitchers — skipping BVP)")
            continue
        if not a_lineup and not h_lineup:
            print("  (no lineup data resolvable — skipping)")
            continue

        feats = build_bvp_features(
            away_lineup_ids=a_lineup,
            home_sp_id=h_sp,
            home_lineup_ids=h_lineup,
            away_sp_id=a_sp,
        )
        # Print headline numbers
        print(f"  away lineup vs {h_sp_name}: "
              f"OPS={feats['bvp_ops_shrunk_away']:.3f} "
              f"({int(feats['bvp_n_pa_away'])} PA, "
              f"strong={int(feats['bvp_strong_owners_away'])}, "
              f"weak={int(feats['bvp_weak_vs_away'])})")
        print(f"  home lineup vs {a_sp_name}: "
              f"OPS={feats['bvp_ops_shrunk_home']:.3f} "
              f"({int(feats['bvp_n_pa_home'])} PA, "
              f"strong={int(feats['bvp_strong_owners_home'])}, "
              f"weak={int(feats['bvp_weak_vs_home'])})")
        print(f"  bvp_home_edge_ops = {feats['bvp_home_edge_ops']:+.3f}  "
              f"(positive favors home, negative favors away)")
        print(f"  bvp_home_edge_k   = {feats['bvp_home_edge_k']:+.3f}  "
              f"(positive favors home — opposing lineup K's more)")
        if verbose:
            for k, v in sorted(feats.items()):
                print(f"    {k} = {v}")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="BVP smoke test / feature dump")
    p.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    cli(args.date, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
