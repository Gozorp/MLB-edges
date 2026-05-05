"""
stadiums.py
-----------
Static lookup of MLB stadium coordinates + park factors.

Park factors are 3-year averages (2022-2024) from Baseball Savant / FanGraphs,
normalized so 100 = league-average. Keyed by MLB team abbreviation.

runs_factor   : overall runs-scoring index (100 = neutral, 115 = Coors-style)
hr_factor     : home run index
timezone      : IANA timezone string — used for travel/TZ features
"""
from __future__ import annotations

from typing import Dict, Tuple

# (lat, lon, timezone, runs_factor, hr_factor)
STADIUMS: Dict[str, Dict] = {
    "ARI": {"name": "Chase Field",        "lat": 33.4453, "lon": -112.0667, "tz": "America/Phoenix",     "runs": 103, "hr": 102},
    "ATL": {"name": "Truist Park",        "lat": 33.8908, "lon": -84.4678,  "tz": "America/New_York",    "runs": 101, "hr": 104},
    "BAL": {"name": "Camden Yards",       "lat": 39.2839, "lon": -76.6217,  "tz": "America/New_York",    "runs": 102, "hr": 108},
    "BOS": {"name": "Fenway Park",        "lat": 42.3467, "lon": -71.0972,  "tz": "America/New_York",    "runs": 108, "hr":  96},
    "CHC": {"name": "Wrigley Field",      "lat": 41.9484, "lon": -87.6553,  "tz": "America/Chicago",     "runs": 102, "hr": 105},
    "CHW": {"name": "Guaranteed Rate",    "lat": 41.8300, "lon": -87.6339,  "tz": "America/Chicago",     "runs": 103, "hr": 113},
    "CIN": {"name": "Great American",     "lat": 39.0975, "lon": -84.5069,  "tz": "America/New_York",    "runs": 109, "hr": 121},
    "CLE": {"name": "Progressive Field",  "lat": 41.4958, "lon": -81.6852,  "tz": "America/New_York",    "runs":  98, "hr":  96},
    "COL": {"name": "Coors Field",        "lat": 39.7559, "lon": -104.9942, "tz": "America/Denver",      "runs": 117, "hr": 112},
    "DET": {"name": "Comerica Park",      "lat": 42.3390, "lon": -83.0485,  "tz": "America/Detroit",     "runs":  97, "hr":  92},
    "HOU": {"name": "Minute Maid Park",   "lat": 29.7572, "lon": -95.3553,  "tz": "America/Chicago",     "runs":  99, "hr": 104},
    "KC":  {"name": "Kauffman Stadium",   "lat": 39.0517, "lon": -94.4803,  "tz": "America/Chicago",     "runs":  98, "hr":  89},
    "KCR": {"name": "Kauffman Stadium",   "lat": 39.0517, "lon": -94.4803,  "tz": "America/Chicago",     "runs":  98, "hr":  89},
    "LAA": {"name": "Angel Stadium",      "lat": 33.8003, "lon": -117.8827, "tz": "America/Los_Angeles", "runs":  98, "hr": 102},
    "LAD": {"name": "Dodger Stadium",     "lat": 34.0739, "lon": -118.2400, "tz": "America/Los_Angeles", "runs":  98, "hr": 103},
    "MIA": {"name": "loanDepot park",     "lat": 25.7781, "lon": -80.2197,  "tz": "America/New_York",    "runs":  94, "hr":  87},
    "MIL": {"name": "American Family",    "lat": 43.0280, "lon": -87.9712,  "tz": "America/Chicago",     "runs": 100, "hr": 104},
    "MIN": {"name": "Target Field",       "lat": 44.9817, "lon": -93.2775,  "tz": "America/Chicago",     "runs": 100, "hr":  98},
    "NYM": {"name": "Citi Field",         "lat": 40.7571, "lon": -73.8458,  "tz": "America/New_York",    "runs":  95, "hr":  89},
    "NYY": {"name": "Yankee Stadium",     "lat": 40.8296, "lon": -73.9262,  "tz": "America/New_York",    "runs": 103, "hr": 116},
    "OAK": {"name": "Oakland Coliseum",   "lat": 37.7516, "lon": -122.2005, "tz": "America/Los_Angeles", "runs":  96, "hr":  91},
    "ATH": {"name": "Oakland Coliseum",   "lat": 37.7516, "lon": -122.2005, "tz": "America/Los_Angeles", "runs":  96, "hr":  91},
    "PHI": {"name": "Citizens Bank Park", "lat": 39.9057, "lon": -75.1665,  "tz": "America/New_York",    "runs": 101, "hr": 108},
    "PIT": {"name": "PNC Park",           "lat": 40.4469, "lon": -80.0057,  "tz": "America/New_York",    "runs":  98, "hr":  89},
    "SD":  {"name": "Petco Park",         "lat": 32.7073, "lon": -117.1566, "tz": "America/Los_Angeles", "runs":  95, "hr":  93},
    "SDP": {"name": "Petco Park",         "lat": 32.7073, "lon": -117.1566, "tz": "America/Los_Angeles", "runs":  95, "hr":  93},
    "SEA": {"name": "T-Mobile Park",      "lat": 47.5914, "lon": -122.3325, "tz": "America/Los_Angeles", "runs":  96, "hr":  95},
    "SF":  {"name": "Oracle Park",        "lat": 37.7786, "lon": -122.3893, "tz": "America/Los_Angeles", "runs":  94, "hr":  85},
    "SFG": {"name": "Oracle Park",        "lat": 37.7786, "lon": -122.3893, "tz": "America/Los_Angeles", "runs":  94, "hr":  85},
    "STL": {"name": "Busch Stadium",      "lat": 38.6226, "lon": -90.1928,  "tz": "America/Chicago",     "runs":  99, "hr":  92},
    "TB":  {"name": "Tropicana Field",    "lat": 27.7683, "lon": -82.6534,  "tz": "America/New_York",    "runs":  96, "hr":  92},
    "TBR": {"name": "Tropicana Field",    "lat": 27.7683, "lon": -82.6534,  "tz": "America/New_York",    "runs":  96, "hr":  92},
    "TEX": {"name": "Globe Life Field",   "lat": 32.7473, "lon": -97.0833,  "tz": "America/Chicago",     "runs":  97, "hr": 101},
    "TOR": {"name": "Rogers Centre",      "lat": 43.6414, "lon": -79.3894,  "tz": "America/Toronto",     "runs": 100, "hr": 104},
    "WSH": {"name": "Nationals Park",     "lat": 38.8730, "lon": -77.0074,  "tz": "America/New_York",    "runs":  99, "hr":  98},
    "WSN": {"name": "Nationals Park",     "lat": 38.8730, "lon": -77.0074,  "tz": "America/New_York",    "runs":  99, "hr":  98},
}


