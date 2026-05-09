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

// ---------- /api/claude/ask --------------------------------------------------
// Free-form Q&A over the loaded slate.  Browser POSTs JSON
// {date: "YYYY-MM-DD", question: "..."} and gets back Claude's answer.
//
// Why server-side: keeps ANTHROPIC_API_KEY off the client.  The key is
// configured as a Cloudflare Worker secret (npx wrangler secret put
// ANTHROPIC_API_KEY).  If unset, returns 503 — the dashboard hides the
// Q&A widget gracefully when /api/claude/health returns disabled.

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION = "2023-06-01";
const DEFAULT_CLAUDE_MODEL = "claude-opus-4-6";

async function callClaude(env, system, user, opts = {}) {
  const key = env.ANTHROPIC_API_KEY;
  if (!key) {
    return { ok: false, error: "ANTHROPIC_API_KEY not configured" };
  }
  const body = {
    model: opts.model || DEFAULT_CLAUDE_MODEL,
    max_tokens: opts.max_tokens || 800,
    system,
    messages: [{ role: "user", content: user }],
  };
  let resp;
  try {
    resp = await fetch(ANTHROPIC_URL, {
      method: "POST",
      headers: {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, error: "fetch failed: " + String(e) };
  }
  if (!resp.ok) {
    const err = await resp.text().catch(() => `HTTP ${resp.status}`);
    return { ok: false, error: `HTTP ${resp.status}: ${err.slice(0, 300)}` };
  }
  const data = await resp.json();
  const text = (data.content || [])
    .filter(b => b.type === "text")
    .map(b => b.text)
    .join("");
  return {
    ok: true,
    text: text.trim(),
    model: data.model,
    input_tokens: data.usage?.input_tokens || 0,
    output_tokens: data.usage?.output_tokens || 0,
  };
}

async function handleClaudeAsk(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ error: "POST required" }, { status: 405 });
  }
  let body;
  try { body = await request.json(); } catch { return jsonResponse({ error: "bad JSON" }, { status: 400 }); }
  const date = body.date;
  const question = (body.question || "").trim();
  if (!question) return jsonResponse({ error: "missing question" }, { status: 400 });
  if (question.length > 500) return jsonResponse({ error: "question too long (>500 chars)" }, { status: 400 });

  // Pull the slate JSON we already serve at /api/today as Claude's context.
  const slateUrl = date
    ? new URL(`/api/today?date=${date}`, request.url).toString()
    : new URL("/api/today", request.url).toString();
  const slateResp = await fetch(slateUrl);
  let slate = null;
  try { slate = await slateResp.json(); } catch { slate = null; }
  if (!slate || !slate.rows) {
    return jsonResponse({ error: "could not load slate context" }, { status: 502 });
  }

  const system =
    "You are an MLB betting model analyst answering questions about a " +
    "loaded slate. Be concise (under 150 words unless the user asks for " +
    "detail). Cite specific matchups and numbers from the data. Never " +
    "give a recommendation to bet — analyze and explain only.";
  const user =
    `Slate context (date ${slate.date}, ${slate.rows.length} games):\n` +
    "```json\n" + JSON.stringify(slate, null, 2).slice(0, 30000) + "\n```\n\n" +
    `Question: ${question}`;

  const r = await callClaude(env, system, user, { max_tokens: 600 });
  if (!r.ok) return jsonResponse({ error: r.error }, { status: 502 });

  return jsonResponse({
    answer: r.text,
    model: r.model,
    tokens: { input: r.input_tokens, output: r.output_tokens },
    slate_date: slate.date,
  }, { cache: "no-store" });
}

// ---------- /api/claude/commentary ------------------------------------------
// In-game live commentary.  Browser POSTs JSON {gamePk, slate_row, live_state}
// and gets back a 2-3-sentence Claude commentary on whether the pre-game pick
// still looks right vs the live state.
async function handleClaudeCommentary(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ error: "POST required" }, { status: 405 });
  }
  let body;
  try { body = await request.json(); } catch { return jsonResponse({ error: "bad JSON" }, { status: 400 }); }
  const slateRow = body.slate_row || {};
  const liveState = body.live_state || {};
  if (!slateRow.matchup) return jsonResponse({ error: "missing slate_row.matchup" }, { status: 400 });

  const system =
    "You are a live MLB game commentator with access to a betting model's " +
    "pre-game read. Given the model's pick + the current game state, write " +
    "2-3 sentences on whether the model's read still looks right. No " +
    "betting advice. Plain text only.";
  const user = JSON.stringify({ pre_game_pick: slateRow, live_state: liveState }, null, 2);

  const r = await callClaude(env, system, user, { max_tokens: 250 });
  if (!r.ok) return jsonResponse({ error: r.error }, { status: 502 });

  return jsonResponse({
    commentary: r.text,
    model: r.model,
    tokens: { input: r.input_tokens, output: r.output_tokens },
  }, { cache: "public, max-age=60, s-maxage=60" });
}

// ---------- /api/claude/health ----------------------------------------------
// Lightweight check the dashboard can hit on load to decide whether to
// render the Q&A widget.  Returns {enabled: bool, model: ...} without
// calling the API itself (so we don't burn tokens on a probe).
async function handleClaudeHealth(request, env) {
  return jsonResponse({
    enabled: !!env.ANTHROPIC_API_KEY,
    model: DEFAULT_CLAUDE_MODEL,
  });
}

// ---------- main fetch handler -----------------------------------------------
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/api/health") return handleHealth(request, env);
    if (path === "/api/today")  return handleToday(request, env);
    if (path.startsWith("/api/mlb/")) return handleMlbProxy(request, env, ctx);

    if (path === "/api/claude/health")     return handleClaudeHealth(request, env);
    if (path === "/api/claude/ask")        return handleClaudeAsk(request, env);
    if (path === "/api/claude/commentary") return handleClaudeCommentary(request, env);

    // Fall through: ask the static-asset binding to serve whatever's at this
    // path.  In practice this only fires when run_worker_first is true, since
    // by default Cloudflare serves assets before invoking the worker.
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }
    return new Response("Not Found", { status: 404 });
  },
};
