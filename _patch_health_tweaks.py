#!/usr/bin/env python3
"""
_patch_health_tweaks.py
========================
Two quality-of-life adjustments to tools/health_check.py while the
Cloudflare Pages project is being stood up:

  1. daily_slate_heartbeat: widen YELLOW threshold from 6h to 14h.
     The daily-slate workflow fires once in the morning Pacific (so
     up to ~16h of "no recent fire" is normal in late evening). The
     old 6h YELLOW threshold made the check sit in steady-state
     yellow for most of the day. RED threshold (24h) stays.

  2. odds_api_completeness -> kalshi_coverage_rate. The check has
     never actually examined OddsAPI — it segments the picks_*_diag
     odds_status column, and since the 2026-05-21 OddsAPI cancellation
     (see [[oddsapi-cancelled]] memory), every row that's "ok" is
     ok via Kalshi. The new name + new message strings reflect
     what the check actually measures.

Note: the rename means health_alert_state.json keeps a stale entry
for odds_api_completeness that won't update again. Harmless — it's
just one orphan key in a state dict. New fires append under the new
name. No backfill needed.

Per locked memory: bash + Python str.replace; no Edit tool.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
TARGET = REPO / "tools" / "health_check.py"


def must_replace(p: Path, old: str, new: str, label: str = "") -> None:
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"[ok]   {label}")


# ===========================================================================
# 1. daily_slate_heartbeat: widen YELLOW threshold to 14h
# ===========================================================================

# 1a. module docstring summary line
must_replace(
    TARGET,
    '  - daily_slate_heartbeat:   last daily-slate commit age (RED >24h, YELLOW >6h)\n',
    '  - daily_slate_heartbeat:   last daily-slate commit age (RED >24h, YELLOW >14h)\n',
    "1a/4: docstring — update YELLOW threshold from 6h to 14h",
)

# 1b. actual threshold + message text inside the check function
must_replace(
    TARGET,
    '    if age > 6.0:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": f"daily-slate last ran {age:.1f}h ago "\n'
    '                           f"(threshold 6h)",\n'
    '                "detail": detail}\n',
    '    if age > 14.0:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": f"daily-slate last ran {age:.1f}h ago "\n'
    '                           f"(threshold 14h)",\n'
    '                "detail": detail}\n',
    "1b/4: check function — widen YELLOW threshold to 14h",
)


# ===========================================================================
# 2. Rename odds_api_completeness -> kalshi_coverage_rate
# ===========================================================================

# 2a. CHECK_CATEGORIES dict key
must_replace(
    TARGET,
    '    "odds_api_completeness":       CAT_DATA_FLOW,\n',
    '    "kalshi_coverage_rate":        CAT_DATA_FLOW,\n',
    "2a/4: CHECK_CATEGORIES — rename key",
)

# 2b. Function definition + inner name + message strings (one big block
# replace so we get the rename + the message text in a single hop).
must_replace(
    TARGET,
    'def check_odds_api_completeness(now: datetime) -> Dict:\n'
    '    name = "odds_api_completeness"\n'
    '    p = _find_today_picks_csv(now)\n'
    '    if p is None:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": "no picks_<today>_diag.csv found",\n'
    '                "detail": {}}\n'
    '    rows = _load_picks_csv(p)\n'
    '    if not rows:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": "picks CSV is empty",\n'
    '                "detail": {"path": str(p.relative_to(REPO))}}\n'
    '    statuses = {}\n'
    '    for r in rows:\n'
    '        k = (r.get("odds_status") or "").strip() or "(blank)"\n'
    '        statuses[k] = statuses.get(k, 0) + 1\n'
    '    total = len(rows)\n'
    '    ok = statuses.get("fetched", 0) + statuses.get("fetched_capped", 0)\n'
    '    non_ok = total - ok\n'
    '    pct_non_ok = (non_ok / total) if total else 0.0\n'
    '    detail = {"total_rows": total, "ok_rows": ok,\n'
    '              "non_ok_pct": round(pct_non_ok * 100, 1),\n'
    '              "status_distribution": statuses}\n'
    '    if pct_non_ok > 0.75:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": f"{pct_non_ok*100:.0f}% of slate is "\n'
    '                           f"non-fetched odds",\n'
    '                "detail": detail}\n'
    '    if pct_non_ok > 0.25:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": f"{pct_non_ok*100:.0f}% of slate is "\n'
    '                           f"non-fetched odds",\n'
    '                "detail": detail}\n',
    'def check_kalshi_coverage_rate(now: datetime) -> Dict:\n'
    '    """Segments today\'s picks_*_diag odds_status column to measure\n'
    '    Kalshi moneyline coverage. Despite the legacy "odds_status"\n'
    '    column name, since the 2026-05-21 OddsAPI cancellation every\n'
    '    row that\'s "fetched" or "fetched_capped" is ok via Kalshi.\n'
    '    The check is renamed from odds_api_completeness to reflect\n'
    '    what it actually measures."""\n'
    '    name = "kalshi_coverage_rate"\n'
    '    p = _find_today_picks_csv(now)\n'
    '    if p is None:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": "no picks_<today>_diag.csv found",\n'
    '                "detail": {}}\n'
    '    rows = _load_picks_csv(p)\n'
    '    if not rows:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": "picks CSV is empty",\n'
    '                "detail": {"path": str(p.relative_to(REPO))}}\n'
    '    statuses = {}\n'
    '    for r in rows:\n'
    '        k = (r.get("odds_status") or "").strip() or "(blank)"\n'
    '        statuses[k] = statuses.get(k, 0) + 1\n'
    '    total = len(rows)\n'
    '    ok = statuses.get("fetched", 0) + statuses.get("fetched_capped", 0)\n'
    '    non_ok = total - ok\n'
    '    pct_non_ok = (non_ok / total) if total else 0.0\n'
    '    detail = {"total_rows": total, "ok_rows": ok,\n'
    '              "non_ok_pct": round(pct_non_ok * 100, 1),\n'
    '              "status_distribution": statuses}\n'
    '    if pct_non_ok > 0.75:\n'
    '        return {"name": name, "severity": RED,\n'
    '                "message": f"{pct_non_ok*100:.0f}% of slate has no "\n'
    '                           f"Kalshi moneyline",\n'
    '                "detail": detail}\n'
    '    if pct_non_ok > 0.25:\n'
    '        return {"name": name, "severity": YELLOW,\n'
    '                "message": f"{pct_non_ok*100:.0f}% of slate has no "\n'
    '                           f"Kalshi moneyline",\n'
    '                "detail": detail}\n',
    "2b/4: rename function + update name field + update message strings",
)

# 2c. CHECKS list entry (the registration list near the bottom of the file).
must_replace(
    TARGET,
    '    check_odds_api_completeness,\n',
    '    check_kalshi_coverage_rate,\n',
    "2c/4: CHECKS list — rename registration",
)


# ===========================================================================
# Final gate: parse
# ===========================================================================
src = TARGET.read_text(encoding="utf-8")
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"[FAIL] ast.parse after patch: {e}")
    sys.exit(3)
print("[ok]   ast.parse clean")
print("[done] 4 patches applied (2 tweaks)")
