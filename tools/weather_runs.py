#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather_runs.py -- read-only weather/wind RUNS-TILT sidecar (DISPLAY ONLY, Phase 1).

For each slate game: home stadium coords + first-pitch UTC hour -> Open-Meteo hourly
forecast -> vector-projection effective wind (FROM->TO flip, per-park dampening,
surface->apex altitude) + temp/elevation air-density + precip -> a signed runs-tilt
index + a badge (IN down-runs / OUT up-runs / WET / NEUTRAL / INDOOR).

NEVER touches the model. See memory project_weather_runs_spec. Writes
docs/data/weather_runs_<date>.json. Fully sandboxed: any failure degrades to a
partial/empty sidecar; a missing sidecar just means no weather badge renders.

Usage:  python tools/weather_runs.py [YYYY-MM-DD]
"""
import sys, os, csv, json, math, time, glob, re, datetime, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

OM = "https://api.open-meteo.com/v1/forecast"
MLB = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-weather/1.0"}
CANON = {"CWS": "CHW", "ATH": "OAK", "ARI": "AZ"}


def _get(url, timeout=25, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e; time.sleep(sleep)
    raise last


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def circ_diff(a, b):
    return (a - b + 180) % 360 - 180


def load_stadiums():
    d = json.load(open("docs/data/stadium_coords.json", encoding="utf-8"))
    return d.get("teams", {})


def canon(ab):
    ab = (ab or "").strip().upper()
    return CANON.get(ab, ab)


def slate_matchups(date):
    """[(matchup_str, away, home)] from the slate diag."""
    p = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(p):
        p = "picks_%s_diag.csv" % date
    if not os.path.exists(p):
        return []
    csv.field_size_limit(10 ** 7)
    out, seen = [], set()
    with open(p, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mk = (row.get("matchup") or "").strip()
            m = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", mk)
            if not m:
                continue
            bare = "%s @ %s" % (m.group(1), m.group(2))
            if bare in seen:
                continue
            seen.add(bare)
            out.append((bare, m.group(1), m.group(2)))
    return out


def first_pitch_map(date):
    """canon(home_abbr) -> gameDate UTC iso, from statsapi schedule."""
    j = _get("%s/schedule?sportId=1&date=%s&hydrate=team" % (MLB, date))
    out = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            t = g.get("teams", {})
            h = ((t.get("home") or {}).get("team") or {}).get("abbreviation")
            gd = g.get("gameDate")
            if h and gd:
                out.setdefault(canon(h), gd)
    return out


_OM_CACHE = {}


def open_meteo_hour(lat, lon, when_iso):
    key = (round(lat, 3), round(lon, 3))
    if key not in _OM_CACHE:
        url = (OM + "?latitude=%s&longitude=%s&hourly=temperature_2m,precipitation_probability,"
               "weather_code,wind_speed_10m,wind_direction_10m&temperature_unit=fahrenheit"
               "&wind_speed_unit=mph&timezone=GMT&forecast_days=3" % (lat, lon))
        _OM_CACHE[key] = _get(url).get("hourly", {})
    h = _OM_CACHE[key]
    times = h.get("time", [])
    if not times:
        return None
    target = (when_iso or "")[:13]  # YYYY-MM-DDTHH
    idx = None
    for i, ts in enumerate(times):
        if ts[:13] == target:
            idx = i; break
    if idx is None:  # nearest by hour
        idx = 0
    g = lambda k: (h.get(k) or [None] * len(times))[idx]
    return {"hour": times[idx], "temp_f": g("temperature_2m"), "precip": g("precipitation_probability"),
            "code": g("weather_code"), "wind_mph": g("wind_speed_10m"), "wind_from": g("wind_direction_10m")}


def compute(stadium, wx):
    if stadium.get("is_indoor"):
        return {"indoor": True, "badge": "INDOOR", "runs_tilt": 0.0,
                "why": "Indoor dome - weather not a factor"}
    speed = float(wx.get("wind_mph") or 0.0)
    wfrom = float(wx.get("wind_from") or 0.0)
    temp = float(wx.get("temp_f") if wx.get("temp_f") is not None else 70.0)
    precip = float(wx.get("precip") or 0.0)
    code = int(wx.get("code") or 0)
    cf = float(stadium.get("cf_bearing", 0))
    coef = float(stadium.get("wind_coef", 0.6))
    elev = float(stadium.get("elevation_ft", 0))
    wind_to = (wfrom + 180) % 360
    dtheta = circ_diff(wind_to, cf)
    alt = 1.0 if speed <= 8 else min(1.4, 1.0 + 0.03 * (speed - 8))
    eff = speed * coef * alt * math.cos(math.radians(dtheta))
    wet = precip >= 50 or code >= 51
    wind_tilt = clamp(eff / 12.0, -1, 1)
    temp_tilt = clamp((temp - 70) / 35.0, -0.5, 0.5)
    elev_tilt = clamp((elev - 1000) / 8000.0, 0, 0.5)
    runs_tilt = clamp(0.60 * wind_tilt + 0.25 * temp_tilt + 0.15 * elev_tilt - (0.5 if wet else 0), -1, 1)
    if wet:
        badge = "WET"
    elif eff >= 4:
        badge = "OUT"
    elif eff <= -4:
        badge = "IN"
    else:
        badge = "NEUTRAL"
    why = []
    if badge == "OUT":
        why.append("%.0f mph blowing OUT to CF (+runs)" % abs(eff))
    elif badge == "IN":
        why.append("%.0f mph blowing IN toward home (-runs)" % abs(eff))
    elif badge == "WET":
        why.append("rain/storm risk (precip %.0f%%) - fewer runs / delay risk" % precip)
    else:
        why.append("wind %.0f mph crosswind/calm" % speed)
    if elev >= 3000:
        why.append("thin air %d ft (+carry)" % int(elev))
    if temp <= 50:
        why.append("cold %.0f F (-carry)" % temp)
    elif temp >= 88:
        why.append("hot %.0f F (+carry)" % temp)
    if stadium.get("is_retractable"):
        why.append("retractable roof - may be closed")
        if wet or temp <= 45 or temp >= 95:
            runs_tilt *= 0.45  # roof very likely closed in storm/extreme -> weather muted
            if badge == "WET":
                badge = "WET?"
    return {"indoor": False, "badge": badge, "runs_tilt": round(runs_tilt, 3),
            "eff_wind": round(eff, 1), "dir": "out" if eff > 0 else "in",
            "wind_mph": round(speed, 1), "wind_from": int(wfrom), "cf_bearing": int(cf),
            "temp_f": round(temp, 1), "precip_pct": int(precip), "code": code, "wet": wet,
            "retractable": bool(stadium.get("is_retractable")),
            "why": "; ".join(why)}


def build(date):
    stad = load_stadiums()
    fp = {}
    try:
        fp = first_pitch_map(date)
    except Exception as e:
        print("schedule fetch failed: %s" % e)
    out = {}
    for bare, away, home in slate_matchups(date):
        s = stad.get(canon(home)) or stad.get(home)
        if not s:
            continue
        try:
            when = fp.get(canon(home)) or ("%sT23:00:00Z" % date)
            wx = open_meteo_hour(s["lat"], s["lon"], when)
            if not wx:
                continue
            rec = compute(s, wx)
            rec["stadium"] = s.get("name"); rec["home"] = canon(home)
            rec["first_pitch_utc"] = when; rec["forecast_hour"] = wx.get("hour")
            out[bare] = rec
        except Exception as e:
            out[bare] = {"badge": "NEUTRAL", "error": type(e).__name__}
    return out


def resolve_date(arg):
    if arg:
        return arg
    fs = sorted(glob.glob("docs/data/picks_*_diag.csv") + glob.glob("picks_*_diag.csv"), key=os.path.getmtime)
    if fs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fs[-1]))
        if m:
            return m.group(1)
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def main():
    date = resolve_date(sys.argv[1] if len(sys.argv) > 1 else None)
    sidecar = {"date": date,
               "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "method": "effWind=speed*coef*alt*cos(windTo-cfBearing); tilt=0.6*wind+0.25*temp+0.15*elev-0.5*wet; DISPLAY ONLY",
               "games": {}}
    try:
        sidecar["games"] = build(date)
        rows = [(k, v.get("badge"), v.get("eff_wind"), v.get("runs_tilt")) for k, v in sidecar["games"].items()]
        flagged = [r for r in rows if r[1] in ("IN", "OUT", "WET")]
        print("games: %d  | flagged: %d" % (len(rows), len(flagged)))
        for k, b, e, t in rows:
            print("  %-20s %-8s eff=%s tilt=%s" % (k, b, e, t))
    except Exception as e:
        print("WEATHER-FAIL %s: %s" % (type(e).__name__, e))
    outp = os.path.join("docs", "data", "weather_runs_%s.json" % date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=1)
    os.replace(outp + ".tmp", outp)  # atomic: no torn sidecar on crash/AV-lock
    print("wrote %s" % outp)


if __name__ == "__main__":
    main()
