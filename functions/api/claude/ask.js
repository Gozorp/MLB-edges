// /api/claude/ask
// POST {date: "YYYY-MM-DD" | null, question: "..."} -> {answer: "..."} | {error}
//
// Server-side proxy to Anthropic's Messages API so the dashboard can
// stream the user's API key from a Pages env var instead of shipping it
// in client JS. Loads minimal slate context for the requested date
// (picks_<date>_diag.csv via raw.githubusercontent.com) so Claude has
// the actual numbers to ground its answer.
//
// Setup: see /api/claude/health.js header comment for ANTHROPIC_API_KEY.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const SYSTEM_PROMPT = `You are the mlb_edge model's analyst. You receive a sports-betting model's daily picks + a user question.
Your job: explain the model's reasoning in 2-4 short paragraphs, ground every claim in the numbers provided, and flag where the model is most exposed.
Tone: precise, no hype. Use the exact numbers (probabilities, edges, K/9, etc.) when you reference them. Acknowledge counter-signals.
Hard rules:
- Never advise the user to make a specific bet. Frame insights as "the model thinks..." / "the gap suggests...".
- If the data doesn't support an answer, say so plainly.
- Keep responses under ~400 words.`;

const REPO_RAW_BASE = "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/docs/data/";

async function loadSlateContext(date) {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  try {
    const csvR = await fetch(REPO_RAW_BASE + `picks_${date}_diag.csv`, {
      cf: {cacheTtl: 60},
    });
    if (!csvR.ok) return null;
    const csv = await csvR.text();
    // Trim CSV to first ~25 rows so we stay well under context budget.
    const lines = csv.split(/\r?\n/).slice(0, 26);
    return lines.join("\n");
  } catch (_) {
    return null;
  }
}

export async function onRequestPost(context) {
  const env = context.env || {};
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return new Response(JSON.stringify({error: "ANTHROPIC_API_KEY not configured"}), {
      status: 503,
      headers: {"Content-Type": "application/json", ...CORS},
    });
  }

  let body;
  try {
    body = await context.request.json();
  } catch (_) {
    return new Response(JSON.stringify({error: "Invalid JSON body"}), {
      status: 400,
      headers: {"Content-Type": "application/json", ...CORS},
    });
  }

  const date = body && body.date ? String(body.date).slice(0, 10) : null;
  const question = body && body.question ? String(body.question).slice(0, 4000) : "";
  if (!question) {
    return new Response(JSON.stringify({error: "Missing question"}), {
      status: 400,
      headers: {"Content-Type": "application/json", ...CORS},
    });
  }

  const slateCtx = await loadSlateContext(date);
  const ctxBlock = slateCtx
    ? `\n\nSlate context (CSV head for ${date}):\n\`\`\`\n${slateCtx}\n\`\`\`\n`
    : "";

  const userMsg =
    `Question from a user about today's mlb_edge slate${date ? ` (${date})` : ""}:` +
    `\n\n${question}${ctxBlock}`;

  const model = env.ANTHROPIC_MODEL || "claude-opus-4-6";
  const maxTokens = Number(env.ANTHROPIC_MAX_TOKENS || 1500);

  let resp;
  try {
    resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model,
        max_tokens: maxTokens,
        system: SYSTEM_PROMPT,
        messages: [{role: "user", content: userMsg}],
      }),
    });
  } catch (e) {
    return new Response(JSON.stringify({error: "Upstream fetch failed: " + (e && e.message)}), {
      status: 502,
      headers: {"Content-Type": "application/json", ...CORS},
    });
  }

  if (!resp.ok) {
    const errText = await resp.text().catch(() => "");
    return new Response(
      JSON.stringify({error: `Anthropic API error ${resp.status}: ${errText.slice(0, 200)}`}),
      {status: 502, headers: {"Content-Type": "application/json", ...CORS}}
    );
  }

  const j = await resp.json();
  const blocks = Array.isArray(j.content) ? j.content : [];
  const answer = blocks
    .filter(b => b && b.type === "text" && typeof b.text === "string")
    .map(b => b.text)
    .join("\n\n")
    .trim();

  return new Response(
    JSON.stringify({
      answer,
      model: j.model || model,
      usage: j.usage || null,
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
