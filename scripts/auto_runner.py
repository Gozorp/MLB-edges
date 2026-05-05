"""
auto_runner.py — set-and-forget orchestrator for the mlb_edge pipeline.

Cadence (tiered, not 5-min-for-everything):
    06:30 daily   B-R standings pull, Savant leaderboards, model retrain
    11:00 daily   warm-up slate build (odds may still be sparse)
    12:00-23:00   every 15 min: odds refresh + slate regenerate
    03:00 daily   log rotation + integrity check

Logging:
    logs/status.log        — every job start/finish
    logs/errors.log        — exception tracebacks
Notifications (Windows):
    Toast via PowerShell on any job failure. No extra deps.

Run:
    python scripts/auto_runner.py          # foreground
    python scripts/auto_runner.py --once   # run all due jobs once, exit
    python scripts/auto_runner.py --now <job>   # run one job immediately

On boot: see scripts/autostart.bat which launches this in a detached
console.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

# Make the project importable whether launched from repo root or scripts/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _setup_logging() -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid double handlers on re-init (--once re-entry).
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    status = logging.handlers.RotatingFileHandler(
        LOGS / "status.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    status.setLevel(logging.INFO)
    status.setFormatter(fmt)
    root.addHandler(status)

    errors = logging.handlers.RotatingFileHandler(
        LOGS / "errors.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(fmt)
    root.addHandler(errors)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    return logging.getLogger("auto_runner")


log = _setup_logging()


# ---------------------------------------------------------------------------
# Notifications (Windows toast via PowerShell — no extra deps)
# ---------------------------------------------------------------------------
def _notify(title: str, body: str) -> None:
    if sys.platform != "win32":
        log.warning("Non-windows toast skipped: %s — %s", title, body)
        return
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null; "
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f"$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode('{title}')) > $null; "
        f"$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode('{body}')) > $null; "
        "$n = [Windows.UI.Notifications.ToastNotification]::new($t); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('mlb_edge').Show($n)"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=10, capture_output=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Toast notification failed: %s", e)


# ---------------------------------------------------------------------------
# Job wrapper: log + notify on failure
# ---------------------------------------------------------------------------
def _run(name: str, fn, *args, **kwargs):
    log.info("[%s] START", name)
    try:
        result = fn(*args, **kwargs)
        log.info("[%s] OK %s", name, result if result else "")
        return True
    except Exception as e:  # noqa: BLE001
        log.error("[%s] FAIL %s\n%s", name, e, traceback.format_exc())
        _notify(f"mlb_edge {name} FAILED", str(e)[:120])
        return False


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def job_refresh_standings() -> str:
    from mlb_edge.data_sources.bref_fetch import fetch_standings
    written = fetch_standings(date.today())
    return f"wrote {len(written)} standings CSVs"


def job_refresh_savant() -> str:
    # Reuse the existing CLI script if present; otherwise call its fetch
    # function directly. Using subprocess keeps the existing script
    # authoritative (it already has UA, retries, rate limiting).
    script = ROOT / "scripts" / "refresh_savant.py"
    if not script.exists():
        # Fallback: the script lives at the parent repo (/d/mlb_edge/scripts).
        alt = ROOT.parent / "scripts" / "refresh_savant.py"
        if alt.exists():
            script = alt
    if not script.exists():
        return "skipped (refresh_savant.py not found)"
    p = subprocess.run([sys.executable, str(script)], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"refresh_savant.py rc={p.returncode}: {p.stderr[-400:]}")
    return "savant leaderboards refreshed"


def job_refresh_bat_tracking() -> str:
    # Same pattern — the bat-tracking CSV is the one the slate loader
    # looks up as its "Savant snapshot on or before X".
    script = ROOT / "scripts" / "refresh_data.py"
    if not script.exists():
        alt = ROOT.parent / "scripts" / "refresh_data.py"
        if alt.exists():
            script = alt
    if not script.exists():
        return "skipped (refresh_data.py not found)"
    p = subprocess.run([sys.executable, str(script), "--savant-only"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"refresh_data.py rc={p.returncode}: {p.stderr[-400:]}")
    return "bat-tracking refreshed"


def job_retrain() -> str:
    today = date.today()
    # Train on the last 4 seasons ending today.
    seasons = ",".join(str(y) for y in range(today.year - 3, today.year + 1))
    cmd = [sys.executable, "-m", "mlb_edge.main",
           "--mode", "train",
           "--seasons", seasons,
           "--through", today.isoformat(),
           "--save", "models/latest.pkl"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"train rc={p.returncode}: {p.stderr[-600:]}")
    # Extract the train AUCs from stdout if present.
    tail = "\n".join(p.stdout.strip().splitlines()[-4:])
    return f"trained on {seasons} | {tail}"


_PREGAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup", "Delayed Start"}


def _slate_lock_state(day: date) -> tuple[bool, str, int, int]:
    """
    Return (locked, msg, started_count, total_count).

    Locked = at least one game on `day`'s slate is no longer in a pre-game
    state. Once first pitch happens anywhere, we treat the actionable slate
    as frozen — the pre-first-pitch picks file shouldn't be overwritten by
    in-game odds (which the model isn't trained on and which produce
    nonsense edges).
    """
    try:
        from mlb_edge import data_ingestion as di
        schedule = di.fetch_schedule_mlb_api(day)
    except Exception as e:
        return (False, f"schedule check failed: {e}", 0, 0)
    if not schedule:
        return (False, "no games scheduled", 0, 0)
    started = [g for g in schedule if g.get("status") not in _PREGAME_STATUSES]
    if started:
        return (True, f"{len(started)}/{len(schedule)} games past first pitch",
                len(started), len(schedule))
    return (False, "all games pre-game", 0, len(schedule))


def job_predict_slate(day: date | None = None) -> str:
    day = day or date.today()
    locked, lock_msg, started, total = _slate_lock_state(day)
    stamp = day.strftime("%Y%m%d")
    # When locked, divert to an _inflight file so the pre-first-pitch
    # picks_YYYYMMDD.csv stays frozen as the actionable slate of record.
    out_name = f"picks_{stamp}_inflight.csv" if locked else f"picks_{stamp}.csv"
    out = ROOT / out_name
    cmd = [sys.executable, "-m", "mlb_edge.main",
           "--mode", "predict",
           "--date", day.isoformat(),
           "--model_path", "models/latest.pkl",
           "--out", str(out),
           "--bankroll", "100"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"predict rc={p.returncode}: {p.stderr[-600:]}")
    lines = p.stdout.strip().splitlines()
    total_line = next((l for l in reversed(lines) if l.startswith("Total bets")), "")
    if locked:
        return f"LOCKED ({started}/{total} live) — {out.name} | {total_line}"
    return f"{out.name} | {total_line}"


def job_health_check() -> str:
    """Nightly integrity sanity check."""
    from mlb_edge.data_sources.bref import latest_date_on_or_before
    today = date.today()
    bref_latest = latest_date_on_or_before(today)
    model_mtime = None
    mp = ROOT / "models" / "latest.pkl"
    if mp.exists():
        model_mtime = datetime.fromtimestamp(mp.stat().st_mtime)

    warnings = []
    if bref_latest is None:
        warnings.append("no B-R standings snapshot found")
    elif (today - bref_latest).days > 2:
        warnings.append(f"B-R standings stale ({bref_latest}, {(today - bref_latest).days}d old)")

    if model_mtime is None:
        warnings.append("models/latest.pkl missing")
    elif (datetime.now() - model_mtime).days > 2:
        warnings.append(f"model stale ({model_mtime:%Y-%m-%d}, {(datetime.now() - model_mtime).days}d old)")

    if warnings:
        _notify("mlb_edge health check", "; ".join(warnings))
        return "WARN: " + "; ".join(warnings)
    return f"bref={bref_latest} model={model_mtime:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Scheduler loop (stdlib only — no `schedule` dep)
# ---------------------------------------------------------------------------
def _morning_daily_jobs() -> bool:
    ok = True
    ok &= _run("standings",     job_refresh_standings)
    ok &= _run("savant",        job_refresh_savant)
    ok &= _run("bat_tracking",  job_refresh_bat_tracking)
    ok &= _run("retrain",       job_retrain)
    ok &= _run("predict",       job_predict_slate)
    return ok


def _slate_window_jobs() -> bool:
    return _run("predict", job_predict_slate)


def _nightly_jobs() -> bool:
    return _run("health_check", job_health_check)


# Schedule: (hour, minute, func, label)
# Times are LOCAL time, assumed US-Eastern adjacent (user lives on-schedule
# with MLB slates). Adjust in config if you're on another zone.
_MORNING = (6, 30)
_NIGHTLY = (3, 0)
_SLATE_FROM = 11  # start at 11:00
_SLATE_UNTIL = 23  # last tick at 22:45
_SLATE_MIN = {0, 15, 30, 45}


def _is_due(now: datetime, last: dict) -> list[tuple[str, callable]]:
    due: list[tuple[str, callable]] = []
    hm = (now.hour, now.minute)
    today = now.date()

    if hm == _MORNING and last.get("morning") != today:
        due.append(("morning", _morning_daily_jobs))
        last["morning"] = today

    if hm == _NIGHTLY and last.get("nightly") != today:
        due.append(("nightly", _nightly_jobs))
        last["nightly"] = today

    if (_SLATE_FROM <= now.hour <= _SLATE_UNTIL
            and now.minute in _SLATE_MIN
            and last.get("slate") != (today, now.hour, now.minute)):
        due.append(("slate_tick", _slate_window_jobs))
        last["slate"] = (today, now.hour, now.minute)

    return due


def loop_forever() -> None:
    import time
    log.info("auto_runner: ENTER loop (morning=%02d:%02d, nightly=%02d:%02d, "
             "slate=%02d:00-%02d:45/15min)",
             *_MORNING, *_NIGHTLY, _SLATE_FROM, _SLATE_UNTIL)
    last: dict = {}
    while True:
        now = datetime.now()
        for label, fn in _is_due(now, last):
            log.info("auto_runner: tick %s at %s", label, now.strftime("%H:%M:%S"))
            fn()
        # Sleep to the next minute boundary.
        next_tick = (now.replace(second=0, microsecond=0)
                     + timedelta(minutes=1))
        time.sleep(max(1, (next_tick - datetime.now()).total_seconds()))


def run_once() -> int:
    """Run every job once, right now. Useful for manual catch-up."""
    ok = True
    ok &= _morning_daily_jobs()
    ok &= _nightly_jobs()
    return 0 if ok else 1


_JOBS = {
    "standings": job_refresh_standings,
    "savant": job_refresh_savant,
    "bat_tracking": job_refresh_bat_tracking,
    "retrain": job_retrain,
    "predict": job_predict_slate,
    "health": job_health_check,
}


def run_now(name: str) -> int:
    fn = _JOBS.get(name)
    if not fn:
        log.error("Unknown job %r. Available: %s", name, list(_JOBS))
        return 2
    return 0 if _run(name, fn) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="Run morning+nightly jobs once, exit.")
    ap.add_argument("--now", metavar="JOB",
                    help=f"Run one job and exit. One of: {','.join(_JOBS)}")
    args = ap.parse_args(argv)

    if args.now:
        return run_now(args.now)
    if args.once:
        return run_once()
    loop_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
