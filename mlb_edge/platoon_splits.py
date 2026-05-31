"""
platoon_splits.py — top-5 batter platoon context for the Claude Brain layer.

This module is the joiner half of the platoon-brain MVP.  Given a slate's
diag_df (one row per game) and lineup info, it fetches each team's actual
top-5 batters and their career splits vs LHP/RHP from MLB statsapi, then
attaches two JSON-string columns to the diag CSV:

    away_top_5_batters_json
    home_top_5_batters_json

Each JSON value is a list of up to 5 batter records with the shape:

    {
      "order": 1,
      "name": "Aaron Judge",
      "bat_side": "R",
      "season_xwOBA_proxy": null,   # placeholder — Savant integration follow-up
      "recent_trend_delta": null,   # placeholder — last-14d feature follow-up
      "vs_LHP_OPS_career": 1.062,
      "vs_LHP_PA_career": 1299,
      "vs_RHP_OPS_career": 1.017,
      "vs_RHP_PA_career": 3897,
      "vs_today_SP_OPS": 1.017,     # pre-resolved against today's opposing SP
      "vs_today_SP_PA": 3897,       # for switch-hitters, resolved to the side they'll bat from
      "sample_flag": "OK"           # OK | LOW_SAMPLE | NO_DATA
    }

The "vs_today_SP_*" fields save the LLM from doing the handedness lookup;
it just reads the relevant number directly.

Design notes:
- Single MLB statsapi endpoint per player: /people/{id}/stats?stats=careerStatSplits&group=hitting&sitCodes=vl,vr
- Weekly cache in data/platoon_cache/<player_id>.json — splits don't change
  day-to-day, so a TTL of 7 days is plenty and saves ~150 API calls per slate
- Best-effort: per-row try/except so one player fetch failure doesn't kill
  the whole slate.  Failed rows get sample_flag=NO_DATA, JSON still valid.
- No xgboost-side feature creation — this is LLM-context only.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/platoon_cache")
CACHE_TTL_SEC = 7 * 24 * 60 * 60  # 1 week

MIN_USEFUL_PA = 100   # below this -> LOW_SAMPLE flag (LLM should discount)
STABLE_PA = 150       # above this -> OK


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "mlb_edge_platoon_splits/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _cache_path(player_id: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{player_id}.json"


def _read_cache(player_id: int) -> Optional[dict]:
    p = _cache_path(player_id)
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > CACHE_TTL_SEC:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(player_id: int, data: dict) -> None:
    try:
        _cache_path(player_id).write_text(
            json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.debug("[platoon_splits] cache write failed for %s: %s",
                  player_id, e)


# ---------------------------------------------------------------------------
# Splits fetch
# ---------------------------------------------------------------------------
def get_career_splits(player_id: int) -> dict:
    """Return {vs_LHP: {OPS, PA, AVG}, vs_RHP: {OPS, PA, AVG}, bat_side}."""
    cached = _read_cache(player_id)
    if cached is not None and "season_PA" in cached:
        return cached

    url = (f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
           f"?stats=careerStatSplits&group=hitting&sitCodes=vl,vr")
    out = {"vs_LHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},
           "vs_RHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},
           "bat_side": None}
    try:
        data = _fetch_json(url)
        for s in data.get("stats", []):
            for split in s.get("splits", []):
                code = (split.get("split") or {}).get("code", "")
                stat = split.get("stat", {})
                try:
                    pa = int(stat.get("plateAppearances", 0) or 0)
                except (TypeError, ValueError):
                    pa = 0
                try:
                    ops = float(stat.get("ops") or 0)
                except (TypeError, ValueError):
                    ops = 0.0
                try:
                    avg = float(stat.get("avg") or 0)
                except (TypeError, ValueError):
                    avg = 0.0
                entry = {"OPS": ops, "PA": pa, "AVG": avg}
                if code == "vl":
                    out["vs_LHP"] = entry
                elif code == "vr":
                    out["vs_RHP"] = entry
    except Exception as e:
        log.debug("[platoon_splits] fetch failed for %s: %s", player_id, e)

    # Bat side — separate endpoint
    try:
        person = _fetch_json(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}")
        people = person.get("people") or []
        if people:
            bs = (people[0].get("batSide") or {}).get("code")
            out["bat_side"] = bs
    except Exception:
        pass

    # Season HR + PA (Phase 1.5 HR-prop ranking). Cached alongside the
    # splits; the cache-version check above (requires "season_PA")
    # refreshes pre-existing entries that lack it.
    out["season_HR"] = 0
    out["season_PA"] = 0
    try:
        sdata = _fetch_json(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}"
            f"/stats?stats=season&group=hitting")
        for s in sdata.get("stats", []):
            for split in s.get("splits", []):
                st = split.get("stat", {})
                try:
                    out["season_HR"] = int(st.get("homeRuns", 0) or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    out["season_PA"] = int(st.get("plateAppearances", 0) or 0)
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        log.debug("[platoon_splits] season HR/PA fetch failed for %s: %s",
                  player_id, e)

    _write_cache(player_id, out)
    return out


def _sample_flag(pa: int) -> str:
    if pa < MIN_USEFUL_PA:
        return "LOW_SAMPLE"
    if pa < STABLE_PA:
        return "SUB_STABLE"
    return "OK"


def _resolve_vs_today_SP(splits: dict, sp_handedness: str
                          ) -> Tuple[float, int]:
    """Return (OPS, PA) the batter will face today, accounting for switch-hitters.

    `sp_handedness` is "L" or "R" — the opposing SP's throwing hand.
    Switch hitters (bat_side="S") bat from the OPPOSITE side of the SP, so
    they face the SP from their preferred-side and get that side's splits.
    Right-side batters face same-handed pitchers as a *disadvantage*.

    For LLM consumption we just return what the batter is realistically
    looking at — the number that determines whether this matchup is hard
    or easy for them.
    """
    # Switch hitters bat from the opposite side of the SP
    bs = (splits.get("bat_side") or "").upper()
    if bs == "S":
        effective_side = "R" if sp_handedness == "L" else "L"
    else:
        effective_side = bs or "R"

    # An L-batter vs an L-pitcher faces the same-handed pitcher, so they
    # look up "vs_LHP" splits.
    if sp_handedness == "L":
        key = "vs_LHP"
    else:
        key = "vs_RHP"

    entry = splits.get(key, {})
    return entry.get("OPS", 0.0), entry.get("PA", 0)


# ---------------------------------------------------------------------------
# Lineup retrieval
# ---------------------------------------------------------------------------
def get_top_n_lineup(game_pk: int, team_side: str, n: int = 5
                      ) -> List[Tuple[int, str, int]]:
    """Pull the top-n batters from the actual batted lineup (boxscore).

    Returns list of (batting_order_position, name, player_id).
    For backtest data this is the lineup that ACTUALLY hit; for live games
    that haven't started, this returns whatever boxscore reports (may be
    empty pre-game; caller should fall back to projected lineup).
    """
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        data = _fetch_json(url)
    except Exception as e:
        log.debug("[platoon_splits] boxscore fetch failed for %s: %s",
                  game_pk, e)
        return []
    team = data.get("teams", {}).get(team_side, {})
    rows: List[Tuple[int, str, int]] = []
    seen = set()
    for pid, info in team.get("players", {}).items():
        order = info.get("battingOrder")
        if not order:
            continue
        try:
            pos = int(order) // 100
        except (ValueError, TypeError):
            continue
        if 1 <= pos <= n and pos not in seen:
            rows.append((pos, info["person"]["fullName"],
                         info["person"]["id"]))
            seen.add(pos)
    rows.sort()
    return rows


# ---------------------------------------------------------------------------
# Top-level: build JSON payload for one team in one game
# ---------------------------------------------------------------------------
def build_team_top_5_payload(game_pk: int, team_side: str,
                               opposing_sp_handedness: Optional[str]
                               ) -> List[dict]:
    """Return list[dict] suitable for json.dumps as a JSON-string CSV column.

    `opposing_sp_handedness` is "L" or "R" — used to pre-resolve the
    "vs_today_SP_*" fields so the LLM doesn't have to do the lookup.
    Pass None if SP handedness unknown (the field stays null).
    """
    lineup = get_top_n_lineup(game_pk, team_side, n=5)
    out: List[dict] = []
    for pos, name, pid in lineup:
        try:
            splits = get_career_splits(pid)
        except Exception as e:
            log.debug("[platoon_splits] splits err for %s (%s): %s",
                      name, pid, e)
            out.append({
                "order": pos, "name": name, "bat_side": None,
                "vs_LHP_OPS_career": None, "vs_LHP_PA_career": 0,
                "vs_RHP_OPS_career": None, "vs_RHP_PA_career": 0,
                "vs_today_SP_OPS": None, "vs_today_SP_PA": 0,
                "season_HR": 0, "season_PA": 0,
                "sample_flag": "NO_DATA",
            })
            continue

        vs_l = splits["vs_LHP"]
        vs_r = splits["vs_RHP"]
        pa_today = 0
        ops_today = None
        if opposing_sp_handedness in ("L", "R"):
            ops_today, pa_today = _resolve_vs_today_SP(
                splits, opposing_sp_handedness)

        # sample_flag combines both sides — if either is LOW, flag LOW_SAMPLE
        flag_l = _sample_flag(vs_l["PA"])
        flag_r = _sample_flag(vs_r["PA"])
        flag = ("LOW_SAMPLE" if "LOW_SAMPLE" in (flag_l, flag_r)
                else "SUB_STABLE" if "SUB_STABLE" in (flag_l, flag_r)
                else "OK")

        out.append({
            "order": pos,
            "name": name,
            "bat_side": splits.get("bat_side"),
            "vs_LHP_OPS_career": round(vs_l["OPS"], 3),
            "vs_LHP_PA_career": vs_l["PA"],
            "vs_RHP_OPS_career": round(vs_r["OPS"], 3),
            "vs_RHP_PA_career": vs_r["PA"],
            "vs_today_SP_OPS": round(ops_today, 3) if ops_today else None,
            "vs_today_SP_PA": pa_today,
            "season_HR": int(splits.get("season_HR", 0) or 0),
            "season_PA": int(splits.get("season_PA", 0) or 0),
            "sample_flag": flag,
        })
    return out


def attach_top_5_to_diag(diag_df, matchup_to_game_pk: Dict[str, int],
                          matchup_to_sp_handedness:
                              Optional[Dict[str, Dict[str, str]]] = None):
    """Add `away_top_5_batters_json` and `home_top_5_batters_json` columns
    to a diag DataFrame.  Each column is a JSON-encoded string.

    `matchup_to_game_pk` maps "AWAY @ HOME" -> game_pk.
    `matchup_to_sp_handedness` maps "AWAY @ HOME" -> {"away_sp_hand": "R",
    "home_sp_hand": "L"}.  When None or missing for a matchup, the
    vs_today_SP_* fields stay null in the payload.

    Returns the modified diag_df (in-place modification too).
    """
    if "matchup" not in diag_df.columns:
        log.warning("[platoon_splits] diag_df missing matchup column")
        return diag_df

    if matchup_to_sp_handedness is None:
        matchup_to_sp_handedness = {}

    away_payloads: List[str] = []
    home_payloads: List[str] = []
    for _, row in diag_df.iterrows():
        matchup = str(row.get("matchup", "")).strip()
        game_pk = matchup_to_game_pk.get(matchup)
        sp = matchup_to_sp_handedness.get(matchup, {})
        if not game_pk:
            away_payloads.append("[]")
            home_payloads.append("[]")
            continue
        try:
            # AWAY team faces the HOME SP, so vs_today_SP uses home_sp_hand
            away = build_team_top_5_payload(
                game_pk, "away", sp.get("home_sp_hand"))
            # HOME team faces the AWAY SP
            home = build_team_top_5_payload(
                game_pk, "home", sp.get("away_sp_hand"))
        except Exception as e:
            log.warning("[platoon_splits] payload build failed for %s: %s",
                        matchup, e)
            away, home = [], []
        away_payloads.append(json.dumps(away, separators=(",", ":")))
        home_payloads.append(json.dumps(home, separators=(",", ":")))

    diag_df["away_top_5_batters_json"] = away_payloads
    diag_df["home_top_5_batters_json"] = home_payloads
    return diag_df


# ---------------------------------------------------------------------------
# CLI for ad-hoc testing
# ---------------------------------------------------------------------------
def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="platoon_splits debug CLI")
    ap.add_argument("--player", type=int, help="MLB player_id to query")
    ap.add_argument("--game-pk", type=int, help="game_pk to fetch lineup")
    ap.add_argument("--side", choices=("home", "away"), default="away")
    ap.add_argument("--vs", choices=("L", "R"), default=None,
                     help="opposing SP handedness for vs_today_SP_* fields")
    args = ap.parse_args()

    if args.player:
        print(json.dumps(get_career_splits(args.player), indent=2))
    elif args.game_pk:
        payload = build_team_top_5_payload(args.game_pk, args.side, args.vs)
        print(json.dumps(payload, indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    _cli()
