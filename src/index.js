// =============================================================================
// src/index.js — Cloudflare Workers entrypoint
// =============================================================================
// Single worker that handles /api/* routes for the mlb_edge dashboard.
// Static files (docs/index.html, docs/data/*.csv, etc.) are served directly
// by Cloudflare's static-asset layer (configured in wrangler.toml [assets]).
// This worker is invoked only when no static file matches the request path.
//
// Routes:
//   GET /api/health              — deployment metadata
//   GET /api/today               — latest slate as JSON (?date=YYYY-MM-DD optional)
//   GET /api/mlb/{path...}       — edge-cached proxy for statsapi.mlb.com
// =============================================================================

const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;

function jsonResponse(data, init = {}) {
  return new Response(JSON.stringify(data, null, 2), {
    status: init.status || 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": init.cache || "no-store",
      ...(init.headers || {}),
    },
  });
}

// ---------- /api/health ------------------------------------------------------
async function handleHealth(request, env) {
  return jsonResponse({
    status: "ok",
    timestamp: new Date().toISOString(),
    commit: env.CF_PAGES_COMMIT_SHA || env.CF_VERSION_METADATA?.id || "unknown",
    region: (request.cf && request.cf.colo) || "unknown",
  });
}

// ---------- /api/today -------------------------------------------------------
function splitCSVLine(line) {
  // CSV split that respects double-quoted fields (signals column has commas).
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') { inQuotes = !inQuotes; continue; }
    if (ch === "," && !inQuotes) { out.push(cur); cur = ""; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}

function parseCSV(text) {
  const lines = text.replace(/\r/g, "").trim().split("\n");
  if (lines.length < 2) return [];
  const headers = splitCSVLine(lines[0]).map(h => h.trim());
  return lines.slice(1).map(line => {
    const cols = splitCSVLine(line);
    const o = {};
    for (let i = 0; i < headers.length; i++) {
      o[headers[i]] = (cols[i] || "").trim();
    }
    return o;
  });
}

async function handleToday(request, env) {
  const url = new URL(request.url);
  let date = url.searchParams.get("date");

  // Resolve date — either ?date= override or latest from manifest
  if (!date || !ISO_RE.test(date)) {
    const mResp = await env.ASSETS.fetch(new URL("/data/manifest.json", url).toString());
    if (!mResp.ok) {
      return jsonResponse({ error: "manifest fetch failed", status: mResp.status }, { status: 502 });
    }
    const manifest = await mResp.json();
    date = manifest.dates && manifest.dates[0];
    if (!date) {
      return jsonResponse({ error: "no slates available" }, { status: 404 });
    }
  }

  // Fetch the diag CSV for that date through the static-asset binding
  const csvUrl = new URL(`/data/picks_${date}_diag.csv`, url).toString();
  const cResp = await env.ASSETS.fetch(csvUrl);
  if (!cResp.ok) {
    return jsonResponse({ error: "slate CSV not found", date, status: cResp.status }, { status: 404 });
  }
  const rows = parseCSV(await cResp.text());

  return jsonResponse({ date, games: rows.length, rows }, {
    cache: "public, max-age=300, s-maxage=60",
  });
}

// ---------- /api/mlb/* -------------------------------------------------------
const MLB_UPSTREAM = "https://statsapi.mlb.com/api/";

function cacheSecondsFor(path) {
  if (path.includes("/feed/live"))         return 30;
  if (path.includes("/winProbability"))    return 30;
  if (path.includes("/playByPlay"))        return 30;
  if (path.includes("/boxscore"))          return 60;
  if (path.includes("/schedule"))          return 300;
  if (path.includes("/teams/") && path.includes("/roster")) return 3600;
  if (path.includes("/people/"))           return 3600;
  return 60;
}

async function handleMlbProxy(request, env, ctx) {
  const url = new URL(request.url);
  // Strip the /api/mlb/ prefix to get the upstream path
  const subpath = url.pathname.replace(/^\/api\/mlb\/?/, "");
  const upstreamUrl = `${MLB_UPSTREAM}${subpath}${url.search}`;
  const ttl = cacheSecondsFor(subpath);

  // Cloudflare edge cache
  const cache = caches.default;
  const cacheKey = new Request(upstreamUrl, { method: "GET" });
  let resp = await cache.match(cacheKey);

  if (!resp) {
    let upstreamResp;
    try {
      upstreamResp = await fetch(upstreamUrl, {
        headers: {
          "Accept": "application/json",
          "User-Agent": "mlb_edge-worker/1.0 (+https://github.com/Gozorp/MLB-edges)",
        },
      });
    } catch (e) {
      return jsonResponse({ error: "upstream fetch failed", message: String(e) }, { status: 502 });
    }
    resp = new Response(upstreamResp.body, upstreamResp);
    resp.headers.set("Cache-Control", `public, s-maxage=${ttl}, max-age=${ttl}`);
    resp.headers.set("X-Mlb-Edge-Proxy", "1");
    if (resp.ok) {
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
    }
  }

  const out = new Response(resp.body, resp);
  out.headers.set("Access-Control-Allow-Origin", "*");
  return out;
}

// ---------- main fetch handler -----------------------------------------------
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/api/health") return handleHealth(request, env);
    if (path === "/api/today")  return handleToday(request, env);
    if (path.startsWith("/api/mlb/")) return handleMlbProxy(request, env, ctx);

    // Fall through: ask the static-asset binding to serve whatever's at this
    // path.  In practice this only fires when run_worker_first is true, since
    // by default Cloudflare serves assets before invoking the worker.  Kept
    // here as a safety net / for clarity.
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }
    return new Response("Not Found", { status: 404 });
  },
};
