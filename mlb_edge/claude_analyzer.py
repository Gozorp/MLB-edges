"""
mlb_edge/claude_analyzer.py
---------------------------
Wrapper around the Anthropic Messages API for the four mlb_edge use-cases:

    1. postgame(date)         — per-game post-mortem on yesterday's losses
    2. weekly_memo(end_date)  — Sunday review of the prior 7 days
    3. live_commentary(...)   — per-poll commentary on an in-progress game
    4. ask(slate_ctx, q)      — free-form Q&A over the loaded slate

Why a single module: each use case shares the same context-building helpers
(turn a slate row + outcome into a structured JSON payload Claude can read),
the same API call pattern, and the same retry/error handling.  Keeping them
together means we don't duplicate prompt scaffolding.

Configuration
=============
Reads ANTHROPIC_API_KEY from the environment.  If unset, every public method
returns a structured error rather than raising — callers can degrade
gracefully (skip the analysis, still ship the slate).

Model is configurable via ANTHROPIC_MODEL env var (default: claude-opus-4-6
per the user's setup choice on 2026-05-09).

API Reference
=============
    POST https://api.anthropic.com/v1/messages
    headers: x-api-key, anthropic-version, content-type
    body:    {model, max_tokens, system, messages: [{role, content}]}
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_TIMEOUT = 90  # generous — Opus reasoning can take 30-60s per call


@dataclass
class ClaudeResponse:
    ok: bool
    text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


def _get_api_key() -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key or None


def _call_api(system: str, user: str, *,
              model: Optional[str] = None,
              max_tokens: int = 1024,
              retries: int = 2) -> ClaudeResponse:
    """Single Anthropic Messages API call with retries on transient failures."""
    key = _get_api_key()
    if not key:
        return ClaudeResponse(ok=False, error="ANTHROPIC_API_KEY not set")

    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            content = data.get("content", [])
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            usage = data.get("usage", {})
            return ClaudeResponse(
                ok=True, text=text.strip(),
                model=data.get("model", model),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            last_err = Exception(f"HTTP {e.code}: {err_body[:300]}")
            # Don't retry on 4xx (auth, bad request, rate limit needs backoff)
            if 400 <= e.code < 500 and e.code != 429:
                break
            if attempt < retries:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    return ClaudeResponse(ok=False, error=str(last_err))


# ---------------------------------------------------------------------------
# Use-case 1: post-game per-pick analysis
# ---------------------------------------------------------------------------
def postgame_for_pick(slate_row: Dict[str, Any],
                      game_result: Dict[str, Any],
                      *, model: Optional[str] = None) -> ClaudeResponse:
    """Analyze a single completed game where the model's pick can be graded.

    slate_row:
        Row from picks_*_diag.csv — at minimum {matchup, pick, f5_prob,
        full_prob, fair_prob, edge_pp, tier, signals, why_skipped, ...}.
    game_result:
        From MLB statsapi or the dashboard's `results` map: {away, home,
        away_score, home_score, status, winner, statusText, optional
        away_pitcher, home_pitcher, key_plays?}.

    Returns a ClaudeResponse whose .text is a JSON string with shape:
        {
          "verdict": "WIN" | "LOSS" | "PUSH",
          "headline": "<one-line takeaway>",
          "hypothesis": "<paragraph: most likely cause if loss>",
          "signals_to_recheck": ["bp_min", "team_quality_mod", ...]
        }
    """
    system = (
        "You are an MLB betting model post-mortem analyst.  You receive one "
        "completed game's pre-game model output and the actual outcome.  Your "
        "job is to identify in plain language whether the model's pick was "
        "correct, and if it lost, to hypothesize the single most likely cause "
        "from the model's stated signals.\n\n"
        "Always respond with valid JSON only.  Schema:\n"
        '{"verdict": "WIN"|"LOSS"|"PUSH", '
        '"headline": "<one short sentence>", '
        '"hypothesis": "<3-5 sentences explaining the most likely failure '
        'mode if LOSS, or what worked if WIN>", '
        '"signals_to_recheck": ["<csv field name>", ...]}'
    )
    user = json.dumps({
        "slate_row": slate_row,
        "game_result": game_result,
    }, indent=2, default=str)
    return _call_api(system, user, model=model, max_tokens=800)


# ---------------------------------------------------------------------------
# Use-case 2: weekly pattern memo
# ---------------------------------------------------------------------------
def weekly_memo(picks_with_outcomes: List[Dict[str, Any]],
                *, model: Optional[str] = None) -> ClaudeResponse:
    """Review a week of (pick, outcome) pairs and surface persistent patterns.

    Input: list of dicts each containing the slate_row + game_result for
    every graded pick in the past 7 days.

    Returns a ClaudeResponse whose .text is a markdown memo with sections:
      ## What's working
      ## What's losing money
      ## Suggested rule changes
    """
    n = len(picks_with_outcomes)
    wins = sum(1 for r in picks_with_outcomes if r.get("won"))
    system = (
        "You are an MLB betting model auditor.  You have one week of pick "
        "decisions and their outcomes.  Identify three things:\n"
        "  (1) which signals/rules are correlating with wins\n"
        "  (2) which signals/rules are correlating with losses\n"
        "  (3) one or two concrete rule changes worth testing next week\n"
        "Be specific: cite individual game examples by matchup name when "
        "possible.  Output Markdown only.  No JSON."
    )
    user = (f"Week summary: {n} graded picks, {wins} wins, "
            f"{n-wins} losses ({wins/max(1,n):.1%} hit rate).\n\n"
            f"Per-pick details:\n```json\n"
            f"{json.dumps(picks_with_outcomes, indent=2, default=str)}\n```")
    return _call_api(system, user, model=model, max_tokens=2000)


# ---------------------------------------------------------------------------
# Use-case 3: in-game live commentary
# ---------------------------------------------------------------------------
def live_commentary(slate_row: Dict[str, Any],
                    live_state: Dict[str, Any],
                    *, model: Optional[str] = None) -> ClaudeResponse:
    """Comment on whether the pre-game pick still looks right vs live state.

    live_state: {inning, halfInning, awayScore, homeScore,
                 latest_win_prob, peak_win_prob, key_plays_so_far: [...]}

    Returns ClaudeResponse.text — short paragraph (~50 words) suitable for
    rendering inline next to the live tracker.  Plain text, no JSON.
    """
    system = (
        "You are a live MLB game commentator with access to a betting "
        "model's pre-game read.  Given the model's pick + the current game "
        "state, write 2-3 sentences on whether the model's read still looks "
        "right.  No betting advice — just descriptive analysis of how the "
        "game has unfolded relative to the pre-game model.  Plain text only."
    )
    user = json.dumps({
        "pre_game_pick": slate_row,
        "live_state": live_state,
    }, indent=2, default=str)
    return _call_api(system, user, model=model, max_tokens=300)


# ---------------------------------------------------------------------------
# Use-case 4: free-form Q&A over the loaded slate
# ---------------------------------------------------------------------------
def ask(slate_context: Dict[str, Any], question: str,
        *, model: Optional[str] = None) -> ClaudeResponse:
    """Answer a user's question about the loaded slate."""
    system = (
        "You are an MLB betting model analyst answering questions about a "
        "loaded slate.  Be concise (under 150 words unless the user asks "
        "for detail).  Cite specific matchups and numbers from the data.  "
        "Never give a recommendation to bet — analyze and explain only."
    )
    user = (f"Slate context (date {slate_context.get('date','?')}, "
            f"{len(slate_context.get('rows',[]))} games):\n```json\n"
            f"{json.dumps(slate_context, indent=2, default=str)}\n```\n\n"
            f"Question: {question}")
    return _call_api(system, user, model=model, max_tokens=600)
