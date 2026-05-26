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

# Schema version. Bumped 2026-05-26 when we added per-check `category`
# fields and the top-level `categories` roll-up.
SCHEMA_VERSION = 2

# Cloudflare Pages deploy base URL. Hardcoded to the default Pages
# subdomain; flip to a custom domain if/when one is mapped.
PAGES_BASE_URL = "https://mlb-edges.pages.dev"

# Categories — used for the rolled-up dashboard card.
CAT_WORKFLOWS  = "workflows"
CAT_DATA_FLOW  = "data_flow"
CAT_DEPLOYMENT = "deployment"
CAT_MODEL      = "model"

# Name -> category. Each check result gets this stamped on it in
# main() so the check functions stay framework-free.


CHECK_CATEGORIES = {
    # workflows
    "daily_slate_heartbeat":       CAT_WORKFLOWS,
    "refit_calibrator_heartbeat":  CAT_WORKFLOWS,
    "weekly_backtest_heartbeat":   CAT_WORKFLOWS,
    "claude_brain_heartbeat":      CAT_WORKFLOWS,
    # data flow
    "bullpen_meta_freshness":      CAT_DATA_FLOW,
    "odds_api_completeness":       CAT_DATA_FLOW,
    "pending_sp_data_rate":        CAT_DATA_FLOW,
    # deployment
    "cloudflare_deploy_freshness": CAT_DEPLOYMENT,
    "anthropic_api_probe":         CAT_DEPLOYMENT,
    # model
    "weights_state_freshness":     CAT_MODEL,
    "core_models_presence":        CAT_MODEL,
    "runaway_ceiling_alarm":       CAT_MODEL,
    "stress_warning_rate":         CAT_MODEL,
}


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


def _today_iso_utc(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _find_today_picks_csv(now: datetime) -> Optional[Path]:
    """Locate today's picks diag CSV in either of two known locations."""
    today = _today_iso_utc(now)
    for p in (REPO / "docs" / "data" / f"picks_{today}_diag.csv",
              REPO / f"picks_{today}_diag.csv"):
        if p.exists():
            return p
    return None


def _load_picks_csv(p: Path) -> List[Dict]:
    import csv as _csv
    try:
        with p.open(encoding="utf-8") as f:
            return list(_csv.DictReader(f))
    except (OSError, _csv.Error):
        return []


def _workflow_heartbeat_check(now: datetime, name: str,
                                grep_pattern: str,
                                yellow_h: float, red_h: float,
                                cadence_desc: str) -> Dict:
    """Shared shape for workflow-heartbeat checks via `git log --grep`."""
    iso = _git_last_commit_iso(grep_pattern)
    if iso is None:
        return {"name": name, "severity": RED,
                "message": f"no `{grep_pattern}` commit found in git log",
                "detail": {}}
    age = _age_hours(iso, now)
    detail = {"last_run_iso": iso, "age_hours": round(age, 2),
              "cadence": cadence_desc}
    if age > red_h:
        return {"name": name, "severity": RED,
                "message": (f"{cadence_desc} hasn't fired in "
                            f"{age/24:.1f} days "
                            f"(threshold {red_h/24:.0f}d)"),
                "detail": detail}
    if age > yellow_h:
        return {"name": name, "severity": YELLOW,
                "message": (f"last fired {age/24:.1f} days ago "
                            f"(threshold {yellow_h/24:.0f}d)"),
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": (f"last fired {age:.1f}h ago" if age < 24
                        else f"last fired {age/24:.1f}d ago"),
            "detail": detail}


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


def check_refit_calibrator_heartbeat(now: datetime) -> Dict:
    return _workflow_heartbeat_check(
        now, "refit_calibrator_heartbeat", "refit-calibrator:",
        yellow_h=240.0, red_h=336.0,
        cadence_desc="weekly calibrator refit")


