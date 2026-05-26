#!/usr/bin/env python3
"""
_patch_anthropic_probe.py
==========================
Commit 2 of the health-check expansion. Adds the single remaining
check: anthropic_api_probe (HTTPS GET to /api/claude/health, asserts
enabled:true and model is the expected version).

kalshi_divergence was dropped from Commit 2 entirely. Probe finding
on 2026-05-25: OddsAPI subscription cancelled 2026-05-21, so the
"divergence between Kalshi and OddsAPI fair_prob" check is unbuildable
against the current pipeline (and a Kalshi-vs-ESPN proxy was rejected
on cost/value grounds — see chat log).

Three small edits to tools/health_check.py:
  1. CHECK_CATEGORIES dict: add anthropic_api_probe -> CAT_DEPLOYMENT
  2. New check_anthropic_api_probe function inserted between
     check_cloudflare_deploy_freshness and check_runaway_ceiling_alarm
  3. CHECKS list: add check_anthropic_api_probe right after
     check_cloudflare_deploy_freshness (keeps deployment-category
     checks adjacent)

No schema bump. Schema v2 from Commit 1 already supports arbitrary
new checks under existing categories.

Per locked memory: bash + Python str.replace; no Edit tool.
"""
from __future__ import annotations
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SCRIPT = REPO / "tools" / "health_check.py"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# ---------------------------------------------------------------------------
# Edit 1: CHECK_CATEGORIES dict — add anthropic_api_probe under deployment
# ---------------------------------------------------------------------------
must_replace(
    SCRIPT,
    '    # deployment\n'
    '    "cloudflare_deploy_freshness": CAT_DEPLOYMENT,\n',
    '    # deployment\n'
    '    "cloudflare_deploy_freshness": CAT_DEPLOYMENT,\n'
    '    "anthropic_api_probe":         CAT_DEPLOYMENT,\n',
    "1/3: register anthropic_api_probe in CHECK_CATEGORIES",
)


# ---------------------------------------------------------------------------
# Edit 2: insert check_anthropic_api_probe function definition
# ---------------------------------------------------------------------------
# Anchor: end of check_cloudflare_deploy_freshness + blank lines + next def.
# The closing return + the next def is uniquely positioned in the file.
must_replace(
    SCRIPT,
    '            "message": f"deployed {age:.1f}h ago "\n'
    '                       f"({deployed_sha[:8]})",\n'
    '            "detail": detail}\n'
    '\n'
    '\n'
    'def check_runaway_ceiling_alarm(now: datetime) -> Dict:\n',
    '            "message": f"deployed {age:.1f}h ago "\n'
    '                       f"({deployed_sha[:8]})",\n'
    '            "detail": detail}\n'
    '\n'
    '\n'
    'def check_anthropic_api_probe(now: datetime) -> Dict:\n'
    '    """HTTPS GET to /api/claude/health. Asserts the Pages deployment\n'
    '    has the ANTHROPIC_API_KEY env var set (enabled:true) and is on\n'
    '    the expected model. A missing key silently disables Deep Analysis\n'
    '    on the dashboard, which is exactly the kind of failure that\n'
    '    needs proactive paging rather than waiting for a user to notice.\n'
    '    """\n'
    '    name = "anthropic_api_probe"\n'
    '    expected_model = "claude-opus-4-6"\n'
    '    try:\n'
    '        req = urllib.request.Request(\n'
    '            f"{PAGES_BASE_URL}/api/claude/health",\n'
    '            headers={"User-Agent": "mlb-edge-health-check/1"},\n'
    '        )\n'
    '        with urllib.request.urlopen(req, timeout=10) as r:\n'
    '            body = json.loads(r.read().decode("utf-8"))\n'
    '    except (urllib.error.URLError, urllib.error.HTTPError, OSError,\n'
    '            json.JSONDecodeError) as e:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": f"/api/claude/health unreachable: "\n'
    '                           f"{type(e).__name__}",\n'
    '                "detail": {"pages_url": PAGES_BASE_URL,\n'
    '                           "error": str(e)[:200]}}\n'
    '    enabled = bool(body.get("enabled"))\n'
    '    model = (body.get("model") or "").strip()\n'
    '    detail = {"pages_url": PAGES_BASE_URL,\n'
    '              "enabled": enabled, "model": model,\n'
    '              "max_tokens": body.get("max_tokens"),\n'
    '              "deployed_commit": (body.get("commit")\n'
    '                                  or "unknown")[:12]}\n'
    '    if not enabled:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": "ANTHROPIC_API_KEY not set on Pages env "\n'
    '                           "(Deep Analysis disabled)",\n'
    '                "detail": detail}\n'
    '    if model != expected_model:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": f"model is \'{model}\', expected "\n'
    '                           f"\'{expected_model}\'",\n'
    '                "detail": detail}\n'
    '    return {"name": name, "severity": GREEN,\n'
    '            "message": f"enabled, model={model}",\n'
    '            "detail": detail}\n'
    '\n'
    '\n'
    'def check_runaway_ceiling_alarm(now: datetime) -> Dict:\n',
    "2/3: insert check_anthropic_api_probe function",
)


# ---------------------------------------------------------------------------
# Edit 3: CHECKS list — register the new function right after cloudflare
# ---------------------------------------------------------------------------
must_replace(
    SCRIPT,
    '    # deployment\n'
    '    check_cloudflare_deploy_freshness,\n',
    '    # deployment\n'
    '    check_cloudflare_deploy_freshness,\n'
    '    check_anthropic_api_probe,\n',
    "3/3: register check_anthropic_api_probe in CHECKS list",
)


# ---------------------------------------------------------------------------
# Final gate: parse the modified file. If we broke syntax, fail loud.
# ---------------------------------------------------------------------------
src = SCRIPT.read_text(encoding="utf-8")
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"[FAIL] ast.parse after patch: {e}")
    sys.exit(3)
print("[ok]   ast.parse clean")
print("[done] all 3 patches applied")
