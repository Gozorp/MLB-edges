"""
nightly_backstop.py
-------------------
Standalone midnight pipeline. Designed as a backstop for the Claude-driven
midnight cron — fires from Windows Task Scheduler so it survives Claude
restarts, crashes, machine reboots.

What it does (in order):
    1. Refresh Savant leaderboard CSVs (via scripts/refresh_savant.py)
    2. Refresh bat-tracking + lineup data (via scripts/refresh_data.py)
    3. Refresh B-R standings (via mlb_edge.data_sources.bref_fetch — uses MLB
       Stats API, NOT Cloudflare-blocked B-R direct)
    4. Retrain (skipped if models/latest.pkl mtime is already today after midnight)
    5. Generate today's slate (predict)
    6. Run audit on the slate
    7. Emit a summary log

What it does NOT do:
    - B-R box-score scraping via Chrome MCP. That requires Claude Code +
      Chrome extension, which only the Claude-driven cron can do. The
      box-score JSONs are nice-to-have historical context, not blocking
      for daily picks.

Usage:
    python scripts/nightly_backstop.py            # full pipeline
    python scripts/nightly_backstop.py --skip-retrain   # skip step 4

Logs to logs/nightly_backstop_{YYYYMMDD}.log.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

today = date.today()
LOG_FILE = LOGS / f"nightly_backstop_{today:%Y%m%d}.log"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("nightly_backstop")


def _run_subprocess(label: str, cmd: list[str], timeout: int,
                    high_priority: bool = False) -> bool:
    """Run a subprocess, log result, return True on success.

    `high_priority=True` raises the Windows process-priority class to
    AboveNormal — used for the retrain step where we want the OS to favor
    this work over background apps. Modest speedup (~5%) from reduced
    context-switch latency.
    """
    log.info("[%s] START: %s", label, " ".join(cmd))
    try:
        # `creationflags` is Windows-only — passing it on POSIX raises
        # TypeError even at value 0. Keep it OUT of kwargs entirely off-Windows.
        kwargs: dict = {}
        if sys.platform == "win32":
            # 0x08000000 = CREATE_NO_WINDOW — suppress the conhost popup
            # that would otherwise flash for every child process spawned
            # from a scheduled task.
            flags = 0x08000000
            if high_priority:
                # 0x00008000 = ABOVE_NORMAL_PRIORITY_CLASS. HIGH_PRIORITY_CLASS
                # (0x00000080) would starve interactive apps; AboveNormal
                # nudges the scheduler without making the box unusable.
                flags |= 0x00008000
            kwargs["creationflags"] = flags
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, **kwargs)
        if p.returncode != 0:
            log.error("[%s] FAIL rc=%d\nSTDERR: %s\nSTDOUT: %s",
                      label, p.returncode, (p.stderr or "")[-800:], (p.stdout or "")[-800:])
            return False
        # Log last few lines for visibility
        tail = "\n".join((p.stdout or "").strip().splitlines()[-6:])
        log.info("[%s] OK:\n%s", label, tail)
        return True
    except subprocess.TimeoutExpired:
        log.error("[%s] TIMEOUT after %ds", label, timeout)
        return False
    except Exception as e:
        log.error("[%s] EXCEPTION %s\n%s", label, e, traceback.format_exc())
        return False


def _refresh_script_path(name: str) -> Path | None:
    """refresh_*.py lives in scripts/ — historically at the repo root
    (D:/mlb_edge/scripts/) but newer copies may also live in the project
    subdir. Check both, mirroring auto_runner.py's fallback."""
    candidates = [
        ROOT / "scripts" / name,
        ROOT.parent / "scripts" / name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def step_savant() -> bool:
    script = _refresh_script_path("refresh_savant.py")
    if script is None:
        log.error("[savant] refresh_savant.py not found")
        return False
    return _run_subprocess(
        "savant",
        [sys.executable, str(script)],
        timeout=600,
    )


def step_bat_tracking() -> bool:
    script = _refresh_script_path("refresh_data.py")
    if script is None:
        log.warning("[bat_tracking] refresh_data.py not found, skipping")
        return True
    return _run_subprocess(
        "bat_tracking",
        [sys.executable, str(script), "--savant-only"],
        timeout=300,
    )


def step_standings() -> bool:
    """Pull B-R standings via MLB Stats API (Cloudflare-bypassed)."""
    log.info("[standings] START")
    try:
        from mlb_edge.data_sources.bref_fetch import fetch_standings
        written = fetch_standings(today)
        log.info("[standings] OK: wrote %d files", len(written))
        return True
    except Exception as e:
        log.error("[standings] FAIL %s\n%s", e, traceback.format_exc())
        return False


def _retrain_in_flight() -> bool:
    """True if a python.exe is currently running mlb_edge.main --mode train."""
    if sys.platform != "win32":
        return False
    try:
        p = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=20,
        )
        return "mlb_edge.main" in p.stdout and "--mode" in p.stdout and "train" in p.stdout
    except Exception:
        return False