def check_weekly_backtest_heartbeat(now: datetime) -> Dict:
    return _workflow_heartbeat_check(
        now, "weekly_backtest_heartbeat", "weekly-backtest:",
        yellow_h=240.0, red_h=336.0,
        cadence_desc="weekly backtest")


def check_claude_brain_heartbeat(now: datetime) -> Dict:
    return _workflow_heartbeat_check(
        now, "claude_brain_heartbeat", "claude-brain:",
        yellow_h=36.0, red_h=72.0,
        cadence_desc="daily Claude Brain review")


def check_bullpen_meta_freshness(now: datetime) -> Dict:
    name = "bullpen_meta_freshness"
    today = _today_iso_utc(now)
    p = REPO / "docs" / "data" / f"bullpen_meta_{today}.json"
    if not p.exists():
        return {"name": name, "severity": RED,
                "message": f"bullpen_meta_{today}.json missing",
                "detail": {"expected_path": str(p.relative_to(REPO))}}
    age = (now.timestamp() - p.stat().st_mtime) / 3600.0
    detail = {"path": str(p.relative_to(REPO)),
              "mtime_age_hours": round(age, 2)}
    if age > 24.0:
        return {"name": name, "severity": RED,
                "message": f"bullpen_meta written {age:.1f}h ago "
                           f"(threshold 24h)",
                "detail": detail}
    if age > 12.0:
        return {"name": name, "severity": YELLOW,
                "message": f"bullpen_meta written {age:.1f}h ago "
                           f"(threshold 12h)",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"written {age:.1f}h ago",
            "detail": detail}


def check_odds_api_completeness(now: datetime) -> Dict:
    name = "odds_api_completeness"
    p = _find_today_picks_csv(now)
    if p is None:
        return {"name": name, "severity": RED,
                "message": "no picks_<today>_diag.csv found",
                "detail": {}}
    rows = _load_picks_csv(p)
    if not rows:
        return {"name": name, "severity": YELLOW,
                "message": "picks CSV is empty",
                "detail": {"path": str(p.relative_to(REPO))}}
    statuses = {}
    for r in rows:
        k = (r.get("odds_status") or "").strip() or "(blank)"
        statuses[k] = statuses.get(k, 0) + 1
    total = len(rows)
    ok = statuses.get("fetched", 0) + statuses.get("fetched_capped", 0)
    non_ok = total - ok
    pct_non_ok = (non_ok / total) if total else 0.0
    detail = {"total_rows": total, "ok_rows": ok,
              "non_ok_pct": round(pct_non_ok * 100, 1),
              "status_distribution": statuses}
    if pct_non_ok > 0.75:
        return {"name": name, "severity": RED,
                "message": f"{pct_non_ok*100:.0f}% of slate is "
                           f"non-fetched odds",
                "detail": detail}
    if pct_non_ok > 0.25:
        return {"name": name, "severity": YELLOW,
                "message": f"{pct_non_ok*100:.0f}% of slate is "
                           f"non-fetched odds",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"{ok}/{total} rows have market odds",
            "detail": detail}


def check_pending_sp_data_rate(now: datetime) -> Dict:
    name = "pending_sp_data_rate"
    p = _find_today_picks_csv(now)
    if p is None:
        return {"name": name, "severity": YELLOW,
                "message": "no picks CSV for today",
                "detail": {}}
    rows = _load_picks_csv(p)
    if not rows:
        return {"name": name, "severity": GREEN,
                "message": "no rows to evaluate",
                "detail": {}}
    pending = sum(1 for r in rows
                  if (r.get("tier") or "").strip() == "PENDING_SP_DATA")
    total = len(rows)
    pct = pending / total if total else 0.0
    detail = {"total_rows": total, "pending_sp_data_count": pending,
              "pct_of_slate": round(pct * 100, 1)}
    if pct > 0.50:
        return {"name": name, "severity": RED,
                "message": f"{pct*100:.0f}% PENDING_SP_DATA "
                           f"({pending}/{total})",
                "detail": detail}
    if pct > 0.25:
        return {"name": name, "severity": YELLOW,
                "message": f"{pct*100:.0f}% PENDING_SP_DATA "
                           f"({pending}/{total})",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"{pending}/{total} rows PENDING_SP_DATA",
            "detail": detail}


