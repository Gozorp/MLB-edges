// Health check — returns deployment metadata so you can verify the live site
// is serving the commit you expect.  Hit /api/health on the deployed Pages site.
export async function onRequestGet(context) {
  const env = context.env || {};
  return new Response(JSON.stringify({
    status: "ok",
    timestamp: new Date().toISOString(),
    commit: env.CF_PAGES_COMMIT_SHA || "unknown",
    branch: env.CF_PAGES_BRANCH || "unknown",
    region: (context.request.cf && context.request.cf.colo) || "unknown",
  }, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