def step_retrain(force: bool = False) -> bool:
    """Retrain unless models/latest.pkl is already from today (after midnight)
    OR a retrain is currently in flight (wait up to 60 min for it to finish).
    """
    # If a retrain is already running, wait it out instead of starting a duplicate.
    if not force:
        for poll in range(12):  # 12 × 5 min = 60 min cap
            if not _retrain_in_flight():
                break
            log.info("[retrain] another retrain in flight — waiting (poll %d/12, 5 min)", poll + 1)
            time.sleep(300)
        else:
            log.warning("[retrain] in-flight retrain still running after 60 min — proceeding anyway")

    mp = ROOT / "models" / "latest.pkl"
    if mp.exists() and not force:
        mt = datetime.fromtimestamp(mp.stat().st_mtime)
        today_midnight = datetime.combine(today, datetime.min.time())
        if mt >= today_midnight:
            log.info("[retrain] SKIP: model already retrained today (mtime=%s)", mt)
            return True

    yesterday = today - timedelta(days=1)
    seasons = ",".join(str(y) for y in range(today.year - 3, today.year + 1))
    return _run_subprocess(
        "retrain",
        [sys.executable, "-m", "mlb_edge.main",
         "--mode", "train",
         "--seasons", seasons,
         "--through", yesterday.isoformat(),
         "--save", "models/latest.pkl"],
        timeout=4 * 3600,  # 4 hours
        high_priority=True,  # nudge Windows scheduler to favor retrain
    )


def step_predict() -> bool:
    out = ROOT / f"picks_{today:%Y-%m-%d}.csv"
    return _run_subprocess(
        "predict",
        [sys.executable, "-m", "mlb_edge.main",
         "--mode", "predict",
         "--date", today.isoformat(),
         "--model_path", "models/latest.pkl",
         "--out", str(out),
         "--bankroll", "100"],
        timeout=600,
    )


def step_audit() -> bool:
    """Best-effort audit. Doesn't block pipeline if it fails.

    Uses regex-based replacement instead of literal string replace because
    audit_v10.py's hardcoded date / output filename change frequently as the
    user iterates on it. Regex is robust to whatever value happens to be in
    the file today. Bug fixed: previously the literals "day = date(2026, 4,
    25)" and "audit_2026-04-25_v10_interim.csv" stopped matching once the
    file was updated, so step_audit silently wrote to whatever date was
    hard-coded — could overwrite previous days' audits or read stale odds.
    """
    import re
    audit_script = ROOT / "audit_v10.py"
    if not audit_script.exists():
        log.warning("[audit] SKIP: audit_v10.py not found")
        return True
    audit_today = ROOT / f"audit_{today:%Y-%m-%d}.py"
    src = audit_script.read_text(encoding="utf-8")
    # Robust regex replace — matches any day = date(YYYY, M, D) and any
    # audit_YYYY-MM-DD*.csv literal regardless of the current values.
    new_src = re.sub(
        r"day\s*=\s*date\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}\s*\)",
        f"day = date({today.year}, {today.month}, {today.day})",
        src,
    )
    new_src = re.sub(
        r'"audit_\d{4}-\d{2}-\d{2}[^"]*\.csv"',
        f'"audit_{today:%Y-%m-%d}.csv"',
        new_src,
    )
    # Also patch the odds glob to match today's date (was hardcoded — Bug #5).
    new_src = re.sub(
        r'"data/odds_cache/odds_\d{4}-\d{2}-\d{2}\*\.parquet"',
        f'"data/odds_cache/odds_{today:%Y-%m-%d}*.parquet"',
        new_src,
    )
    audit_today.write_text(new_src, encoding="utf-8")
    return _run_subprocess(
        "audit",
        [sys.executable, str(audit_today)],
        timeout=600,
    )


def step_beginner_slate() -> bool:
    """Generate the human-readable slate (slate_readable_YYYY-MM-DD.md).
    Best-effort — depends on audit + picks files being present. If the
    audit failed earlier we silently skip rather than blocking the nightly.
    """
    script = ROOT / "scripts" / "beginner_slate.py"
    if not script.exists():
        log.warning("[beginner] beginner_slate.py not found, skipping")
        return True
    return _run_subprocess(
        "beginner",
        [sys.executable, str(script), "--date", today.isoformat()],
        timeout=120,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-retrain", action="store_true",
                    help="Skip the retrain step (refresh + predict only).")
    ap.add_argument("--force-retrain", action="store_true",
                    help="Retrain even if model is already fresh today.")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("NIGHTLY BACKSTOP starting (today=%s)", today)
    log.info("=" * 60)

    results: dict[str, bool] = {}
    results["savant"] = step_savant()
    results["bat_tracking"] = step_bat_tracking()
    results["standings"] = step_standings()
    if args.skip_retrain:
        results["retrain"] = True  # treat as no-op
        log.info("[retrain] SKIP: --skip-retrain flag")
    else:
        results["retrain"] = step_retrain(force=args.force_retrain)
    results["predict"] = step_predict()
    results["audit"] = step_audit()
    results["beginner"] = step_beginner_slate()

    log.info("=" * 60)
    log.info("SUMMARY:")
    for k, v in results.items():
        log.info("  %-14s %s", k, "OK" if v else "FAIL")
    log.info("=" * 60)

    # Return non-zero if any blocking step failed.
    blocking = ["savant", "standings", "retrain", "predict"]
    failed = [k for k in blocking if not results[k]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
