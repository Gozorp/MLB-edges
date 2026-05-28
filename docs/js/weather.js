/* weather.js — live weather HUD for mlb_edge
 *
 * Self-bootstrapping. Loaded as a plain <script src=...> tag from index.html.
 * Hooks into the existing Quant Terminal dashboard without touching renderSlate().
 *
 * Pre-game semantics: for any game whose first-pitch time is more than 30 min
 * in the future, the chip displays the FORECAST at the game's UTC start hour —
 * not the current weather at the stadium right now. For live and finished
 * games the chip uses current weather. This matters: a 7:10 PM first pitch
 * with rain at 5 PM but clearing by game time should show "Clear", not "Rain".
 *
 * Data sources:
 *   - Open-Meteo (https://open-meteo.com/) — free, no key. Returns current +
 *     48h hourly forecast with WMO weather codes.
 *   - MLB Stats API schedule — gives each game's UTC gameDate for forecast
 *     slot selection.
 *   - ipapi.co — IP-based geolocation for the user-location chip (best-effort).
 *
 * Refresh cadence: 15 min. Per-stadium full-forecast cache; current vs.
 * forecast-at-hour both pull from the same cached response.
 *
 * Unit toggle (°C / °F): persisted in localStorage as mlb_edge_weather_unit.
 * Default: 'F' (US baseball audience).
 */

