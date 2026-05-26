#!/usr/bin/env python3
"""
tools/health_check.py
=====================
Periodic pipeline-health observer. Runs the 3-check MVP:

  - daily_slate_heartbeat:   last daily-slate commit age (RED >24h, YELLOW >6h)
  - weights_state_freshness: last audit log entry age   (RED >48h, YELLOW >26h)
  - core_models_presence:    models/latest.pkl exists   (RED if missing)

Writes:
  - docs/data/health.json             — current snapshot for the dashboard
  - docs/data/health_alert_state.json — last-fire timestamps for rate-limiting

POSTs to Discord webhook (env DISCORD_HEALTH_WEBHOOK) when:
  - Any check is RED and the same RED hasn't been alerted in the last 6h
  - Daily 8am UTC digest (regardless of state — dead-man switch)

If DISCORD_HEALTH_WEBHOOK is unset, the script still writes health.json so
the dashboard card can render. The Discord side is the push half; the JSON
file is the pull half.

Designed to run on a 30-minute GitHub Actions cron. Zero non-stdlib deps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
HEALTH_JSON = REPO / "docs" / "data" / "health.json"
ALERT_STATE_JSON = REPO / "docs" / "data" / "health_alert_state.json"
AUDIT_LOG = REPO / "data" / "state" / "recalibration_log.jsonl"
MODEL_FILE = REPO / "models" / "latest.pkl"

DISCORD_WEBHOOK = os.environ.get("DISCORD_HEALTH_WEBHOOK", "").strip()

# Rate-limit window: same RED check can't re-alert within this many hours.
RATE_LIMIT_HOURS = 6.0
# Daily digest fires once per day at this UTC hour, regardless of state.
DAILY_DIGEST_HOUR_UTC = 8

GREEN = "green"
YELLOW = "yellow"
RED = "red"

EMOJI = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}
COLORS = {GREEN: 0x3FB950, YELLOW: 0xD29922, RED: 0xF85149}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _git_last_commit_iso(grep_pattern: str) -> Optional[str]:
    """Most recent commit's committer date (ISO 8601), matching grep pattern."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--grep", grep_pattern, "--format=%cI"],
            cwd=str(REPO),
            text=True,
            timeout=10,
        ).strip()
        return out or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None


def _audit_log_last_ts_iso() -> Optional[str]:
    """Last 'ts' field from data/state/recalibration_log.jsonl, or None."""
    if not AUDIT_LOG.exists():
        return None
    try:
        last = None
        with AUDIT_LOG.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return None
        return json.loads(last).get("ts")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _parse_iso(iso: str) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_hours(iso: str, now: datetime) -> Optional[float]:
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_daily_slate_heartbeat(now: datetime) -> Dict:
    iso = _git_last_commit_iso("daily-slate:")
    name = "daily_slate_heartbeat"
    if iso is None:
        return {"name": name, "severity": RED,
                "message": "no daily-slate commit found in git log",
                "detail": {}}
    age = _age_hours(iso, now)
    detail = {"last_run_iso": iso, "age_hours": round(age, 2)}
    if age > 24.0:
        return {"name": name, "severity": RED,
                "message": f"daily-slate hasn't run in {age:.1f}h "
                           f"(threshold 24h)",
                "detail": detail}
    if age > 6.0:
        return {"name": name, "severity": YELLOW,
                "message": f"daily-slate last ran {age:.1f}h ago "
                           f"(threshold 6h)",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"last ran {age:.1f}h ago",
            "detail": detail}


def check_weights_state_freshness(now: datetime) -> Dict:
    iso = _audit_log_last_ts_iso()
    name = "weights_state_freshness"
    if iso is None:
        return {"name": name, "severity": RED,
                "message": "audit log empty or unreadable",
                "detail": {}}
    age = _age_hours(iso, now)
    detail = {"last_entry_iso": iso, "age_hours": round(age, 2)}
    if age > 48.0:
        return {"name": name, "severity": RED,
                "message": f"weights last learned {age:.1f}h ago "
                           f"(threshold 48h)",
                "detail": detail}
    if age > 26.0:
        return {"name": name, "severity": YELLOW,
                "message": f"weights last learned {age:.1f}h ago "
                           f"(expected ~daily)",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"last learned {age:.1f}h ago",
            "detail": detail}


def check_core_models_presence(now: datetime) -> Dict:
    name = "core_models_presence"
    if MODEL_FILE.exists():
        size = MODEL_FILE.stat().st_size
        return {"name": name, "severity": GREEN,
                "message": f"present ({size / 1024 / 1024:.1f} MB)",
                "detail": {"path": str(MODEL_FILE.relative_to(REPO)),
                           "size_bytes": size}}
    return {"name": name, "severity": RED,
            "message": f"models/latest.pkl missing — every workflow will fail "
                       f"the sanity check",
            "detail": {"path": str(MODEL_FILE.relative_to(REPO))}}


CHECKS: List[Callable[[datetime], Dict]] = [
    check_daily_slate_heartbeat,
    check_weights_state_freshness,
    check_core_models_presence,
]


