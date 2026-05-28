/* weather.js — live weather HUD for mlb_edge
 *
 * Self-bootstrapping. Loaded as a plain <script src=...> tag from index.html.
 * Hooks into the existing Quant Terminal dashboard without touching renderSlate().
 *
 * Data source: Open-Meteo (https://open-meteo.com/) — free, no key, no rate
 * limits for personal use. Returns current_weather + hourly precipitation
 * probability + hourly wind, with WMO weather codes for the icon mapping.
 *
 * IP geolocation for the header user-chip: ipapi.co (free, no key, returns
 * approximate lat/lon from request IP). Best-effort; failures are silent.
 *
 * Refresh cadence: 15 minutes. Per-stadium cache keyed by (lat, lon)
 * rounded to 3 decimals so same-city games share fetches.
 *
 * Unit toggle (°C / °F): persisted in localStorage as mlb_edge_weather_unit.
 * Default: 'F' (US baseball audience).
 *
 * No external CSS file — styles are injected once at boot. No external JS
 * deps. Matches the Quant Terminal identity: monospace, compact, muted.
 */

(function () {
  'use strict';

  const REFRESH_MS = 15 * 60 * 1000;
  const STADIUM_JSON_URL = 'data/stadium_coords.json';
  const OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast';
  const IPAPI_URL = 'https://ipapi.co/json/';
  const UNIT_LS_KEY = 'mlb_edge_weather_unit';

  // ---- WMO weather code -> { icon, label, severity } -------------------
  // Severity 0 = clear/benign, 1 = cloud/fog, 2 = precip, 3 = severe.
  // Icons are inline SVG strings so we don't need an external sprite.
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

  // SVG icons — 16px viewBox, currentColor strokes. Kept tiny so the row
  // chip stays inline-readable in the monospace table.
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

  // ---- styles injected once at boot ------------------------------------
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
    .wx-row-chip { margin-left: 8px; }

    /* Header user chip — sits beside the visit pill */
    .wx-user-chip {
      cursor: default;
    }
    .wx-user-chip .wx-loc {
      color: var(--text, #c9d1d9);
      font-weight: 500;
    }

    /* Detail-panel HUD — bigger, with wind arrow + precip strip */
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
    .wx-hud-right { text-align: right; font-size: 9px; opacity: 0.5; }

    .wx-unit-toggle {
      cursor: pointer; user-select: none;
      border: 1px solid var(--border, #30363d);
      padding: 1px 4px; border-radius: 2px;
      font-size: 10px; margin-left: 4px;
      background: transparent; color: inherit;
    }
    .wx-unit-toggle:hover { color: var(--text, #c9d1d9); }
  `;

  // ---- shared state ----------------------------------------------------
  let STADIUMS = null;             // loaded once from JSON
  let STADIUMS_PROMISE = null;
  const FETCH_CACHE = new Map();   // 'lat,lon' -> { data, ts }
  const INFLIGHT = new Map();      // 'lat,lon' -> Promise
  let currentUnit = (function () {
    try {
      const v = localStorage.getItem(UNIT_LS_KEY);
      return v === 'C' || v === 'F' ? v : 'F';
    } catch (e) { return 'F'; }
  })();

  function setUnit(u) {
    currentUnit = (u === 'C') ? 'C' : 'F';
    try { localStorage.setItem(UNIT_LS_KEY, currentUnit); } catch (e) {}
    // Re-render every existing chip without re-fetching.
    document.querySelectorAll('.wx-chip[data-wx-key]').forEach(renderChipFromCache);
    document.querySelectorAll('.wx-hud[data-wx-key]').forEach(renderHudFromCache);
  }

  function fmtTemp(celsius) {
    if (celsius == null || Number.isNaN(celsius)) return '—';
    const v = (currentUnit === 'F') ? (celsius * 9 / 5 + 32) : celsius;
    return Math.round(v) + '°' + currentUnit;
  }

  function fmtWindSpeed(kmh) {
    if (kmh == null) return '—';
    // mph for US-default unit, km/h otherwise
    if (currentUnit === 'F') return Math.round(kmh * 0.621371) + ' mph';
    return Math.round(kmh) + ' km/h';
  }

  function dirToCompass(deg) {
    if (deg == null) return '—';
    const pts = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                 'S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return pts[Math.round(deg / 22.5) % 16];
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

  // ---- Open-Meteo fetch -----------------------------------------------
  function cacheKey(lat, lon) {
    return `${lat.toFixed(3)},${lon.toFixed(3)}`;
  }

  function fetchWeather(lat, lon) {
    const key = cacheKey(lat, lon);
    const now = Date.now();
    const cached = FETCH_CACHE.get(key);
    if (cached && (now - cached.ts) < REFRESH_MS) {
      return Promise.resolve(cached.data);
    }
    if (INFLIGHT.has(key)) return INFLIGHT.get(key);

    const url = `${OPEN_METEO_URL}?latitude=${lat}&longitude=${lon}`
      + '&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,precipitation'
      + '&hourly=precipitation_probability'
      + '&forecast_hours=4'
      + '&wind_speed_unit=kmh'
      + '&temperature_unit=celsius'
      + '&timezone=auto';

    const p = fetch(url)
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(j => {
        const cur = j.current || {};
        const hourly = j.hourly || {};
        const popArr = hourly.precipitation_probability || [];
        // Peak precip% over the next 3 forecast hours (hour 0..2 of forecast_hours=4)
        const popNext3 = popArr.slice(0, 3).reduce(
          (a, b) => Math.max(a, b == null ? 0 : b), 0);
        const data = {
          temp_c: cur.temperature_2m,
          weather_code: cur.weather_code,
          wind_kmh: cur.wind_speed_10m,
          wind_dir: cur.wind_direction_10m,
          precip_pct: popNext3,
          fetched_at: Date.now(),
        };
        FETCH_CACHE.set(key, { data, ts: Date.now() });
        INFLIGHT.delete(key);
        return data;
      })
      .catch(err => {
        INFLIGHT.delete(key);
        console.warn('[wx] open-meteo fetch failed', err);
        throw err;
      });

    INFLIGHT.set(key, p);
    return p;
  }

  // ---- chip render helpers --------------------------------------------
  function buildChipHTML(wx, stadiumMeta, opts) {
    opts = opts || {};
    if (!wx) return '<span class="wx-chip wx-loading">—</span>';
    const wmo = WX_CODE_MAP[wx.weather_code] || { icon: 'cld', label: 'Unknown', sev: 1 };
    const icon = ICONS[wmo.icon] || ICONS.cld;
    const sevClass = wmo.sev >= 2 ? ` wx-sev-${wmo.sev}` : '';
    const tempHtml = `<span class="wx-temp">${fmtTemp(wx.temp_c)}</span>`;
    const precipHtml = (wx.precip_pct >= 10)
      ? ` <span class="wx-precip">${Math.round(wx.precip_pct)}%</span>`
      : '';
    const windHtml = (wx.wind_kmh != null && wx.wind_kmh >= 8)
      ? ` ${dirToCompass(wx.wind_dir)} ${fmtWindSpeed(wx.wind_kmh)}`
      : '';
    const retractBadge = (stadiumMeta && stadiumMeta.is_retractable)
      ? '<span class="wx-roof-badge" title="Retractable roof">⌂</span>' : '';
    const title = (stadiumMeta && stadiumMeta.name ? stadiumMeta.name + ' · ' : '')
      + `${wmo.label}, ${fmtTemp(wx.temp_c)}`
      + (wx.precip_pct >= 10 ? `, ${Math.round(wx.precip_pct)}% precip next 3h` : '')
      + (wx.wind_kmh >= 8 ? `, wind ${dirToCompass(wx.wind_dir)} ${fmtWindSpeed(wx.wind_kmh)}` : '');
    return `<span class="wx-chip${sevClass}" title="${title.replace(/"/g, '&quot;')}">`
      + icon + tempHtml + precipHtml + windHtml + retractBadge + '</span>';
  }

  function buildIndoorChip(stadiumMeta) {
    const stadiumName = stadiumMeta && stadiumMeta.name ? stadiumMeta.name : 'Indoor';
    return `<span class="wx-chip wx-indoor" title="${stadiumName} · indoor / weather not a factor">`
      + ICONS.indoor + '<span>INDOOR</span></span>';
  }

  function renderChipFromCache(el) {
    const key = el.dataset.wxKey;
    if (!key) return;
    const cached = FETCH_CACHE.get(key);
    const stadiumMeta = el.__stadiumMeta || null;
    if (cached && cached.data) {
      el.outerHTML = buildChipHTML(cached.data, stadiumMeta).replace(
        '<span class="wx-chip',
        `<span class="wx-chip" data-wx-key="${key}"`.replace('data-wx-key="" ', '')
      );
      // Re-bind stadium meta on the new element via DOM walk:
      const replaced = document.querySelector(`.wx-chip[data-wx-key="${key}"]`);
      if (replaced) replaced.__stadiumMeta = stadiumMeta;
    }
  }

  // ---- per-row chip attachment ----------------------------------------
  function parseMatchupFromRow(row) {
    // Row's first <strong> child holds the matchup like "HOU @ TEX" or
    // "HOU @ TEX (G2 of 3)".
    const strong = row.querySelector('td strong');
    if (!strong) return null;
    let raw = (strong.textContent || '').trim();
    if (!raw || raw === '—') return null;
    raw = raw.replace(/\s*\([^)]*\)\s*$/, '').trim();
    const m = raw.match(/^([A-Z]{2,3})\s+@\s+([A-Z]{2,3})$/);
    if (!m) return null;
    return { away: m[1], home: m[2], strong: strong };
  }

  function attachRowChip(row) {
    if (row.__wxAttached) return;
    const parsed = parseMatchupFromRow(row);
    if (!parsed) return;
    row.__wxAttached = true;

    const home = parsed.home;
    const meta = (STADIUMS && STADIUMS[home]) ? STADIUMS[home] : null;

    // Build the chip placeholder right next to the matchup label.
    const span = document.createElement('span');
    span.className = 'wx-row-chip';
    if (!meta) {
      span.innerHTML = '';  // unknown abbrev — fail quiet
      return;
    }
    if (meta.is_indoor) {
      span.innerHTML = buildIndoorChip(meta);
      parsed.strong.parentNode.appendChild(span);
      return;
    }
    const key = cacheKey(meta.lat, meta.lon);
    span.innerHTML = `<span class="wx-chip wx-loading" data-wx-key="${key}">…</span>`;
    parsed.strong.parentNode.appendChild(span);
    const placeholder = span.querySelector('.wx-chip');
    placeholder.__stadiumMeta = meta;

    fetchWeather(meta.lat, meta.lon).then(wx => {
      const fresh = span.querySelector('.wx-chip');
      if (!fresh) return;
      fresh.outerHTML = buildChipHTML(wx, meta).replace(
        '<span class="wx-chip',
        `<span class="wx-chip" data-wx-key="${key}"`
      );
      const re = span.querySelector('.wx-chip');
      if (re) re.__stadiumMeta = meta;
    }).catch(() => {
      const fresh = span.querySelector('.wx-chip');
      if (fresh) fresh.classList.remove('wx-loading');
    });

    // Also attach a HUD into the matching details row if it exists.
    const detailRow = row.nextElementSibling;
    if (detailRow && detailRow.classList.contains('details-row')) {
      attachDetailHud(detailRow, parsed, meta);
    }
  }

  function buildHudHTML(wx, meta) {
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
    return `<div class="wx-hud">
      <div class="wx-hud-left">
        <span class="wx-big-icon">${icon}</span>
        <div>
          <div class="wx-condition">${wmo.label}${meta && meta.is_retractable ? ' <span class="wx-roof-badge" title="Retractable roof">⌂</span>' : ''}</div>
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
          <div class="wx-metric-lbl">Precip (next 3h)</div>
          <div class="wx-metric-val">${Math.round(wx.precip_pct || 0)}%</div>
        </div>
      </div>
      <div class="wx-hud-right">refresh · 15m</div>
    </div>`;
  }

  function attachDetailHud(detailRow, parsed, meta) {
    if (detailRow.__wxHudAttached) return;
    detailRow.__wxHudAttached = true;
    const td = detailRow.querySelector('td');
    if (!td) return;

    const mount = document.createElement('div');
    mount.className = 'wx-hud-mount';
    if (!meta) return;
    if (meta.is_indoor) {
      mount.innerHTML = buildHudHTML(null, meta);
      td.insertBefore(mount, td.firstChild);
      return;
    }
    const key = cacheKey(meta.lat, meta.lon);
    mount.innerHTML = buildHudHTML(null, meta);  // loading state
    td.insertBefore(mount, td.firstChild);

    fetchWeather(meta.lat, meta.lon).then(wx => {
      mount.innerHTML = buildHudHTML(wx, meta);
      const hud = mount.querySelector('.wx-hud');
      if (hud) hud.dataset.wxKey = key;
    }).catch(() => {
      mount.innerHTML = '';
    });
  }

  function renderHudFromCache(el) {
    const key = el.dataset.wxKey;
    if (!key) return;
    const cached = FETCH_CACHE.get(key);
    if (!cached || !cached.data) return;
    // Find the parent .wx-hud-mount and re-render
    const mount = el.closest('.wx-hud-mount');
    if (!mount) return;
    // Recover the stadium meta from the parent row's chip
    const detailRow = mount.closest('tr.details-row');
    let meta = null;
    if (detailRow) {
      const prev = detailRow.previousElementSibling;
      const chip = prev && prev.querySelector('.wx-chip[data-wx-key="' + key + '"]');
      if (chip) meta = chip.__stadiumMeta || null;
    }
    mount.innerHTML = buildHudHTML(cached.data, meta);
    const hud = mount.querySelector('.wx-hud');
    if (hud) hud.dataset.wxKey = key;
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
      // Insert before help button so it sits with the other header pills.
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
      const wx = await fetchWeather(geo.lat, geo.lon);
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
      // Re-init user chip text (its unit toggle was inside the chip)
      initUserChip();
    }
  }, true);  // capture so we beat the row-click handler

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
    // The slate gets re-rendered when the user filters or when data refreshes.
    // MutationObserver attaches new chips as rows appear.
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
      // Invalidate the cache so the next render fetches fresh.
      FETCH_CACHE.clear();
      // Re-fetch for visible row chips
      document.querySelectorAll('.wx-chip[data-wx-key]').forEach(el => {
        const meta = el.__stadiumMeta;
        if (!meta) return;
        fetchWeather(meta.lat, meta.lon).then(wx => {
          const updated = buildChipHTML(wx, meta);
          const key = cacheKey(meta.lat, meta.lon);
          el.outerHTML = updated.replace(
            '<span class="wx-chip',
            `<span class="wx-chip" data-wx-key="${key}"`
          );
          const re = document.querySelector(`.wx-chip[data-wx-key="${key}"]`);
          if (re) re.__stadiumMeta = meta;
        }).catch(() => {});
      });
      // Re-fetch user chip
      initUserChip();
    }, REFRESH_MS);
  }

  function boot() {
    injectStyles();
    initUserChip();  // fire-and-forget — geolocation lookup runs in parallel
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

  // Expose for console debugging.
  window.__wx = { setUnit, getUnit: () => currentUnit, fetchWeather, FETCH_CACHE };
})();