def check_cloudflare_deploy_freshness(now: datetime) -> Dict:
    name = "cloudflare_deploy_freshness"
    try:
        req = urllib.request.Request(
            f"{PAGES_BASE_URL}/api/health",
            headers={"User-Agent": "mlb-edge-health-check/1"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as e:
        return {"name": name, "severity": RED,
                "message": f"/api/health unreachable: "
                           f"{type(e).__name__}",
                "detail": {"pages_url": PAGES_BASE_URL,
                           "error": str(e)[:200]}}
    deployed_sha = (body.get("commit") or "unknown").strip()
    if deployed_sha in ("unknown", ""):
        return {"name": name, "severity": YELLOW,
                "message": "deployed commit SHA not reported by /api/health",
                "detail": {"pages_url": PAGES_BASE_URL,
                           "body": body}}
    try:
        ct = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", deployed_sha],
            cwd=str(REPO), text=True, timeout=10).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        ct = None
    detail = {"deployed_sha": deployed_sha[:12],
              "pages_url": PAGES_BASE_URL,
              "deployed_commit_iso": ct}
    if ct is None:
        return {"name": name, "severity": YELLOW,
                "message": "deployed SHA not in local git history "
                           "(maybe fetch-depth too small)",
                "detail": detail}
    age = _age_hours(ct, now)
    detail["age_hours"] = round(age, 2)
    if age > 48.0:
        return {"name": name, "severity": RED,
                "message": f"deployed commit is {age/24:.1f}d old",
                "detail": detail}
    if age > 24.0:
        return {"name": name, "severity": YELLOW,
                "message": f"deployed commit is {age:.1f}h old",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"deployed {age:.1f}h ago "
                       f"({deployed_sha[:8]})",
            "detail": detail}


def check_anthropic_api_probe(now: datetime) -> Dict:
    """HTTPS GET to /api/claude/health. Asserts the Pages deployment
    has the ANTHROPIC_API_KEY env var set (enabled:true) and is on
    the expected model. A missing key silently disables Deep Analysis
    on the dashboard, which is exactly the kind of failure that
    needs proactive paging rather than waiting for a user to notice.
    """
    name = "anthropic_api_probe"
    expected_model = "claude-opus-4-6"
    try:
        req = urllib.request.Request(
            f"{PAGES_BASE_URL}/api/claude/health",
            headers={"User-Agent": "mlb-edge-health-check/1"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as e:
        return {"name": name, "severity": RED,
                "message": f"/api/claude/health unreachable: "
                           f"{type(e).__name__}",
                "detail": {"pages_url": PAGES_BASE_URL,
                           "error": str(e)[:200]}}
    enabled = bool(body.get("enabled"))
    model = (body.get("model") or "").strip()
    detail = {"pages_url": PAGES_BASE_URL,
              "enabled": enabled, "model": model,
              "max_tokens": body.get("max_tokens"),
              "deployed_commit": (body.get("commit")
                                  or "unknown")[:12]}
    if not enabled:
        return {"name": name, "severity": RED,
                "message": "ANTHROPIC_API_KEY not set on Pages env "
                           "(Deep Analysis disabled)",
                "detail": detail}
    if model != expected_model:
        return {"name": name, "severity": YELLOW,
                "message": f"model is '{model}', expected "
                           f"'{expected_model}'",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"enabled, model={model}",
            "detail": detail}


def check_runaway_ceiling_alarm(now: datetime) -> Dict:
    name = "runaway_ceiling_alarm"
    if not AUDIT_LOG.exists():
        return {"name": name, "severity": YELLOW,
                "message": "audit log missing",
                "detail": {}}
    alarms_7d, alarms_24h = [], []
    cutoff_7d = now.timestamp() - 7 * 86400
    cutoff_24h = now.timestamp() - 86400
    try:
        with AUDIT_LOG.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not entry.get("runaway_ceiling_alarm"):
                    continue
                ts = _parse_iso(entry.get("ts", ""))
                if ts is None:
                    continue
                if ts.timestamp() > cutoff_7d:
                    alarms_7d.append(entry)
                if ts.timestamp() > cutoff_24h:
                    alarms_24h.append(entry)
    except OSError:
        pass
    detail = {"alarms_7d": len(alarms_7d),
              "alarms_24h": len(alarms_24h)}
    if alarms_24h:
        last = alarms_24h[-1]
        features = last.get("runaway_features", [])
        detail["last_alarm_iso"] = last.get("ts")
        detail["runaway_features"] = features
        return {"name": name, "severity": RED,
                "message": f"runaway alarm fired in last 24h on: "
                           f"{', '.join(features) or '(unknown)'}",
                "detail": detail}
    if alarms_7d:
        return {"name": name, "severity": YELLOW,
                "message": f"{len(alarms_7d)} runaway alarm(s) "
                           f"in last 7 days",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": "no runaway alarms in last 7 days",
            "detail": detail}


def check_stress_warning_rate(now: datetime) -> Dict:
    name = "stress_warning_rate"
    p = _find_today_picks_csv(now)
    if p is None:
        return {"name": name, "severity": YELLOW,
                "message": "no picks CSV for today",
                "detail": {}}
    rows = _load_picks_csv(p)
    if not rows:
        return {"name": name, "severity": GREEN,
                "message": "no rows to evaluate",
                "detail": {}}
    stressed = sum(1 for r in rows
                   if (r.get("stress_warnings") or "").strip())
    total = len(rows)
    pct = stressed / total if total else 0.0
    detail = {"total_rows": total, "stress_warned_count": stressed,
              "pct_of_slate": round(pct * 100, 1)}
    if pct > 0.75:
        return {"name": name, "severity": RED,
                "message": f"{pct*100:.0f}% of slate stress-warned "
                           f"({stressed}/{total})",
                "detail": detail}
    if pct > 0.50:
        return {"name": name, "severity": YELLOW,
                "message": f"{pct*100:.0f}% of slate stress-warned "
                           f"({stressed}/{total})",
                "detail": detail}
    return {"name": name, "severity": GREEN,
            "message": f"{stressed}/{total} rows stress-warned",
            "detail": detail}


CHECKS: List[Callable[[datetime], Dict]] = [
    # workflows
    check_daily_slate_heartbeat,
    check_refit_calibrator_heartbeat,
    check_weekly_backtest_heartbeat,
    check_claude_brain_heartbeat,
    # data flow
    check_bullpen_meta_freshness,
    check_odds_api_completeness,
    check_pending_sp_data_rate,
    # deployment
    check_cloudflare_deploy_freshness,
    check_anthropic_api_probe,
    # model
    check_weights_state_freshness,
    check_core_models_presence,
    check_runaway_ceiling_alarm,
    check_stress_warning_rate,
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
    # Stamp each result with its category for the dashboard roll-up.
    for r in results:
        r["category"] = CHECK_CATEGORIES.get(r["name"], "uncategorized")
    overall = _overall_severity(results)

    # Per-category roll-up: max-severity within each category.
    category_severity: Dict[str, str] = {}
    sev_rank = {GREEN: 0, YELLOW: 1, RED: 2}
    for r in results:
        cat = r["category"]
        cur = category_severity.get(cat, GREEN)
        if sev_rank[r["severity"]] > sev_rank[cur]:
            category_severity[cat] = r["severity"]
        elif cat not in category_severity:
            category_severity[cat] = r["severity"]

    # Build snapshot (schema v2)
    health = {
        "version": SCHEMA_VERSION,
        "checked_at": now.isoformat(),
        "overall": overall,
        "categories": category_severity,
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
