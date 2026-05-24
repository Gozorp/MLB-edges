// /api/claude/health
// Reports whether the dashboard's AI-augmented narrative is wired up on
// this deployment. The dashboard polls this on page load and only shows
// "Ask Claude" + Claude-take footers when this returns enabled:true.
//
// Enabled iff the ANTHROPIC_API_KEY secret is set on the Pages env.
// Add the secret in: Cloudflare dashboard -> Pages -> mlb-edges project
// -> Settings -> Environment variables -> Production -> add
//   ANTHROPIC_API_KEY = sk-ant-...
// then redeploy. No code change needed to flip it on.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function onRequestGet(context) {
  const env = context.env || {};
  const enabled = !!env.ANTHROPIC_API_KEY;
  return new Response(
    JSON.stringify({
      enabled,
      model: enabled ? (env.ANTHROPIC_MODEL || "claude-opus-4-6") : null,
      max_tokens: enabled ? Number(env.ANTHROPIC_MAX_TOKENS || 1500) : null,
      commit: env.CF_PAGES_COMMIT_SHA || "unknown",
    }),
    {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        ...CORS,
      },
    }
  );
}

export async function onRequestOptions() {
  return new Response(null, {status: 204, headers: CORS});
}
