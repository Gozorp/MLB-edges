#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
player_vectors.py -- player-level bottom-up O/U inputs. DISPLAY/OVERLAY ONLY.

Blueprint 2026-07-18 (user-directed), steps 1+2: exports the daily slate as
an ID-referencing two-object schema -- `players` (compact positional stat
vectors keyed by MLBAM id) and `matchups` (game_pk-keyed lineup/weather
shells) -- plus an EWMA form-decay multiplier per player, replacing hard
rolling windows with a smooth exponential curve.

Vector layout (documented in meta.vector_layout; one deviation from the
blueprint: BOTH platoon sides ship so the frontend can select against the
actual opposing starter at render time):
  [0] xwOBA vs RHP        [1] xwOBA vs LHP
  [2] xSLG  vs RHP        [3] xSLG  vs LHP
  [4] 7d-EWMA form multiplier (latest ewm(span=7) game xwOBA / season mean)
  [5] walk rate (BB / PA)
  [6] season PA  (for the frontend's Bayesian small-sample shrink)

Sources: the SAME cached YTD Statcast frame predict uses (no new fetches;
PA-outcome rows only) + one statsapi schedule call for lineups/weather.
Writes docs/data/player_vectors_<date>.json (atomic).

Usage: python tools/player_vectors.py [YYYY-MM-DD]
Sandboxed: any failure prints a warning and writes nothing.
"""
import datetime
import json
import os
import sys
import urllib.request

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-player-vectors/1.0"}
MIN_PA = 10          # below this a player ships no vector (frontend shrinks to league)
EWMA_SPAN = 7


def _schedule(slate):
    url = ("%s/schedule?sportId=1&date=%s&hydrate=team,probablePitcher,lineups,weather,venue"
           % (API, slate))
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
    out = []
    for d in j.get("dates", []):
        for g in sorted(d.get("games", []), key=lambda x: x.get("gameNumber") or 1):
            t = g.get("teams", {})
            lu = g.get("lineups") or {}
            wx = g.get("weather") or {}
            wind = (wx.get("wind") or "")          # e.g. "15 mph, Out To CF"
            speed, wdir = None, None
            if "," in wind:
                spd, _, wdir = wind.partition(",")
                try: speed = int(spd.strip().split()[0])
                except (ValueError, IndexError): pass
                wdir = wdir.strip()
            out.append({
                "game_pk": g.get("gamePk"),
                "game_num": g.get("gameNumber") or 1,
                "away_team": ((t.get("away") or {}).get("team") or {}).get("abbreviation"),
                "home_team": ((t.get("home") or {}).get("team") or {}).get("abbreviation"),
                "away_sp": ((t.get("away") or {}).get("probablePitcher") or {}).get("fullName"),
                "home_sp": ((t.get("home") or {}).get("probablePitcher") or {}).get("fullName"),
                "away_lineup": [str(p.get("id")) for p in (lu.get("awayPlayers") or [])],
                "home_lineup": [str(p.get("id")) for p in (lu.get("homePlayers") or [])],
                "venue": (g.get("venue") or {}).get("name"),
                "weather": {"temp": (int(wx["temp"]) if str(wx.get("temp", "")).isdigit() else None),
                            "condition": wx.get("condition"),
                            "wind_speed": speed, "wind_dir": wdir},
            })
    return out


def _player_vectors():
    import pandas as pd
    from mlb_edge import data_ingestion as di
    day = datetime.date.today() - datetime.timedelta(days=1)
    sc = di.fetch_ytd_statcast(day)
    ev = sc[sc["events"].notna()].copy()          # one row per completed PA
    ev["game_date"] = pd.to_datetime(ev["game_date"])   # cache mixes str/Timestamp
    ev["xw"] = ev["estimated_woba_using_speedangle"].fillna(ev["woba_value"])
    ev["xs"] = ev["estimated_slg_using_speedangle"]

    players = {}
    for bid, g in ev.groupby("batter"):
        pa = len(g)
        if pa < MIN_PA:
            continue
        vs = {}
        for hand in ("R", "L"):
            gh = g[g["p_throws"] == hand]
            vs[hand] = (round(float(gh["xw"].mean()), 3) if len(gh) >= 5 else None,
                        round(float(gh["xs"].mean()), 3) if gh["xs"].notna().sum() >= 5 else None)
        # EWMA form decay (blueprint step 2): smooth per-game xwOBA curve,
        # latest ewm value over the season mean = today's form multiplier.
        by_game = (g.sort_values("game_date").groupby("game_date")["xw"].mean())
        season_mean = float(by_game.mean()) if len(by_game) else 0.0
        if len(by_game) >= 3 and season_mean > 0:
            decay = float(by_game.ewm(span=EWMA_SPAN, adjust=False).mean().iloc[-1]) / season_mean
            decay = round(max(0.5, min(1.5, decay)), 3)   # clamp: display sanity
        else:
            decay = 1.0
        bb_rate = round(float((g["events"] == "walk").mean()), 3)
        players[str(int(bid))] = [vs["R"][0], vs["L"][0], vs["R"][1], vs["L"][1],
                                  decay, bb_rate, pa]
    return players


def main():
    slate = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    try:
        matchups = _schedule(slate)
    except Exception as e:
        print("[player-vectors] schedule fetch failed: %s; skip" % e)
        return
    if not matchups:
        print("[player-vectors] no games for %s; skip" % slate)
        return
    try:
        players = _player_vectors()
    except Exception as e:
        print("[player-vectors] statcast vector build failed: %r; skip" % (e,))
        return
    n_lineups = sum(1 for m in matchups if m["away_lineup"] or m["home_lineup"])
    out = {
        "meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date": slate,
            "pipeline_status": "GREEN",
            "vector_layout": ["xwoba_vs_R", "xwoba_vs_L", "xslg_vs_R", "xslg_vs_L",
                              "ewma_decay_mult", "bb_rate", "season_pa"],
            "ewma_span": EWMA_SPAN, "min_pa": MIN_PA,
            "league_xwoba": 0.315,
        },
        "players": players,
        "matchups": matchups,
    }
    outp = os.path.join(ROOT, "docs", "data", "player_vectors_%s.json" % slate)
    tmp = outp + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))     # compact: no pretty-print bloat
    os.replace(tmp, outp)
    kb = os.path.getsize(outp) / 1024.0
    print("[player-vectors] %d players / %d matchups (%d with posted lineups) -> %s (%.0f KB)"
          % (len(players), len(matchups), n_lineups, outp, kb))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[player-vectors] WARN unexpected failure %r -- nothing written" % (e,))
