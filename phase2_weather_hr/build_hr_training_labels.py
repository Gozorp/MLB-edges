#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase-2 INGESTION (shadow branch) -- backfill a labeled HR x weather x Savant
training set. NOT wired into production; run in July to build the dataset.

One row per team-game with the engineered weather x batted-ball features + the
actual-HR label, so retrain_hr_weather.py can test whether weather genuinely
improves HR / total-runs prediction. Output: data/phase2/hr_weather_labels.csv

Usage (July): python phase2_weather_hr/build_hr_training_labels.py 2026-04-01 2026-09-30
"""
import sys, math

# ARCHIVE endpoint = ACTUAL past weather (NOT the forecast weather_runs.py uses for display)
OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
MLB = "https://statsapi.mlb.com/api/v1"

# ---- reuse the LOCKED weather_runs projection so train == serve ----
def effective_wind(speed_mph, wind_from_deg, cf_bearing, wind_coef):
    """Signed park-projected wind: + = OUT to CF (helps HR), - = IN. Mirrors
    tools/weather_runs.py compute() exactly."""
    wind_to = (wind_from_deg + 180) % 360
    dtheta = (wind_to - cf_bearing + 180) % 360 - 180
    alt = 1.0 if speed_mph <= 8 else min(1.4, 1.0 + 0.03 * (speed_mph - 8))
    return speed_mph * wind_coef * alt * math.cos(math.radians(dtheta))

def air_density_index(temp_f, elevation_ft, humidity_pct):
    """Carry multiplier: warm / high / humid = thinner air = more carry.
    TODO July: calibrate coefficients against the label set."""
    return (1.0 + 0.25 * ((temp_f - 70) / 35.0)
            + 0.15 * (elevation_ft / 8000.0)
            + 0.05 * ((humidity_pct - 50) / 100.0 * 0.3))

# ---- data fetchers (skeleton -- implement in July) ----
def fetch_archive_weather(lat, lon, date, first_pitch_hour_utc):
    """Open-Meteo ARCHIVE hourly for the actual game-time weather:
    GET OM_ARCHIVE?latitude=&longitude=&start_date=date&end_date=date&hourly=
    temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m
    &temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=GMT  -> pick the hour
    slot == first-pitch UTC hour. TODO July; cache by (lat,lon,date)."""
    raise NotImplementedError("July: implement archive weather fetch")

def lineup_savant_quality(team, date):
    """Season-to-date-BEFORE-`date` lineup Savant HR-quality: barrel%, avg EV,
    avg LA, fly_ball%, xwOBAcon. Pull Statcast (end_date = date-1) -> no leakage.
    TODO July: reuse tools/savant_*.py patterns; per-batter -> lineup aggregate."""
    raise NotImplementedError("July: implement pre-game Savant aggregation")

def actual_hrs(game_pk):
    """Actual HRs hit, total + per side, from the statsapi boxscore (the LABEL)."""
    raise NotImplementedError("July: implement boxscore HR count")

def engineer_row(weather, savant, park):
    """Feature row incl. the weather x batted-ball INTERACTIONS (the core idea:
    weather acts on balls in the air, so scale it by barrel/fly-ball quality)."""
    ew = effective_wind(weather["wind_mph"], weather["wind_from"],
                        park["cf_bearing"], park["wind_coef"])
    adi = air_density_index(weather["temp_f"], park["elevation_ft"], weather["humidity"])
    return {
        "effective_wind": round(ew, 2), "air_density_index": round(adi, 3),
        "temp_f": weather["temp_f"], "precip_prob": weather["precip"],
        "barrel_rate": savant["barrel"], "hard_hit": savant["hard_hit"],
        "fly_ball_pct": savant["fb"], "avg_la": savant["la"], "xwobacon": savant["xwobacon"],
        # --- core interaction features ---
        "ew_x_barrel":   round(ew * savant["barrel"], 3),
        "ew_x_flyball":  round(ew * savant["fb"], 3),
        "adi_x_la":      round(adi * savant["la"], 3),
        "adi_x_flyball": round(adi * savant["fb"], 3),
        "parkhr_x_ew":   round(park.get("hr_factor", 1.0) * ew, 3),
    }

def build(date_start, date_end):
    """Loop games -> join weather + savant + label -> data/phase2/hr_weather_labels.csv.
    TODO July: iterate the schedule, build rows via engineer_row(), write CSV."""
    raise NotImplementedError("July: implement the backfill loop")

if __name__ == "__main__":
    print("Phase-2 ingestion skeleton -- run in July. See phase2_weather_hr/PHASE2_SPEC.md")
    # build(sys.argv[1], sys.argv[2])
