#!/usr/bin/env python3
"""
_patch_force_test_alert.py
==========================
Adds a permanent diagnostic button to the health-check loop:

  Workflow gains a boolean workflow_dispatch input `force_test_alert`.
  When true, the script posts a synthetic test ping to the Discord
  webhook regardless of check state. Useful for:
    - Verifying webhook secret after rotation
    - Confirming plumbing after adding new alert rules
    - Sanity-checking Discord/Cloudflare/runner changes

Keeping the flag permanent (not a "ship it then pull it out" lifecycle)
because it's free to keep, requires manual dispatch to fire (can't
false-alarm from cron), and we'll want this button every time we
touch the alert path.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SCRIPT = REPO / "tools" / "health_check.py"
WORKFLOW = REPO / ".github" / "workflows" / "health-check.yml"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# ---------- 1. health_check.py: add _build_test_embed + main() handling ----
must_replace(
    SCRIPT,
    "def _post_discord(payload: Dict) -> bool:\n",
    "def _build_test_embed(now: datetime) -> Dict:\n"
    "    return {\n"
    '        "embeds": [{\n'
    '            "title": "\\ud83d\\udd14 mlb_edge: test ping",\n'
    '            "description": (\n'
    '                "**End-to-end webhook verification.**\\n\\n"\n'
    '                "If you see this, the loop is wired correctly:\\n"\n'
    '                "GitHub Actions \\u2192 health_check.py \\u2192 Discord.\\n\\n"\n'
    '                "_This is a manual workflow_dispatch test, not a real alert._"\n'
    '            ),\n'
    '            "color": 0x58A6FF,\n'
    '            "timestamp": now.isoformat(),\n'
    '            "footer": {"text": "fire via Actions \\u2192 Pipeline health check '
    '\\u2192 Run workflow"},\n'
    '        }]\n'
    "    }\n"
    "\n"
    "\n"
    "def _post_discord(payload: Dict) -> bool:\n",
    "1: add _build_test_embed helper",
)

must_replace(
    SCRIPT,
    "def main() -> int:\n"
    "    now = datetime.now(timezone.utc)\n"
    "    results = [c(now) for c in CHECKS]\n",
    "def main() -> int:\n"
    "    now = datetime.now(timezone.utc)\n"
    "\n"
    "    # Test-ping mode: bypass all checks and post a synthetic ping.\n"
    "    # Manual diagnostic only \\u2014 only firable via workflow_dispatch\n"
    "    # with force_test_alert=true. Returns immediately after posting so\n"
    "    # it doesn't touch health.json or alert state.\n"
    "    if os.environ.get(\"FORCE_TEST_ALERT\", \"\").lower() in (\"1\", \"true\", \"yes\"):\n"
    "        ok = _post_discord(_build_test_embed(now))\n"
    "        print(f\"[health] FORCE_TEST_ALERT fired: posted={ok}\")\n"
    "        return 0 if ok else 1\n"
    "\n"
    "    results = [c(now) for c in CHECKS]\n",
    "2: wire FORCE_TEST_ALERT env handling in main",
)


# ---------- 2. workflow yaml: add the workflow_dispatch input ------------
must_replace(
    WORKFLOW,
    "on:\n"
    "  schedule:\n"
    '    - cron: "*/30 * * * *"  # every 30 minutes\n'
    "  workflow_dispatch:\n",
    "on:\n"
    "  schedule:\n"
    '    - cron: "*/30 * * * *"  # every 30 minutes\n'
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      force_test_alert:\n"
    "        description: 'Fire a synthetic Discord test ping (no real check runs)'\n"
    "        type: boolean\n"
    "        required: false\n"
    "        default: false\n",
    "3: workflow_dispatch input",
)

must_replace(
    WORKFLOW,
    "      - name: Run health check\n"
    "        env:\n"
    "          DISCORD_HEALTH_WEBHOOK: ${{ secrets.DISCORD_HEALTH_WEBHOOK }}\n"
    "        run: python tools/health_check.py\n",
    "      - name: Run health check\n"
    "        env:\n"
    "          DISCORD_HEALTH_WEBHOOK: ${{ secrets.DISCORD_HEALTH_WEBHOOK }}\n"
    "          # workflow_dispatch input plumbs through here. On cron runs\n"
    "          # this resolves to an empty string, so the script's truthy\n"
    "          # check correctly skips the test path.\n"
    "          FORCE_TEST_ALERT: ${{ inputs.force_test_alert }}\n"
    "        run: python tools/health_check.py\n",
    "4: pass FORCE_TEST_ALERT env into the step",
)


# Verify the script still parses
import ast
ast.parse(SCRIPT.read_text(encoding="utf-8"))
print("[ok]   AST parse: OK")