# ---------------------------------------------------------------------------
# Overall + alerting
# ---------------------------------------------------------------------------
def _overall_severity(results: List[Dict]) -> str:
    sevs = {r["severity"] for r in results}
    if RED in sevs:
        return RED
    if YELLOW in sevs:
        return YELLOW
    return GREEN


def _load_alert_state() -> Dict:
    if not ALERT_STATE_JSON.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _should_fire_red_alert(check_name: str, now: datetime,
                            state: Dict) -> bool:
    prev = state.get(check_name) or {}
    last_iso = prev.get("last_fired_at")
    if not last_iso:
        return True
    age = _age_hours(last_iso, now)
    if age is None:
        return True
    return age >= RATE_LIMIT_HOURS


def _should_fire_digest(now: datetime, state: Dict) -> bool:
    if now.hour != DAILY_DIGEST_HOUR_UTC:
        return False
    prev = state.get("_daily_digest") or {}
    last_iso = prev.get("last_fired_at")
    if not last_iso:
        return True
    last_dt = _parse_iso(last_iso)
    if last_dt is None:
        return True
    return last_dt.date() != now.date()


def _build_test_embed(now: datetime) -> Dict:
    return {
        "embeds": [{
            "title": "\ud83d\udd14 mlb_edge: test ping",
            "description": (
                "**End-to-end webhook verification.**\n\n"
                "If you see this, the loop is wired correctly:\n"
                "GitHub Actions \u2192 health_check.py \u2192 Discord.\n\n"
                "_This is a manual workflow_dispatch test, not a real alert._"
            ),
            "color": 0x58A6FF,
            "timestamp": now.isoformat(),
            "footer": {"text": "fire via Actions \u2192 Pipeline health check \u2192 Run workflow"},
        }]
    }


def _post_discord(payload: Dict) -> bool:
    if not DISCORD_WEBHOOK:
        return False
    try:
        req = urllib.request.Request(
            DISCORD_WEBHOOK,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"[health] Discord post failed: {e}", file=sys.stderr)
        return False


def _build_alert_embed(red_results: List[Dict], now: datetime) -> Dict:
    lines = []
    for r in red_results:
        lines.append(f"🔴 `{r['name']}`: {r['message']}")
    return {
        "embeds": [{
            "title": f"🔴 mlb_edge: {len(red_results)} critical check(s) failing",
            "description": "\n".join(lines),
            "color": COLORS[RED],
            "timestamp": now.isoformat(),
            "footer": {"text": "rate-limited 6h per check"},
        }]
    }


def _build_digest_embed(results: List[Dict], overall: str,
                        now: datetime) -> Dict:
    lines = [f"Overall: **{overall.upper()}**", ""]
    for r in results:
        lines.append(f"{EMOJI[r['severity']]} `{r['name']}`: {r['message']}")
    return {
        "embeds": [{
            "title": f"{EMOJI[overall]} mlb_edge daily health digest",
            "description": "\n".join(lines),
            "color": COLORS[overall],
            "timestamp": now.isoformat(),
            "footer": {"text": "8am UTC dead-man switch — silence means broken"},
        }]
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    now = datetime.now(timezone.utc)

    # Test-ping mode: bypass all checks and post a synthetic ping.
    # Manual diagnostic only \u2014 only firable via workflow_dispatch
    # with force_test_alert=true. Returns immediately after posting so
    # it doesn't touch health.json or alert state.
    if os.environ.get("FORCE_TEST_ALERT", "").lower() in ("1", "true", "yes"):
        ok = _post_discord(_build_test_embed(now))
        print(f"[health] FORCE_TEST_ALERT fired: posted={ok}")
        return 0 if ok else 1

    results = [c(now) for c in CHECKS]
    overall = _overall_severity(results)

    # Build snapshot
    health = {
        "checked_at": now.isoformat(),
        "overall": overall,
        "checks": results,
    }

    # Decide what to alert
    state = _load_alert_state()
    new_state = dict(state)

    red_to_fire = []
    for r in results:
        if r["severity"] == RED:
            if _should_fire_red_alert(r["name"], now, state):
                red_to_fire.append(r)
                new_state[r["name"]] = {
                    "last_fired_at": now.isoformat(),
                    "last_severity": RED,
                    "last_message": r["message"],
                }

    digest_to_fire = _should_fire_digest(now, state)

    # POST
    if red_to_fire:
        embed = _build_alert_embed(red_to_fire, now)
        ok = _post_discord(embed)
        print(f"[health] RED alert payload fired: {len(red_to_fire)} check(s), "
              f"posted={ok}")
    if digest_to_fire:
        embed = _build_digest_embed(results, overall, now)
        ok = _post_discord(embed)
        if ok:
            new_state["_daily_digest"] = {"last_fired_at": now.isoformat()}
        print(f"[health] daily digest fired: posted={ok}")

    # Write outputs
    HEALTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_JSON.write_text(json.dumps(health, indent=2), encoding="utf-8")
    ALERT_STATE_JSON.write_text(json.dumps(new_state, indent=2),
                                 encoding="utf-8")

    print(f"[health] overall={overall}  "
          f"checks={ {r['name']: r['severity'] for r in results} }  "
          f"red_alerts_fired={len(red_to_fire)}  "
          f"digest_fired={digest_to_fire}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