# Roof status: 0 = open-air (always exposed), 1 = retractable, 2 = fixed/closed-dome.
# When 1 or 2, weather features (wind, precip) should be discounted because the
# game is played indoors more often than not.
ROOF_TYPE: Dict[str, int] = {
    "ARI": 1,   # Chase Field — retractable
    "HOU": 1,   # Minute Maid — retractable
    "MIA": 1,   # loanDepot — retractable
    "MIL": 1,   # American Family — retractable
    "SEA": 1,   # T-Mobile Park — retractable
    "TEX": 1,   # Globe Life Field — retractable
    "TOR": 1,   # Rogers Centre — retractable
    "TB":  2,   # Tropicana — fixed dome
    "TBR": 2,
}


def roof_type(team: str) -> int:
    """0 = open, 1 = retractable, 2 = fixed dome. Defaults to 0 (open-air)."""
    return ROOF_TYPE.get(normalize_team(team), 0)


# Divisions for "is_divisional" feature
DIVISIONS: Dict[str, str] = {
    # AL East
    "BAL": "AL_E", "BOS": "AL_E", "NYY": "AL_E", "TB": "AL_E", "TBR": "AL_E", "TOR": "AL_E",
    # AL Central
    "CHW": "AL_C", "CLE": "AL_C", "DET": "AL_C", "KC": "AL_C", "KCR": "AL_C", "MIN": "AL_C",
    # AL West
    "HOU": "AL_W", "LAA": "AL_W", "OAK": "AL_W", "ATH": "AL_W", "SEA": "AL_W", "TEX": "AL_W",
    # NL East
    "ATL": "NL_E", "MIA": "NL_E", "NYM": "NL_E", "PHI": "NL_E", "WSH": "NL_E", "WSN": "NL_E",
    # NL Central
    "CHC": "NL_C", "CIN": "NL_C", "MIL": "NL_C", "PIT": "NL_C", "STL": "NL_C",
    # NL West
    "ARI": "NL_W", "COL": "NL_W", "LAD": "NL_W", "SD": "NL_W", "SDP": "NL_W", "SF": "NL_W", "SFG": "NL_W",
}


# Map pybaseball/bbref variants to our canonical keys
TEAM_ALIASES: Dict[str, str] = {
    "AZ": "ARI", "WAS": "WSH", "TBR": "TB", "KCR": "KC", "SDP": "SD",
    "SFG": "SF", "WSN": "WSH", "ATH": "OAK", "CWS": "CHW",
    # Full names (for the-odds-api which returns them verbose)
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Athletics": "OAK", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}


def normalize_team(team: str) -> str:
    """Map any team name/abbrev variant to our canonical 3-letter code.

    Aliases take precedence over STADIUMS membership — otherwise short codes
    like ATH that appear in both dicts short-circuit to themselves instead of
    being collapsed to their canonical key (ATH → OAK).
    """
    if team in TEAM_ALIASES:
        return TEAM_ALIASES[team]
    if team in STADIUMS:
        return team
    return team


def get_stadium(team: str) -> Dict:
    """Return stadium info for a team, normalizing name variants."""
    canonical = normalize_team(team)
    return STADIUMS.get(canonical, {
        "name": "Unknown", "lat": 0.0, "lon": 0.0,
        "tz": "America/New_York", "runs": 100, "hr": 100,
    })


def is_divisional(home: str, away: str) -> bool:
    h = DIVISIONS.get(normalize_team(home))
    a = DIVISIONS.get(normalize_team(away))
    return h is not None and h == a


def tz_offset_hours(from_team: str, to_team: str) -> int:
    """Approximate timezone hour offset from from_team's home park to to_team's."""
    tz_hours = {
        "America/New_York": -5,  "America/Detroit": -5,  "America/Toronto": -5,
        "America/Chicago": -6,
        "America/Denver": -7,    "America/Phoenix": -7,
        "America/Los_Angeles": -8,
    }
    fs = get_stadium(from_team)
    ts = get_stadium(to_team)
    return tz_hours.get(ts["tz"], -5) - tz_hours.get(fs["tz"], -5)
