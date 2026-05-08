// /api/today — returns the most recent slate as parsed JSON.  Useful for any
// programmatic consumer (Slack bot, mobile app, scripts) that wants the slate
// without parsing CSV.  Reads docs/data/manifest.json to find the newest date,
// then parses picks_*_diag.csv into a structured JSON response.
//
// Optional ?date=YYYY-MM-DD overrides the auto-pick.

function splitCSVLine(line) {
  // Handles quoted fields (e.g. "F2_xwoba_gap=0.038, F3_swing_take_gap=7145.0")
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
  const headers = splitCSVLine(lines[0]);
  return lines.slice(1).map(line => {
    const cols = splitCSVLine(line);
    return Object.fromEntries(headers.map((h, i) => [h.trim(), (cols[i] || "").trim()]));
  });
}

const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  let date = url.searchParams.get("date");

  // 1. Resolve which date to serve (latest from manifest, or the override).
  if (!date || !ISO_RE.test(date)) {
    const manifestUrl = new URL("/data/manifest.json", context.request.url);
    const mResp = await fetch(manifestUrl);
    if (!mResp.ok) {
      return new Response(JSON.stringify({error: "manifest fetch failed", status: mResp.status}),
        {status: 502, headers: {"Content-Type": "application/json"}});
    }
    const manifest = await mResp.json();
    date = manifest.dates && manifest.dates[0];
    if (!date) {
      return new Response(JSON.stringify({error: "no slates available"}),
        {status: 404, headers: {"Content-Type": "application/json"}});
    }
  }

  // 2. Fetch the diag CSV for that date.
  const csvUrl = new URL(`/data/picks_${date}_diag.csv`, context.request.url);
  const cResp = await fetch(csvUrl);
  if (!cResp.ok) {
    return new Response(JSON.stringify({error: "slate CSV not found", date, status: cResp.status}),
      {status: 404, headers: {"Content-Type": "application/json"}});
  }
  const rows = parseCSV(await cResp.text());

  // 3. Return as JSON with a 5-minute browser cache and 60-second edge cache
  //    (long enough that hot reloads aren't slammed, short enough that fresh
  //    cron-bake commits propagate quickly).
  return new Response(JSON.stringify({
    date,
    games: rows.length,
    rows,
  }), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=300, s-maxage=60",
    },
  });
}
