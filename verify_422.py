import requests
from datetime import date

d = date(2026, 4, 22)
url = (
    f"https://statsapi.mlb.com/api/v1/schedule"
    f"?sportId=1&date={d.isoformat()}&hydrate=team"
)
r = requests.get(url, timeout=20)
data = r.json()

games = data.get("dates", [{}])[0].get("games", []) if data.get("dates") else []
if not games:
    print(f"No games found for {d}")
else:
    print(f"{'Away':6s} @ {'Home':6s}  Score       Winner  Total  Status")
    print("-" * 60)
    for g in games:
        status = g.get("status", {}).get("detailedState", "Unknown")
        home_team = g.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation", "???")
        away_team = g.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation", "???")
        home_score = g.get("teams", {}).get("home", {}).get("score")
        away_score = g.get("teams", {}).get("away", {}).get("score")

        if home_score is not None and away_score is not None:
            total = home_score + away_score
            winner = home_team if home_score > away_score else away_team
            print(f"{away_team:6s} @ {home_team:6s}  {away_score:2d}-{home_score:<2d}      {winner:6s}  {total:2d}     {status}")
        else:
            print(f"{away_team:6s} @ {home_team:6s}  -                 -       {status}")