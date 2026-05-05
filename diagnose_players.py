"""
diagnose_players.py
-------------------
For specific players the user named, measure whether their presence moves
team outcomes. Uses MLB Stats API (statsapi.mlb.com) since it gives us
per-game winner + the starting pitcher + full lineup / appearances without
needing a third-party data provider.

(1) Zack Littell (RHP, TB Rays 2023-25): did his team win or lose when he
    started, vs his team's overall record?
(2) James Wood (OF, WSH Nationals 2024-25): did the Nats win more when
    Wood appeared in the lineup vs when he didn't?
"""
from __future__ import annotations
import sys
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests

BASE = "https://statsapi.mlb.com/api/v1"


def _get(path: str, **params) -> dict:
    url = f"{BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    return {}


def find_player_id(full_name: str) -> Optional[int]:
    """Use the /people/search endpoint."""
    r = _get("/people/search", names=full_name, limit=5)
    people = r.get("people", [])
    if not people:
        print(f"  No player match for {full_name!r}")
        return None
    # Prefer active MLB player
    for p in people:
        if p.get("active") and p.get("mlbDebutDate"):
            print(f"  Match: {p['fullName']}  id={p['id']}  "
                  f"pos={p.get('primaryPosition', {}).get('abbreviation', '?')}  "
                  f"team={p.get('currentTeam', {}).get('name', '?')}")
            return p["id"]
    p = people[0]
    print(f"  Fallback: {p['fullName']}  id={p['id']}")
    return p["id"]


def pitcher_game_log(player_id: int, seasons: List[int]) -> List[dict]:
    """Per-start box lines (opponent, result, W/L on the team, score)."""
    games = []
    for season in seasons:
        r = _get(f"/people/{player_id}/stats",
                 stats="gameLog", group="pitching", season=season)
        stats_blocks = r.get("stats", [])
        if not stats_blocks:
            continue
        splits = stats_blocks[0].get("splits", [])
        for s in splits:
            games.append({
                "date": s.get("date"),
                "team_id": s.get("team", {}).get("id"),
                "team_name": s.get("team", {}).get("name"),
                "opponent_id": s.get("opponent", {}).get("id"),
                "opponent_name": s.get("opponent", {}).get("name"),
                "game_pk": s.get("game", {}).get("gamePk"),
                "is_home": s.get("isHome"),
                "is_win": s.get("isWin"),
                "is_loss": s.get("isLoss"),
                "is_start": s.get("stat", {}).get("gamesStarted", 0) >= 1,
                "innings_pitched": s.get("stat", {}).get("inningsPitched"),
                "earned_runs": s.get("stat", {}).get("earnedRuns"),
                "season": season,
            })
    return games


def team_record_by_pitcher_started(games: List[dict]) -> None:
    """Did the TEAM win when he started? (isWin is the pitcher's decision,
    not the team's. We want team W/L — pull via schedule endpoint per
    game_pk to get actual scores.)"""
    starts = [g for g in games if g["is_start"]]
    print(f"  {len(starts)} starts (of {len(games)} appearances)")
    team_wins, team_losses = 0, 0
    details = []
    for g in starts:
        pk = g.get("game_pk")
        if not pk:
            continue
        j = _get(f"/game/{pk}/linescore")
        home_runs = j.get("teams", {}).get("home", {}).get("runs")
        away_runs = j.get("teams", {}).get("away", {}).get("runs")
        if home_runs is None or away_runs is None:
            continue
        his_team_home = bool(g.get("is_home"))
        his_team_runs = home_runs if his_team_home else away_runs
        opp_runs = away_runs if his_team_home else home_runs
        won = his_team_runs > opp_runs
        if won:
            team_wins += 1
        else:
            team_losses += 1
        details.append({
            "date": g["date"], "opp": g["opponent_name"],
            "home": his_team_home,
            "ip": g.get("innings_pitched"),
            "er": g.get("earned_runs"),
            "his_team_runs": his_team_runs,
            "opp_runs": opp_runs,
            "team_won": won,
        })
    total = team_wins + team_losses
    if total:
        print(f"  TEAM record when he started: {team_wins}-{team_losses} "
              f"= {team_wins/total*100:.1f}% win rate")
    # Show per-start lines
    print(f"  {'date':12s}  {'opp':30s}  {'home':5s}  {'IP':>5s}  "
          f"{'ER':>3s}  score(his-opp)  team")
    for d in details[-20:]:  # last 20 to keep output reasonable
        print(f"  {d['date']:12s}  {d['opp']:30s}  "
              f"{'Home' if d['home'] else 'Away':5s}  "
              f"{str(d['ip']):>5s}  {str(d['er']):>3s}  "
              f"  {d['his_team_runs']:>2}-{d['opp_runs']:<2}        "
              f"{'W' if d['team_won'] else 'L'}")
    return team_wins, team_losses


