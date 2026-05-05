"""
weather.py
----------
Open-Meteo wrapper. Free, no API key, rate-limitless for reasonable use.

Two endpoints used:
  - Historical (archive-api.open-meteo.com) for backtests
  - Forecast (api.open-meteo.com) for live predictions

Returns a small dict: {temp_f, wind_mph, wind_direction_deg}. Downstream,
weather.py does NOT know which direction points to center field — that logic
lives in feature_engineering (combined with stadium orientation).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import requests

log = logging.getLogger(__name__)

CACHE_DIR = Path("./data/weather_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HIST_URL = "https://archive-api.open-meteo.com/v1/archive"
FCST_URL = "https://api.open-meteo.com/v1/forecast"


def _cache_key(lat: float, lon: float, dt: datetime) -> Path:
    key = f"{lat:.4f}_{lon:.4f}_{dt.date().isoformat()}_{dt.hour:02d}"
    digest = hashlib.md5(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{digest}.json"


def _load(path: Path) -> Optional[Dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _save(path: Path, data: Dict) -> None:
    try:
        path.write_text(json.dumps(data))
    except Exception as e:
        log.warning("Weather cache write failed: %s", e)


def get_weather(lat: float, lon: float, when: datetime) -> Dict[str, float]:
    """
    Temperature (°F), wind speed (mph), wind direction (°).
    Uses historical endpoint if `when` is >= 2 days in the past,
    forecast endpoint otherwise. Cached per (lat, lon, YYYY-MM-DD, HH).

    Returns neutral defaults if the call fails for any reason — we don't want
    a flaky weather service to block the pipeline.
    """
    # Normalize to UTC-naive. The historical backtest path constructs `when`
    # as a tz-naive pandas Timestamp (game_date + 19h); the live slate path
    # (build_slate_frame) gets tz-aware timestamps parsed from the MLB Stats
    # API's ISO-Z strings. Without this, `when < datetime.utcnow() - …`
    # below raises `TypeError: can't compare offset-naive and offset-aware
    # datetimes` whenever a live prediction runs.
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)

    cache = _cache_key(lat, lon, when)
    cached = _load(cache)
    # Old cache entries lack humidity/precip_prob; fall through to a refetch
    # rather than serving partial dicts that break downstream features.
    if cached is not None and "humidity" in cached and "precip_prob" in cached:
        return cached

    is_historical = when < datetime.utcnow() - timedelta(days=2)
    url = HIST_URL if is_historical else FCST_URL

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ("temperature_2m,wind_speed_10m,wind_direction_10m,"
                   "relative_humidity_2m,precipitation_probability"),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "start_date": when.date().isoformat(),
        "end_date": when.date().isoformat(),
    }
    # The archive API doesn't expose precipitation_probability — strip it
    # so the historical request doesn't 400 out.
    if is_historical:
        params["hourly"] = ("temperature_2m,wind_speed_10m,wind_direction_10m,"
                            "relative_humidity_2m,precipitation")

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        dirs  = hourly.get("wind_direction_10m", [])
        humid = hourly.get("relative_humidity_2m", [])
        # Live: precip_prob (%); historical: precipitation (mm). For training
        # we coerce mm > 0 to a high "probability" of 100 — close enough to
        # the live signal that the model can learn on a unified column.
        if is_historical:
            precip_mm = hourly.get("precipitation", [])
            precips = [100.0 if (p or 0) > 0.1 else 0.0 for p in precip_mm]
        else:
            precips = hourly.get("precipitation_probability", [])

        # Find the closest hour to requested datetime
        target_h = when.hour
        if not times:
            raise RuntimeError("empty hourly payload")

        # Open-Meteo returns times in the requested timezone (UTC)
        idx = min(range(len(times)), key=lambda i: abs(
            datetime.fromisoformat(times[i]).hour - target_h
        ))
        result = {
            "temp_f":      float(temps[idx])   if idx < len(temps)   else 70.0,
            "wind_mph":    float(winds[idx])   if idx < len(winds)   else 0.0,
            "wind_deg":    float(dirs[idx])    if idx < len(dirs)    else 0.0,
            "humidity":    float(humid[idx])   if idx < len(humid)   else 50.0,
            "precip_prob": float(precips[idx]) if idx < len(precips) else 0.0,
        }
        _save(cache, result)
        return result

    except Exception as e:
        log.warning("Weather fetch failed (%.4f,%.4f,%s): %s — returning neutral",
                    lat, lon, when.isoformat(), e)
        return {"temp_f": 70.0, "wind_mph": 0.0, "wind_deg": 0.0,
                "humidity": 50.0, "precip_prob": 0.0}


def wind_out_to_cf_mph(wind_mph: float, wind_deg: float,
                       park_orientation_deg: float = 0.0) -> float:
    """
    Component of wind blowing out to center field.
    `park_orientation_deg` is the compass bearing from home plate to CF;
    most parks point roughly NNE (20-40°). Default 0 assumes wind_deg is
    already relative to CF (which is approximately fine for a league-wide
    analysis and much simpler than maintaining 30 orientation constants).

    Returns signed mph: positive = blowing out, negative = blowing in.
    """
    import math
    relative = (wind_deg - park_orientation_deg) % 360
    return wind_mph * math.cos(math.radians(relative))
