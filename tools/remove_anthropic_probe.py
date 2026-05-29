#!/usr/bin/env python3
"""Remove the anthropic_api_probe health check from tools/health_check.py.

mlb_edge's Claude features run on the Claude Max subscription
(CLAUDE_CODE_OAUTH_TOKEN, via the claude-brain / claude-postgame Actions),
NOT a pay-per-token ANTHROPIC_API_KEY. The Worker is intentionally never given
an API key, so anthropic_api_probe -- which asserts the Worker reports
enabled:true -- was a permanent false-RED. claude_brain_heartbeat already
covers Claude health (if the OAuth token/subscription fails, the brain bake
fails and that heartbeat goes RED).

Strips all three reference sites:
  1. the CHECK_REGISTRY entry           ("anthropic_api_probe": CAT_DEPLOYMENT)
  2. the check_anthropic_api_probe()    function definition
  3. its entry in the CHECKS list       (check_anthropic_api_probe,)

AST-validated and idempotent. Backs the original up to health_check.py.bak.
"""
import ast
import re
import sys
from pathlib import Path

P = Path("tools/health_check.py")


def main() -> int:
    src = P.read_text(encoding="utf-8")
    if "anthropic_api_probe" not in src:
        print("anthropic_api_probe already absent; nothing to do")
        return 0
    original = src

    # 1. Delete the function body using AST line spans (precise).
    tree = ast.parse(src)
    lines = src.split("\n")
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check_anthropic_api_probe":
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            end = node.end_lineno
            # absorb up to one trailing blank line so we don't leave a double gap
            if end < len(lines) and lines[end].strip() == "":
                end += 1
            spans.append((start, end))
    for start, end in sorted(spans, reverse=True):
        del lines[start - 1:end]
    src = "\n".join(lines)

    # 2. Remove the registry entry line.
    src = re.sub(r'(?m)^[ \t]*"anthropic_api_probe"[ \t]*:.*\n', "", src)

    # 3. Remove the CHECKS-list entry line.
    src = re.sub(r'(?m)^[ \t]*check_anthropic_api_probe[ \t]*,?[ \t]*\n', "", src)

    # Validate before writing.
    ast.parse(src)
    assert "anthropic_api_probe" not in src, "still referenced after removal"

    P.with_suffix(".py.bak").write_text(original, encoding="utf-8")
    P.write_text(src, encoding="utf-8", newline="\n")
    print("removed anthropic_api_probe (registry + function + CHECKS entry); "
          "backup -> tools/health_check.py.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
