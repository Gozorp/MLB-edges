"""
live_weather.py
---------------
Day-of-game live weather pulls keyed to stadium coordinates, returning the
pieces the model actually consumes:
    - first-pitch temperature (°F)
    - first-pitch wind speed (mph)
    - wind direction component to CF (signed: +out / -in)
    - precipitation probability (%)
    - retractable-roof discount factor

Wraps the existing `weather.get_weather()` low-level fetcher, but adds:
    1. First-pitch UTC resolution from the MLB Stats API schedule (instead of
       the historical-feature default of 7pm local).
    2. Per-park "effective" run/HR factor by combining the static factors in
       `stadiums.STADIUMS` with the live wind-out-to-CF component.
    3. Roof discounting — at MIA, TB, ARI, HOU, MIL, SEA, TEX, TOR a closed
       roof discounts wind/precip to the indoor baseline.

This module is the live counterpart to `weather.py` (which is general-purpose
and used by the historical backtest). Production predict callers should
prefer `fetch_slate_weather()` here.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

from .stadiums import STADIUMS, ROOF_TYPE, normalize_team
from .weather import get_weather, wind_out_to_cf_mph
from .config import CARRY_FT_PER_10F, TEMP_BASELINE_F

log = logging.getLogger(__name__)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Stadium → bearing (compass degrees) from home plate to dead center.
# Approximate values from MLB-published park orientation tables; precise to
# within a few degrees, which is enough to flip the sign of wind-to-CF.
PARK_CF_BEARING_DEG: Dict[str, float] = {
    "ARI":  18, "ATL":  53, "BAL":  31, "BOS":  45, "CHC":  46,
    "CHW":  43, "CIN":  72, "CLE":   0, "COL":   0, "DET":  20,
    "HOU":  19, "KC":   45, "LAA":  41, "LAD":  25, "MIA":  40,
    "MIL":  53, "MIN":  90, "NYM":  25, "NYY":  75, "OAK":  56,
    "PHI":  17, "PIT":  62, "SD":   12, "SEA":  46, "SF":   90,
    "STL":  50, "TB":   45, "TEX":  17, "TOR":   0, "WSH":  30,
}


@dataclass
class LiveWeather:
    team_home: str
    venue: str
    first_pitch_utc: datetime
    temp_f: float
    wind_mph: float
    wind_deg: float
    wind_to_cf_mph: float       # signed: + blowing out, - blowing in
    humidity: float
    precip_prob: float
    roof: int                    # 0=open / 1=retractable / 2=fixed
    runs_factor_static: int
    hr_factor_static: int
    runs_factor_effective: float
    hr_factor_effective: float
    carry_ft_delta: float       # ball-carry delta vs 70°F baseline


# ---------------------------------------------------------------------------
# Schedule pull — gives us the canonical first-pitch UTC time per game.
# ---------------------------------------------------------------------------
def _fetch_first_pitch_utc(date_str: str) -> Dict[str, datetime]:
    """{home_team_abbr: first_pitch_utc} for all games on `date_str` (YYYY-MM-DD)."""
    try:
        r = requests.get(
            SCHEDULE_URL,
            params={"sportId": 1, "date": date_str, "hydrate": "probablePitcher"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("Schedule fetch failed for %s: %s", date_str, e)
        return {}

    out: Dict[str, datetime] = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            home_full = (g.get("teams", {}).get("home", {})
                          .get("team", {}).get("name"))
            if not home_full:
                continue
            home_abbr = normalize_team(home_full)
            iso = g.get("gameDate", "")
            if iso:
                try:
                    out[home_abbr] = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                except ValueError:
                    pass
    return out


# ---------------------------------------------------------------------------
# Effective park-factor adjustment from live wind/temp
# ---------------------------------------------------------------------------
def _adjust_factors(home_abbr: str, w: dict) -> tuple[float, float, float]:
    """Return (effective_runs_factor, effective_hr_factor, carry_ft_delta).

    Uses the static park factor from `stadiums.STADIUMS` as the base, then
    layers on:
      * +0.35 ft of carry per 10°F over 70°F baseline (FanGraphs StatCast study).
      * Wind-out-to-CF component scaled at ~1.2 ft of carry per mph.
      * Roof discount: retractable assumed 60% closed, fixed = 100% closed.

    Returns the effective multipliers as floats (so 105 → 1.05 scaling
    convention is preserved with the existing `runs_factor` units).
    """
    sd = STADIUMS.get(home_abbr, {})
    base_runs = float(sd.get("runs", 100))
    base_hr   = float(sd.get("hr", 100))
    roof = ROOF_TYPE.get(home_abbr, 0)

    # Temperature carry term
    delta_temp = (w["temp_f"] - TEMP_BASELINE_F)
    temp_carry_ft = (delta_temp / 10.0) * CARRY_FT_PER_10F  # +/- ft of HR carry

    # Wind-to-CF component
    bearing = PARK_CF_BEARING_DEG.get(home_abbr, 0.0)
    wind_to_cf = wind_out_to_cf_mph(w["wind_mph"], w["wind_deg"], bearing)
    wind_carry_ft = wind_to_cf * 1.2

    # Roof discount: closed dome neutralizes wind/temp; retractable averaged
    if roof == 2:
        temp_carry_ft *= 0.0
        wind_carry_ft *= 0.0
    elif roof == 1:
        temp_carry_ft *= 0.4
        wind_carry_ft *= 0.4

    total_carry = temp_carry_ft + wind_carry_ft

    # Map carry-ft to a HR-factor multiplier. Statcast studies show ~3 ft of
    # extra fly-ball distance moves HR/FB rate by ~2-3 percentage points.
    # We translate: 1 ft of carry ≈ 0.7 points on hr_factor (out of 100).
    hr_adj = base_hr + 0.7 * total_carry
    runs_adj = base_runs + 0.4 * total_carry  # runs respond ~half as much

    return runs_adj / 100.0, hr_adj / 100.0, total_carry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_park_weather(
    home_team: str,
    first_pitch_utc: datetime,
) -> Optional[LiveWeather]:
    """Pull live weather for a single park at first-pitch."""
    home_abbr = normalize_team(home_team)
    sd = STADIUMS.get(home_abbr)
    if not sd:
        log.warning("Unknown home team %s — skipping weather", home_team)
        return None

    w = get_weather(sd["lat"], sd["lon"], first_pitch_utc)
    bearing = PARK_CF_BEARING_DEG.get(home_abbr, 0.0)
    wind_cf = wind_out_to_cf_mph(w["wind_mph"], w["wind_deg"], bearing)
    runs_eff, hr_eff, carry_delta = _adjust_factors(home_abbr, w)

    return LiveWeather(
        team_home=home_abbr,
        venue=sd["name"],
        first_pitch_utc=first_pitch_utc,
        temp_f=w["temp_f"],
        wind_mph=w["wind_mph"],
        wind_deg=w["wind_deg"],
        wind_to_cf_mph=wind_cf,
        humidity=w["humidity"],
        precip_prob=w["precip_prob"],
        roof=ROOF_TYPE.get(home_abbr, 0),
        runs_factor_static=int(sd["runs"]),
        hr_factor_static=int(sd["hr"]),
        runs_factor_effective=runs_eff,
        hr_factor_effective=hr_eff,
        carry_ft_delta=carry_delta,
    )


def fetch_slate_weather(date_str: str) -> pd.DataFrame:
    """Pull live weather for every park hosting a game on `date_str`.

    Returns a DataFrame keyed on home team abbreviation; one row per game.
    Used by `main_predict.py` to attach live park context before the model
    scores each row.
    """
    times = _fetch_first_pitch_utc(date_str)
    if not times:
        log.warning("Schedule lookup returned no games for %s", date_str)
        return pd.DataFrame()

    rows: List[Dict] = []
    for home_abbr, fp_utc in times.items():
        lw = fetch_park_weather(home_abbr, fp_utc)
        if lw is None:
            continue
        rows.append(asdict(lw))

    df = pd.DataFrame(rows)
    if not df.empty:
        df["first_pitch_utc"] = pd.to_datetime(df["first_pitch_utc"], utc=True)
    return df


# ---------------------------------------------------------------------------
# CLI for ad-hoc inspection
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = p.parse_args()

    df = fetch_slate_weather(args.date)
    if df.empty:
        print("No games / no weather.")
    else:
        cols = ["team_home", "venue", "temp_f", "wind_mph", "wind_to_cf_mph",
                "precip_prob", "roof", "runs_factor_effective",
                "hr_factor_effective", "carry_ft_delta"]
        print(df[cols].round(2).to_string(index=False))