(function () {
  'use strict';

  const REFRESH_MS = 15 * 60 * 1000;
  const SCHEDULE_TTL_MS = 5 * 60 * 1000;
  const STADIUM_JSON_URL = 'data/stadium_coords.json';
  const OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast';
  const IPAPI_URL = 'https://ipapi.co/json/';
  const MLB_SCHEDULE_URL = 'https://statsapi.mlb.com/api/v1/schedule';
  const UNIT_LS_KEY = 'mlb_edge_weather_unit';
  // Pre-game threshold: if game starts > this many ms in the future, show
  // forecast at game time, not current. 30 min cushion absorbs first-pitch
  // drift and gives a sensible cutover into the "live" rendering.
  const PREGAME_THRESHOLD_MS = 30 * 60 * 1000;

  // ---- WMO weather code -> { icon, label, severity } -------------------
  const WX_CODE_MAP = {
    0:  { icon: 'sun',   label: 'Clear',          sev: 0 },
    1:  { icon: 'sun',   label: 'Mostly clear',   sev: 0 },
    2:  { icon: 'pcld',  label: 'Partly cloudy',  sev: 1 },
    3:  { icon: 'cld',   label: 'Overcast',       sev: 1 },
    45: { icon: 'fog',   label: 'Fog',            sev: 1 },
    48: { icon: 'fog',   label: 'Freezing fog',   sev: 1 },
    51: { icon: 'rain',  label: 'Light drizzle',  sev: 2 },
    53: { icon: 'rain',  label: 'Drizzle',        sev: 2 },
    55: { icon: 'rain',  label: 'Heavy drizzle',  sev: 2 },
    56: { icon: 'rain',  label: 'Freezing drizzle', sev: 3 },
    57: { icon: 'rain',  label: 'Freezing drizzle', sev: 3 },
    61: { icon: 'rain',  label: 'Light rain',     sev: 2 },
    63: { icon: 'rain',  label: 'Rain',           sev: 2 },
    65: { icon: 'rain',  label: 'Heavy rain',     sev: 3 },
    66: { icon: 'rain',  label: 'Freezing rain',  sev: 3 },
    67: { icon: 'rain',  label: 'Freezing rain',  sev: 3 },
    71: { icon: 'snow',  label: 'Light snow',     sev: 2 },
    73: { icon: 'snow',  label: 'Snow',           sev: 2 },
    75: { icon: 'snow',  label: 'Heavy snow',     sev: 3 },
    77: { icon: 'snow',  label: 'Snow grains',    sev: 2 },
    80: { icon: 'rain',  label: 'Rain showers',   sev: 2 },
    81: { icon: 'rain',  label: 'Rain showers',   sev: 2 },
    82: { icon: 'rain',  label: 'Heavy showers',  sev: 3 },
    85: { icon: 'snow',  label: 'Snow showers',   sev: 2 },
    86: { icon: 'snow',  label: 'Snow showers',   sev: 3 },
    95: { icon: 'storm', label: 'Thunderstorm',   sev: 3 },
    96: { icon: 'storm', label: 'Thunderstorm',   sev: 3 },
    99: { icon: 'storm', label: 'Severe storm',   sev: 3 },
  };

  const ICONS = {
    sun:   '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="3"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3"/></svg>',
    pcld:  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="5" cy="6" r="2.2"/><path d="M5 1.5v1.3M1.5 6h1.3M8.2 3.3L7.3 4.2"/><path d="M7 11h6a2 2 0 100-4 3 3 0 00-5.9.5A2.5 2.5 0 007 11z"/></svg>',
    cld:   '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 13h7a2.5 2.5 0 100-5 3.5 3.5 0 00-6.8.5A2.5 2.5 0 005 13z"/></svg>',
    fog:   '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 5h11M2 8h12M2 11h10M3 14h11"/></svg>',
    rain:  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 9h7a2.5 2.5 0 100-5 3.5 3.5 0 00-6.8.5A2.5 2.5 0 005 9z"/><path d="M5 11v2M8 11v2M11 11v2"/></svg>',
    snow:  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 8h7a2.5 2.5 0 100-5 3.5 3.5 0 00-6.8.5A2.5 2.5 0 005 8z"/><path d="M6 11l.5.5M8 10v2M10 11l-.5.5M6 13l.5-.5M10 13l-.5-.5"/></svg>',
    storm: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 8h7a2.5 2.5 0 100-5 3.5 3.5 0 00-6.8.5A2.5 2.5 0 005 8z"/><path d="M8 8l-2 4h2l-1 3 3-4H8l1-3"/></svg>',
    indoor:'<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 8l6-5 6 5M3.5 8v6h9V8M6.5 14v-3h3v3"/></svg>',
  };

  const STYLE = `
    .wx-chip {
      display: inline-flex; align-items: center; gap: 4px;
      font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
      font-size: 11px; color: var(--muted, #8b949e);
      padding: 2px 6px; border: 1px solid var(--border, #30363d);
      border-radius: 3px; background: rgba(255,255,255,0.02);
      vertical-align: middle; white-space: nowrap;
    }
    .wx-chip.wx-indoor { color: #7d8590; opacity: 0.75; }
    .wx-chip.wx-sev-2  { border-color: #6e7681; color: #adbac7; }
    .wx-chip.wx-sev-3  { border-color: #db6d28; color: #f0883e; }
    .wx-chip.wx-loading { opacity: 0.4; }
    .wx-chip svg { flex-shrink: 0; }
    .wx-chip .wx-temp { font-weight: 500; color: var(--text, #c9d1d9); }
    .wx-chip .wx-precip { color: #58a6ff; }
    .wx-chip .wx-roof-badge {
      font-size: 9px; opacity: 0.55; margin-left: 2px;
      letter-spacing: 0.5px;
    }
    .wx-chip .wx-fcst-badge {
      font-size: 8px; opacity: 0.7; letter-spacing: 0.5px;
      color: #58a6ff; margin-left: 2px;
      text-transform: uppercase;
    }
    .wx-row-chip { margin-left: 8px; }

    .wx-user-chip { cursor: default; }
    .wx-user-chip .wx-loc {
      color: var(--text, #c9d1d9);
      font-weight: 500;
    }

    .wx-hud {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 14px;
      align-items: center;
      margin: 12px 0;
      padding: 10px 12px;
      border: 1px solid var(--border, #30363d);
      border-radius: 4px;
      background: rgba(255,255,255,0.015);
      font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
      font-size: 12px;
      color: var(--muted, #8b949e);
    }
    .wx-hud-left { display: flex; align-items: center; gap: 10px; }
    .wx-hud-left .wx-big-icon svg { width: 28px; height: 28px; }
    .wx-hud-left .wx-stadium { font-size: 11px; opacity: 0.7; }
    .wx-hud-left .wx-condition { font-size: 13px; color: var(--text, #c9d1d9); }
    .wx-hud-mid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      font-size: 11px;
    }
    .wx-hud-mid .wx-metric { display: flex; flex-direction: column; gap: 2px; }
    .wx-hud-mid .wx-metric-lbl {
      font-size: 9px; opacity: 0.6; letter-spacing: 1px; text-transform: uppercase;
    }
    .wx-hud-mid .wx-metric-val {
      color: var(--text, #c9d1d9); font-weight: 500; font-size: 13px;
    }
    .wx-wind-arrow {
      display: inline-block; transform-origin: center;
      transition: transform 0.3s ease;
    }
    .wx-hud-right {
      text-align: right; font-size: 9px; opacity: 0.5;
      max-width: 130px; line-height: 1.4;
    }
    .wx-hud-right.wx-fcst { opacity: 0.85; color: #58a6ff; }

    .wx-unit-toggle {
      cursor: pointer; user-select: none;
      border: 1px solid var(--border, #30363d);
      padding: 1px 4px; border-radius: 2px;
      font-size: 10px; margin-left: 4px;
      background: transparent; color: inherit;
    }
    .wx-unit-toggle:hover { color: var(--text, #c9d1d9); }
  `;

  // ---- team abbrev <-> MLB Stats API teamId ----------------------------
  // Same mapping as tools/luck_adjusted_probe.py — keep them in sync if MLB
  // ever issues a new teamId (e.g., when Athletics finalize Vegas move).
  const TEAM_ABBR_TO_ID = {
    "ARI": 109, "AZ": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CHW": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117,
    "KC":  118, "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158, "MIN": 142,
    "NYM": 121, "NYY": 147, "OAK": 133, "PHI": 143, "PIT": 134, "SD":  135,
    "SEA": 136, "SF":  137, "STL": 138, "TB":  139, "TEX": 140, "TOR": 141,
    "WSH": 120,
  };
  const TEAM_ID_TO_ABBR = (function () {
    const out = {};
    // Prefer the diag CSV convention: AZ over ARI when collision.
    const preferred = new Set(["AZ", "ATL", "BAL", "BOS", "CHC", "CHW", "CIN",
      "CLE", "COL", "DET", "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN",
      "NYM", "NYY", "OAK", "PHI", "PIT", "SD", "SEA", "SF", "STL", "TB",
      "TEX", "TOR", "WSH"]);
    Object.entries(TEAM_ABBR_TO_ID).forEach(([abbr, id]) => {
      if (!out[id] || preferred.has(abbr)) out[id] = abbr;
    });
    return out;
  })();

  // ---- shared state ----------------------------------------------------
  let STADIUMS = null;
  let STADIUMS_PROMISE = null;
  const FETCH_CACHE = new Map();   // 'lat,lon' -> { data: meteoJson, ts }
  const INFLIGHT = new Map();
  let SCHEDULE_CACHE = null;       // { data: { 'AWAY@HOME': {startUtc, status} }, ts }
  let SCHEDULE_PROMISE = null;

  let currentUnit = (function () {
    try {
      const v = localStorage.getItem(UNIT_LS_KEY);
      return v === 'C' || v === 'F' ? v : 'F';
    } catch (e) { return 'F'; }
  })();

  function setUnit(u) {
    currentUnit = (u === 'C') ? 'C' : 'F';
    try { localStorage.setItem(UNIT_LS_KEY, currentUnit); } catch (e) {}
    rerenderAllChips();
  }

  function fmtTemp(celsius) {
    if (celsius == null || Number.isNaN(celsius)) return '—';
    const v = (currentUnit === 'F') ? (celsius * 9 / 5 + 32) : celsius;
    return Math.round(v) + '°' + currentUnit;
  }

  function fmtWindSpeed(kmh) {
    if (kmh == null) return '—';
    if (currentUnit === 'F') return Math.round(kmh * 0.621371) + ' mph';
    return Math.round(kmh) + ' km/h';
  }

  function dirToCompass(deg) {
    if (deg == null) return '—';
    const pts = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                 'S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return pts[Math.round(deg / 22.5) % 16];
  }

  function fmtGameTimeLocal(isoUtc) {
    try {
      return new Date(isoUtc).toLocaleTimeString(undefined, {
        hour: 'numeric', minute: '2-digit'
      });
    } catch (e) { return ''; }
  }

  // ---- stadium JSON loader --------------------------------------------
  function loadStadiums() {
    if (STADIUMS_PROMISE) return STADIUMS_PROMISE;
    STADIUMS_PROMISE = fetch(STADIUM_JSON_URL)
      .then(r => r.json())
      .then(j => {
        STADIUMS = j.teams || {};
        return STADIUMS;
      })
      .catch(err => {
        console.warn('[wx] stadium_coords.json fetch failed', err);
        STADIUMS = {};
        return STADIUMS;
      });
    return STADIUMS_PROMISE;
  }

  // ---- MLB schedule loader --------------------------------------------
  function todayDateIso() {
    return new Date().toISOString().slice(0, 10);
  }

  function fetchTodaySchedule() {
    const now = Date.now();
    if (SCHEDULE_CACHE && (now - SCHEDULE_CACHE.ts) < SCHEDULE_TTL_MS) {
      return Promise.resolve(SCHEDULE_CACHE.data);
    }
    if (SCHEDULE_PROMISE) return SCHEDULE_PROMISE;

    const date = todayDateIso();
    const url = `${MLB_SCHEDULE_URL}?sportId=1&date=${date}`;
    SCHEDULE_PROMISE = fetch(url)
      .then(r => r.json())
      .then(j => {
        const games = {};
        (j.dates || []).forEach(d => {
          (d.games || []).forEach(g => {
            const awayId = g.teams && g.teams.away && g.teams.away.team && g.teams.away.team.id;
            const homeId = g.teams && g.teams.home && g.teams.home.team && g.teams.home.team.id;
            if (!awayId || !homeId) return;
            const aw = TEAM_ID_TO_ABBR[awayId];
            const hm = TEAM_ID_TO_ABBR[homeId];
            if (!aw || !hm) return;
            const key = `${aw}@${hm}`;
            const gameNum = g.gameNumber || 1;
            const entry = {
              startUtc: g.gameDate,
              status: (g.status && g.status.detailedState) || '',
              gameNumber: gameNum,
            };
            // Always set the bare key (first/only game), and a DH-suffix key
            // when a doubleheader is detected. For doubleheaders the bare key
            // will hold G1 (the earlier game) since we process in order.
            if (gameNum > 1) {
              games[`${key}_G${gameNum}`] = entry;
            } else {
              games[key] = entry;
            }
          });
        });
        SCHEDULE_CACHE = { data: games, ts: Date.now() };
        SCHEDULE_PROMISE = null;
        return games;
      })
      .catch(err => {
        console.warn('[wx] MLB schedule fetch failed', err);
        SCHEDULE_PROMISE = null;
        return {};
      });
    return SCHEDULE_PROMISE;
  }

  // ---- Open-Meteo full-forecast fetch ---------------------------------
  function cacheKey(lat, lon) {
    return `${lat.toFixed(3)},${lon.toFixed(3)}`;
  }

  function fetchStadiumWeatherFull(lat, lon) {
    const key = cacheKey(lat, lon);
    const now = Date.now();
    const cached = FETCH_CACHE.get(key);
    if (cached && (now - cached.ts) < REFRESH_MS) {
      return Promise.resolve(cached.data);
    }
    if (INFLIGHT.has(key)) return INFLIGHT.get(key);

    // Pull current + hourly forecast in one call. timezone=GMT keeps the
    // hourly.time array in UTC so it matches MLB Stats API gameDate.
    const url = `${OPEN_METEO_URL}?latitude=${lat}&longitude=${lon}`
      + '&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m'
      + '&hourly=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,precipitation_probability'
      + '&forecast_days=2'
      + '&wind_speed_unit=kmh'
      + '&temperature_unit=celsius'
      + '&timezone=GMT';

    const p = fetch(url)
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(j => {
        FETCH_CACHE.set(key, { data: j, ts: Date.now() });
        INFLIGHT.delete(key);
        return j;
      })
      .catch(err => {
        INFLIGHT.delete(key);
        console.warn('[wx] open-meteo fetch failed', err);
        throw err;
      });

    INFLIGHT.set(key, p);
    return p;
  }

  // Extract a weather snapshot from a cached Open-Meteo response.
  // If targetIsoUtc is provided AND falls in the hourly forecast window,
  // returns forecast at that hour (is_forecast=true). Otherwise returns
  // current_weather with precip% peaked over the next 3 hours.
  function extractWeatherAt(meteoJson, targetIsoUtc) {
    if (!meteoJson) return null;
    const hourly = meteoJson.hourly || {};
    const times = hourly.time || [];

    if (targetIsoUtc) {
      const targetHr = targetIsoUtc.slice(0, 13);  // 'YYYY-MM-DDTHH'
      const idx = times.findIndex(t => t.startsWith(targetHr));
      if (idx >= 0) {
        return {
          temp_c:       hourly.temperature_2m         && hourly.temperature_2m[idx],
          weather_code: hourly.weather_code           && hourly.weather_code[idx],
          wind_kmh:     hourly.wind_speed_10m         && hourly.wind_speed_10m[idx],
          wind_dir:     hourly.wind_direction_10m     && hourly.wind_direction_10m[idx],
          precip_pct:   hourly.precipitation_probability && hourly.precipitation_probability[idx],
          is_forecast: true,
          forecast_for: targetIsoUtc,
        };
      }
      // target outside window — fall through to current
    }

    // Current weather + peak precip% over the next 3 hours.
    const cur = meteoJson.current || {};
    const nowIsoHr = new Date().toISOString().slice(0, 13);
    const popIdx = times.findIndex(t => t.startsWith(nowIsoHr));
    let peakNext3 = 0;
    if (popIdx >= 0) {
      const pops = (hourly.precipitation_probability || []).slice(popIdx, popIdx + 3);
      peakNext3 = pops.reduce((a, b) => Math.max(a, b == null ? 0 : b), 0);
    }
    return {
      temp_c: cur.temperature_2m,
      weather_code: cur.weather_code,
      wind_kmh: cur.wind_speed_10m,
      wind_dir: cur.wind_direction_10m,
      precip_pct: peakNext3,
      is_forecast: false,
    };
  }

  // Decide what target time to use for a given matchup.
  // Returns ISO-UTC string for pre-game forecast OR null for current.
  function targetIsoForGame(scheduleEntry) {
    if (!scheduleEntry || !scheduleEntry.startUtc) return null;
    const startMs = new Date(scheduleEntry.startUtc).getTime();
    if (isNaN(startMs)) return null;
    if (startMs > Date.now() + PREGAME_THRESHOLD_MS) {
      return scheduleEntry.startUtc;
    }
    return null;  // live or finished — current is correct
  }

  // ---- chip render helpers --------------------------------------------
  function buildChipHTML(wx, stadiumMeta) {
    if (!wx) return '<span class="wx-chip wx-loading">—</span>';
    const wmo = WX_CODE_MAP[wx.weather_code] || { icon: 'cld', label: 'Unknown', sev: 1 };
    const icon = ICONS[wmo.icon] || ICONS.cld;
    const sevClass = wmo.sev >= 2 ? ` wx-sev-${wmo.sev}` : '';
    const tempHtml = `<span class="wx-temp">${fmtTemp(wx.temp_c)}</span>`;
    const precipHtml = (wx.precip_pct != null && wx.precip_pct >= 10)
      ? ` <span class="wx-precip">${Math.round(wx.precip_pct)}%</span>`
      : '';
    const windHtml = (wx.wind_kmh != null && wx.wind_kmh >= 8)
      ? ` ${dirToCompass(wx.wind_dir)} ${fmtWindSpeed(wx.wind_kmh)}`
      : '';
    const retractBadge = (stadiumMeta && stadiumMeta.is_retractable)
      ? '<span class="wx-roof-badge" title="Retractable roof">⌂</span>' : '';
    const fcstBadge = wx.is_forecast
      ? '<span class="wx-fcst-badge" title="Forecast for first pitch">fcst</span>'
      : '';

    let titleParts = [];
    if (stadiumMeta && stadiumMeta.name) titleParts.push(stadiumMeta.name);
    if (wx.is_forecast && wx.forecast_for) {
      titleParts.push('Forecast for first pitch ' + fmtGameTimeLocal(wx.forecast_for));
    } else {
      titleParts.push('Current');
    }
    titleParts.push(`${wmo.label}, ${fmtTemp(wx.temp_c)}`);
    if (wx.precip_pct >= 10) titleParts.push(`${Math.round(wx.precip_pct)}% precip`);
    if (wx.wind_kmh >= 8) titleParts.push(`wind ${dirToCompass(wx.wind_dir)} ${fmtWindSpeed(wx.wind_kmh)}`);
    const title = titleParts.join(' · ').replace(/"/g, '&quot;');

    return `<span class="wx-chip${sevClass}" title="${title}">`
      + icon + tempHtml + precipHtml + windHtml + retractBadge + fcstBadge + '</span>';
  }

  function buildIndoorChip(stadiumMeta) {
    const stadiumName = stadiumMeta && stadiumMeta.name ? stadiumMeta.name : 'Indoor';
    return `<span class="wx-chip wx-indoor" title="${stadiumName} · indoor / weather not a factor">`
      + ICONS.indoor + '<span>INDOOR</span></span>';
  }

  function buildHudHTML(wx, meta, scheduleEntry) {
    if (meta && meta.is_indoor) {
      return `<div class="wx-hud" data-wx-indoor="true">
        <div class="wx-hud-left">
          <span class="wx-big-icon">${ICONS.indoor}</span>
          <div>
            <div class="wx-condition">INDOOR</div>
            <div class="wx-stadium">${meta.name || ''}</div>
          </div>
        </div>
        <div class="wx-hud-mid"><div class="wx-metric"><div class="wx-metric-lbl">Status</div><div class="wx-metric-val">Climate-controlled</div></div></div>
        <div class="wx-hud-right">weather not a factor</div>
      </div>`;
    }
    if (!wx) {
      return `<div class="wx-hud wx-loading"><div class="wx-hud-left">…</div></div>`;
    }
    const wmo = WX_CODE_MAP[wx.weather_code] || { icon: 'cld', label: 'Unknown' };
    const icon = ICONS[wmo.icon] || ICONS.cld;
    const arrowDeg = (wx.wind_dir == null) ? 0 : (wx.wind_dir + 180) % 360;
    const unitToggle = `<button class="wx-unit-toggle" data-wx-unit-toggle="1" title="Toggle °C / °F">${currentUnit === 'F' ? '°F' : '°C'}</button>`;
    const retractBadge = (meta && meta.is_retractable)
      ? ' <span class="wx-roof-badge" title="Retractable roof">⌂</span>' : '';

    let rightBlock;
    if (wx.is_forecast && wx.forecast_for) {
      rightBlock = `<div class="wx-hud-right wx-fcst">FORECAST<br>first pitch ${fmtGameTimeLocal(wx.forecast_for)}</div>`;
    } else {
      const liveHint = (scheduleEntry && /In Progress|Live/.test(scheduleEntry.status))
        ? 'live conditions' : 'current · refresh 15m';
      rightBlock = `<div class="wx-hud-right">${liveHint}</div>`;
    }

    return `<div class="wx-hud">
      <div class="wx-hud-left">
        <span class="wx-big-icon">${icon}</span>
        <div>
          <div class="wx-condition">${wmo.label}${retractBadge}</div>
          <div class="wx-stadium">${meta ? meta.name : ''}</div>
        </div>
      </div>
      <div class="wx-hud-mid">
        <div class="wx-metric">
          <div class="wx-metric-lbl">Temp</div>
          <div class="wx-metric-val">${fmtTemp(wx.temp_c)}${unitToggle}</div>
        </div>
        <div class="wx-metric">
          <div class="wx-metric-lbl">Wind</div>
          <div class="wx-metric-val">
            <span class="wx-wind-arrow" style="transform: rotate(${arrowDeg}deg);">↑</span>
            ${dirToCompass(wx.wind_dir)} ${fmtWindSpeed(wx.wind_kmh)}
          </div>
        </div>
        <div class="wx-metric">
          <div class="wx-metric-lbl">Precip</div>
          <div class="wx-metric-val">${Math.round(wx.precip_pct || 0)}%</div>
        </div>
      </div>
      ${rightBlock}
    </div>`;
  }

  // ---- per-row chip attachment ----------------------------------------
  function parseMatchupFromRow(row) {
    const strong = row.querySelector('td strong');
    if (!strong) return null;
    let raw = (strong.textContent || '').trim();
    if (!raw || raw === '—') return null;
    // Detect doubleheader G1/G2 via the "(G2 of 3)"-style suffix.
    // Series-game tags like "(G2 of 3)" don't reliably indicate DH; the
    // schedule lookup will use bare key first and we'll only override
    // when we see explicit DH-distinguishing markers from MLB.
    raw = raw.replace(/\s*\([^)]*\)\s*$/, '').trim();
    const m = raw.match(/^([A-Z]{2,3})\s+@\s+([A-Z]{2,3})$/);
    if (!m) return null;
    return { away: m[1], home: m[2], strong: strong };
  }

  function renderRowChip(span, meta, wx) {
    const fresh = span.querySelector('.wx-chip');
    if (!fresh) return;
    fresh.outerHTML = buildChipHTML(wx, meta);
    const re = span.querySelector('.wx-chip');
    if (re) {
      re.__stadiumMeta = meta;
      re.__wxData = wx;
    }
  }

  function renderHud(mount, meta, wx, scheduleEntry) {
    mount.innerHTML = buildHudHTML(wx, meta, scheduleEntry);
    const hud = mount.querySelector('.wx-hud');
    if (hud) {
      hud.__stadiumMeta = meta;
      hud.__wxData = wx;
      hud.__schedEntry = scheduleEntry;
    }
  }

  function attachRowChip(row) {
    if (row.__wxAttached) return;
    const parsed = parseMatchupFromRow(row);
    if (!parsed) return;
    row.__wxAttached = true;

    const meta = (STADIUMS && STADIUMS[parsed.home]) ? STADIUMS[parsed.home] : null;
    if (!meta) return;

    const span = document.createElement('span');
    span.className = 'wx-row-chip';
    parsed.strong.parentNode.appendChild(span);

    if (meta.is_indoor) {
      span.innerHTML = buildIndoorChip(meta);
      // attach HUD too
      const detailRow = row.nextElementSibling;
      if (detailRow && detailRow.classList.contains('details-row')) {
        attachDetailHud(detailRow, parsed, meta, null);
      }
      return;
    }

    span.innerHTML = '<span class="wx-chip wx-loading">…</span>';
    span.__lastMeta = meta;
    row.__wxSpan = span;
    row.__wxParsed = parsed;

    Promise.all([fetchTodaySchedule(), fetchStadiumWeatherFull(meta.lat, meta.lon)])
      .then(([schedule, meteo]) => {
        const matchupKey = `${parsed.away}@${parsed.home}`;
        const sched = schedule[matchupKey] || null;
        const targetIso = targetIsoForGame(sched);
        const wx = extractWeatherAt(meteo, targetIso);
        row.__wxSched = sched;
        row.__wxData = wx;
        renderRowChip(span, meta, wx);

        const detailRow = row.nextElementSibling;
        if (detailRow && detailRow.classList.contains('details-row')) {
          attachDetailHud(detailRow, parsed, meta, sched);
        }
      })
      .catch(() => {
        const fresh = span.querySelector('.wx-chip');
        if (fresh) fresh.classList.remove('wx-loading');
      });
  }

  function attachDetailHud(detailRow, parsed, meta, sched) {
    if (detailRow.__wxHudAttached) return;
    detailRow.__wxHudAttached = true;
    const td = detailRow.querySelector('td');
    if (!td) return;

    const mount = document.createElement('div');
    mount.className = 'wx-hud-mount';
    td.insertBefore(mount, td.firstChild);
    detailRow.__wxMount = mount;

    if (meta.is_indoor) {
      renderHud(mount, meta, null, sched);
      return;
    }

    mount.innerHTML = buildHudHTML(null, meta, sched);

    Promise.all([
      sched ? Promise.resolve(sched) : fetchTodaySchedule().then(s => s[`${parsed.away}@${parsed.home}`] || null),
      fetchStadiumWeatherFull(meta.lat, meta.lon),
    ]).then(([schedEntry, meteo]) => {
      const targetIso = targetIsoForGame(schedEntry);
      const wx = extractWeatherAt(meteo, targetIso);
      renderHud(mount, meta, wx, schedEntry);
    }).catch(() => {
      mount.innerHTML = '';
    });
  }

  function rerenderAllChips() {
    // Re-render unit display on every existing chip + HUD from cached data.
    document.querySelectorAll('.wx-row-chip').forEach(span => {
      const chip = span.querySelector('.wx-chip');
      if (!chip || !chip.__wxData) return;
      renderRowChip(span, chip.__stadiumMeta, chip.__wxData);
    });
    document.querySelectorAll('.wx-hud-mount').forEach(mount => {
      const hud = mount.querySelector('.wx-hud');
      if (!hud || !hud.__wxData) return;
      renderHud(mount, hud.__stadiumMeta, hud.__wxData, hud.__schedEntry);
    });
    initUserChip();  // rebuild header chip with new unit
  }

  // ---- header user-weather chip ---------------------------------------
  async function initUserChip() {
    const host = document.getElementById('wx-user-chip-mount')
              || document.querySelector('header');
    if (!host) return;

    let chip = document.getElementById('wx-user-chip');
    if (!chip) {
      chip = document.createElement('span');
      chip.id = 'wx-user-chip';
      chip.className = 'wx-chip wx-user-chip wx-loading';
      chip.innerHTML = '… local weather';
      const helpBtn = document.getElementById('help-btn');
      if (helpBtn && helpBtn.parentNode) {
        helpBtn.parentNode.insertBefore(chip, helpBtn);
      } else {
        host.appendChild(chip);
      }
    }

    let geo = null;
    try {
      const r = await fetch(IPAPI_URL, { cache: 'no-store' });
      if (r.ok) {
        const j = await r.json();
        if (j.latitude && j.longitude) {
          geo = { lat: +j.latitude, lon: +j.longitude, city: j.city || '' };
        }
      }
    } catch (e) { /* silent */ }

    if (!geo) {
      chip.classList.remove('wx-loading');
      chip.innerHTML = '<span class="wx-loc">local wx unavail</span>';
      return;
    }

    try {
      const meteo = await fetchStadiumWeatherFull(geo.lat, geo.lon);
      const wx = extractWeatherAt(meteo, null);  // always current for user
      const wmo = WX_CODE_MAP[wx.weather_code] || { icon: 'cld', label: 'Unknown', sev: 1 };
      const icon = ICONS[wmo.icon] || ICONS.cld;
      const cityLabel = geo.city ? `<span class="wx-loc">${geo.city}</span>` : '';
      const precipHtml = (wx.precip_pct >= 10) ? ` <span class="wx-precip">${Math.round(wx.precip_pct)}%</span>` : '';
      chip.classList.remove('wx-loading');
      chip.className = 'wx-chip wx-user-chip' + (wmo.sev >= 2 ? ` wx-sev-${wmo.sev}` : '');
      chip.innerHTML = icon + cityLabel + ' '
        + `<span class="wx-temp">${fmtTemp(wx.temp_c)}</span>`
        + precipHtml
        + ` <button class="wx-unit-toggle" data-wx-unit-toggle="1">${currentUnit === 'F' ? '°F' : '°C'}</button>`;
      chip.title = (geo.city || 'Your location') + ` · ${wmo.label}, ${fmtTemp(wx.temp_c)}`;
    } catch (e) {
      chip.classList.remove('wx-loading');
      chip.innerHTML = '<span class="wx-loc">local wx err</span>';
    }
  }

  // ---- unit-toggle delegation -----------------------------------------
  document.addEventListener('click', function (e) {
    const tgt = e.target;
    if (tgt && tgt.matches && tgt.matches('[data-wx-unit-toggle]')) {
      e.stopPropagation();
      setUnit(currentUnit === 'F' ? 'C' : 'F');
    }
  }, true);

  // ---- bootstrap -------------------------------------------------------
  function injectStyles() {
    if (document.getElementById('wx-styles')) return;
    const s = document.createElement('style');
    s.id = 'wx-styles';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function scanSlate() {
    if (!STADIUMS) return;
    document.querySelectorAll('tr.row-clickable').forEach(attachRowChip);
  }

  function watchSlate() {
    const slateMount = document.getElementById('main-slate-anchor') || document.body;
    const obs = new MutationObserver(muts => {
      let dirty = false;
      muts.forEach(m => {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches('tr.row-clickable')) dirty = true;
          else if (n.querySelector && n.querySelector('tr.row-clickable')) dirty = true;
        });
      });
      if (dirty) scanSlate();
    });
    obs.observe(slateMount, { childList: true, subtree: true });
  }

  function setupRefresh() {
    setInterval(() => {
      // Invalidate weather cache (schedule has its own TTL).
      FETCH_CACHE.clear();
      // Walk all attached row chips; pull fresh forecasts.
      document.querySelectorAll('tr.row-clickable').forEach(row => {
        const span = row.__wxSpan;
        const parsed = row.__wxParsed;
        if (!span || !parsed) return;
        const meta = span.__lastMeta;
        if (!meta || meta.is_indoor) return;
        Promise.all([fetchTodaySchedule(), fetchStadiumWeatherFull(meta.lat, meta.lon)])
          .then(([schedule, meteo]) => {
            const sched = schedule[`${parsed.away}@${parsed.home}`] || null;
            const targetIso = targetIsoForGame(sched);
            const wx = extractWeatherAt(meteo, targetIso);
            row.__wxSched = sched;
            row.__wxData = wx;
            renderRowChip(span, meta, wx);

            const detailRow = row.nextElementSibling;
            const mount = detailRow && detailRow.__wxMount;
            if (mount) renderHud(mount, meta, wx, sched);
          })
          .catch(() => {});
      });
      initUserChip();
    }, REFRESH_MS);
  }

  function boot() {
    injectStyles();
    initUserChip();
    loadStadiums().then(() => {
      scanSlate();
      watchSlate();
      setupRefresh();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.__wx = {
    setUnit,
    getUnit: () => currentUnit,
    fetchStadiumWeatherFull,
    fetchTodaySchedule,
    extractWeatherAt,
    FETCH_CACHE,
    SCHEDULE_CACHE: () => SCHEDULE_CACHE,
  };
})();
firstChild);
    detailRow.__wxMount = mount;

    if (meta.is_indoor) {
      renderHud(mount, meta, null, sched);
      return;
    }

    mount.innerHTML = buildHudHTML(null, meta, sched);

    Promise.all([
      sched ? Promise.resolve(sched) : fetchTodaySchedule().then(s => s[`${parsed.away}@${parsed.home}`] || null),
      fetchStadiumWeatherFull(meta.lat, meta.lon),
    ]).then(([schedEntry, meteo]) => {
      const targetIso = targetIsoForGame(schedEntry);
      const wx = extractWeatherAt(meteo, targetIso);
      renderHud(mount, meta, wx, schedEntry);
    }).catch(() => {
      mount.innerHTML = '';
    });
  }

  function rerenderAllChips() {
    document.querySelectorAll('.wx-row-chip').forEach(span => {
      const chip = span.querySelector('.wx-chip');
      if (!chip || !chip.__wxData) return;
      renderRowChip(span, chip.__stadiumMeta, chip.__wxData);
    });
    document.querySelectorAll('.wx-hud-mount').forEach(mount => {
      const hud = mount.querySelector('.wx-hud');
      if (!hud || !hud.__wxData) return;
      renderHud(mount, hud.__stadiumMeta, hud.__wxData, hud.__schedEntry);
    });
    initUserChip();
  }

  // ---- header user-weather chip ---------------------------------------
  async function initUserChip() {
    const host = document.getElementById('wx-user-chip-mount')
              || document.querySelector('header');
    if (!host) return;

    let chip = document.getElementById('wx-user-chip');
    if (!chip) {
      chip = document.createElement('span');
      chip.id = 'wx-user-chip';
      chip.className = 'wx-chip wx-user-chip wx-loading';
      chip.innerHTML = '… local weather';
      const helpBtn = document.getElementById('help-btn');
      if (helpBtn && helpBtn.parentNode) {
        helpBtn.parentNode.insertBefore(chip, helpBtn);
      } else {
        host.appendChild(chip);
      }
    }

    let geo = null;
    try {
      const r = await fetch(IPAPI_URL, { cache: 'no-store' });
      if (r.ok) {
        const j = await r.json();
        if (j.latitude && j.longitude) {
          geo = { lat: +j.latitude, lon: +j.longitude, city: j.city || '' };
        }
      }
    } catch (e) { /* silent */ }

    if (!geo) {
      chip.classList.remove('wx-loading');
      chip.innerHTML = '<span class="wx-loc">local wx unavail</span>';
      return;
    }

    try {
      const meteo = await fetchStadiumWeatherFull(geo.lat, geo.lon);
      const wx = extractWeatherAt(meteo, null);
      const wmo = WX_CODE_MAP[wx.weather_code] || { icon: 'cld', label: 'Unknown', sev: 1 };
      const icon = ICONS[wmo.icon] || ICONS.cld;
      const cityLabel = geo.city ? `<span class="wx-loc">${geo.city}</span>` : '';
      const precipHtml = (wx.precip_pct >= 10) ? ` <span class="wx-precip">${Math.round(wx.precip_pct)}%</span>` : '';
      chip.classList.remove('wx-loading');
      chip.className = 'wx-chip wx-user-chip' + (wmo.sev >= 2 ? ` wx-sev-${wmo.sev}` : '');
      chip.innerHTML = icon + cityLabel + ' '
        + `<span class="wx-temp">${fmtTemp(wx.temp_c)}</span>`
        + precipHtml
        + ` <button class="wx-unit-toggle" data-wx-unit-toggle="1">${currentUnit === 'F' ? '°F' : '°C'}</button>`;
      chip.title = (geo.city || 'Your location') + ` · ${wmo.label}, ${fmtTemp(wx.temp_c)}`;
    } catch (e) {
      chip.classList.remove('wx-loading');
      chip.innerHTML = '<span class="wx-loc">local wx err</span>';
    }
  }

  document.addEventListener('click', function (e) {
    const tgt = e.target;
    if (tgt && tgt.matches && tgt.matches('[data-wx-unit-toggle]')) {
      e.stopPropagation();
      setUnit(currentUnit === 'F' ? 'C' : 'F');
    }
  }, true);

  function injectStyles() {
    if (document.getElementById('wx-styles')) return;
    const s = document.createElement('style');
    s.id = 'wx-styles';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function scanSlate() {
    if (!STADIUMS) return;
    document.querySelectorAll('tr.row-clickable').forEach(attachRowChip);
  }

  function watchSlate() {
    const slateMount = document.getElementById('main-slate-anchor') || document.body;
    const obs = new MutationObserver(muts => {
      let dirty = false;
      muts.forEach(m => {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches('tr.row-clickable')) dirty = true;
          else if (n.querySelector && n.querySelector('tr.row-clickable')) dirty = true;
        });
      });
      if (dirty) scanSlate();
    });
    obs.observe(slateMount, { childList: true, subtree: true });
  }

  function setupRefresh() {
    setInterval(() => {
      FETCH_CACHE.clear();
      document.querySelectorAll('tr.row-clickable').forEach(row => {
        const span = row.__wxSpan;
        const parsed = row.__wxParsed;
        if (!span || !parsed) return;
        const meta = span.__lastMeta;
        if (!meta || meta.is_indoor) return;
        Promise.all([fetchTodaySchedule(), fetchStadiumWeatherFull(meta.lat, meta.lon)])
          .then(([schedule, meteo]) => {
            const sched = schedule[`${parsed.away}@${parsed.home}`] || null;
            const targetIso = targetIsoForGame(sched);
            const wx = extractWeatherAt(meteo, targetIso);
            row.__wxSched = sched;
            row.__wxData = wx;
            renderRowChip(span, meta, wx);
            const detailRow = row.nextElementSibling;
            const mount = detailRow && detailRow.__wxMount;
            if (mount) renderHud(mount, meta, wx, sched);
          })
          .catch(() => {});
      });
      initUserChip();
    }, REFRESH_MS);
  }

  function boot() {
    injectStyles();
    initUserChip();
    loadStadiums().then(() => {
      scanSlate();
      watchSlate();
      setupRefresh();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.__wx = {
    setUnit,
    getUnit: () => currentUnit,
    fetchStadiumWeatherFull,
    fetchTodaySchedule,
    extractWeatherAt,
    FETCH_CACHE,
    SCHEDULE_CACHE: () => SCHEDULE_CACHE,
  };
})();
) => {
      scanSlate();
      watchSlate();
      setupRefresh();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.__wx = {
    setUnit,
    getUnit: () => currentUnit,
    fetchStadiumWeatherFull,
    fetchTodaySchedule,
    extractWeatherAt,
    FETCH_CACHE,
    SCHEDULE_CACHE: () => SCHEDULE_CACHE,
  };
})();