def team_season_record(team_id: int, season: int) -> Tuple[int, int]:
    j = _get("/standings", leagueId="103,104", season=season,
             standingsTypes="regularSeason")
    for rec in j.get("records", []):
        for t in rec.get("teamRecords", []):
            if t.get("team", {}).get("id") == team_id:
                w = t.get("wins", 0)
                l = t.get("losses", 0)
                return w, l
    return 0, 0


def batter_impact(player_id: int, seasons: List[int]) -> None:
    """Pull per-game batting log, then compare team result when he
    played vs games he didn't appear in."""
    played_game_pks: Dict[int, Tuple[int, bool]] = {}  # game_pk -> (team_id, team_won)
    team_id_seen = None
    for season in seasons:
        r = _get(f"/people/{player_id}/stats",
                 stats="gameLog", group="hitting", season=season)
        blocks = r.get("stats", [])
        if not blocks:
            continue
        for s in blocks[0].get("splits", []):
            game_pk = s.get("game", {}).get("gamePk")
            if not game_pk:
                continue
            his_team = s.get("team", {})
            team_id_seen = his_team.get("id")
            # Get team result
            j = _get(f"/game/{game_pk}/linescore")
            home_runs = j.get("teams", {}).get("home", {}).get("runs")
            away_runs = j.get("teams", {}).get("away", {}).get("runs")
            if home_runs is None:
                continue
            his_home = s.get("isHome")
            won = ((his_home and home_runs > away_runs) or
                   (not his_home and away_runs > home_runs))
            played_game_pks[game_pk] = (team_id_seen, won)

    if not played_game_pks:
        print("  No games with this player")
        return

    played_wins = sum(1 for _, (_, w) in played_game_pks.items() if w)
    played_losses = len(played_game_pks) - played_wins
    print(f"  He played in {len(played_game_pks)} games: "
          f"{played_wins}-{played_losses}  "
          f"({played_wins / len(played_game_pks) * 100:.1f}% win rate)")

    # Team full schedule across seasons, filter to games he didn't play
    total_team_wins, total_team_losses = 0, 0
    not_played_wins, not_played_losses = 0, 0
    if team_id_seen:
        for season in seasons:
            j = _get("/schedule", sportId=1, season=season, teamId=team_id_seen,
                     gameTypes="R")
            for date_block in j.get("dates", []):
                for g in date_block.get("games", []):
                    pk = g.get("gamePk")
                    if g.get("status", {}).get("codedGameState") != "F":
                        continue
                    home = g["teams"]["home"]
                    away = g["teams"]["away"]
                    is_home = home["team"]["id"] == team_id_seen
                    my_score = home["score"] if is_home else away["score"]
                    opp_score = away["score"] if is_home else home["score"]
                    if my_score is None or opp_score is None:
                        continue
                    won = my_score > opp_score
                    if won:
                        total_team_wins += 1
                    else:
                        total_team_losses += 1
                    if pk not in played_game_pks:
                        if won:
                            not_played_wins += 1
                        else:
                            not_played_losses += 1
    print(f"  Team overall {seasons}: "
          f"{total_team_wins}-{total_team_losses}  "
          f"({total_team_wins / max(1, total_team_wins + total_team_losses) * 100:.1f}%)")
    np_total = not_played_wins + not_played_losses
    if np_total:
        print(f"  Games he did NOT play: "
              f"{not_played_wins}-{not_played_losses}  "
              f"({not_played_wins / np_total * 100:.1f}% win rate)")
    diff = (played_wins / len(played_game_pks) * 100) - (
        not_played_wins / np_total * 100 if np_total else 0)
    print(f"  Win-rate lift when he played: {diff:+.1f} pp")


def main():
    print("=" * 72)
    print("  (A) Zack Littell — does his team lose when he starts?")
    print("=" * 72)
    pid = find_player_id("Zack Littell")
    if pid:
        games = pitcher_game_log(pid, [2024, 2025])
        team_record_by_pitcher_started(games)
        # Compare to team overall record
        team_id = None
        for g in games:
            if g.get("is_start") and g.get("team_id"):
                team_id = g["team_id"]
                break
        if team_id:
            for season in [2024, 2025]:
                w, l = team_season_record(team_id, season)
                tot = w + l
                if tot:
                    print(f"  Team full-season {season}: {w}-{l} "
                          f"({w / tot * 100:.1f}%)")

    print()
    print("=" * 72)
    print("  (B) James Wood — does Washington win more when he plays?")
    print("=" * 72)
    pid = find_player_id("James Wood")
    if pid:
        batter_impact(pid, [2024, 2025])


if __name__ == "__main__":
    main()
