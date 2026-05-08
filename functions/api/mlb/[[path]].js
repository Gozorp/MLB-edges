// /api/mlb/* — edge-cached proxy for the MLB Stats API.
//
// Why: hitting statsapi.mlb.com directly from the dashboard means every
// visitor's browser fires the same request to MLB, which is slow on cold tabs
// and occasionally rate-limited.  Routing through this Worker:
//   1. Caches at Cloudflare's edge (60s default; tunable per-endpoint below)
//   2. Avoids any future CORS/origin issues
//   3. Lets us add request shaping (timeouts, retry, header tweaks) in one place
//
// Usage from the dashboard JS:
//   fetch('/api/mlb/v1.1/game/824036/feed/live')
//   instead of
//   fetch('https://statsapi.mlb.com/api/v1.1/game/824036/feed/live')
//
// The [[path]] catch-all in the filename means everything after /api/mlb/ is
// passed through as-is, so the entire statsapi surface is available.

const UPSTREAM = "https://statsapi.mlb.com/api/";

// Different endpoints have different staleness tolerance.  Tune here.
function cacheSecondsFor(path) {
  if (path.includes("/feed/live"))         return 30;   // live game state
  if (path.includes("/winProbability"))    return 30;   // updates per pitch
  if (path.includes("/playByPlay"))        return 30;
  if (path.includes("/boxscore"))          return 60;
  if (path.includes("/schedule"))          return 300;  // 5 min — schedule rarely flips
  if (path.includes("/teams/") && path.includes("/roster")) return 3600;  // hourly
  if (path.includes("/people/"))           return 3600; // bio data, hourly
  return 60;  // default
}

export async function onRequestGet(context) {
  const segments = context.params.path;             // string[] from [[path]] catch-all
  const subpath = Array.isArray(segments) ? segments.join("/") : String(segments || "");
  const reqUrl = new URL(context.request.url);
  const upstream = `${UPSTREAM}${subpath}${reqUrl.search}`;

  const ttl = cacheSecondsFor(subpath);

  // Cloudflare's edge cache, keyed on full upstream URL.
  const cache = caches.default;
  const cacheKey = new Request(upstream, { method: "GET" });
  let resp = await cache.match(cacheKey);

  if (!resp) {
    let upstreamResp;
    try {
      upstreamResp = await fetch(upstream, {
        headers: {
          "Accept": "application/json",
          "User-Agent": "mlb_edge-worker/1.0 (+https://github.com/Gozorp/MLB-edges)",
        },
        // 8s timeout via AbortController would be nice; CF Workers support it
        // but we're keeping the proxy simple.  MLB API is generally fast.
      });
    } catch (e) {
      return new Response(JSON.stringify({error: "upstream fetch failed", message: String(e)}),
        {status: 502, headers: {"Content-Type": "application/json"}});
    }

    // Re-wrap to make headers mutable, then cache.
    resp = new Response(upstreamResp.body, upstreamResp);
    resp.headers.set("Cache-Control", `public, s-maxage=${ttl}, max-age=${ttl}`);
    resp.headers.set("X-Mlb-Edge-Proxy", "1");
    if (resp.ok) {
      // Only cache successful responses; let errors retry on next call.
      context.waitUntil(cache.put(cacheKey, resp.clone()));
    }
  }

  // Always set permissive CORS so the proxy is callable from any origin during
  // local dev / sandbox testing.  In production the dashboard is same-origin so
  // CORS is a no-op.
  const out = new Response(resp.body, resp);
  out.headers.set("Access-Control-Allow-Origin", "*");
  return out;
}
