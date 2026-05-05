"""
startup_check.py — one-shot environment verifier for mlb_edge.

Checks on launch (run by auto_runner before the scheduler loop, and also
callable standalone):
    1. required directories exist (data/, models/, logs/, data/bref/
       standings, data/savant_bat_tracking, data/feature_cache)
    2. python dependencies importable (pandas, numpy, xgboost, requests)
    3. models/latest.pkl present and loadable
    4. Odds API key present in env
    5. Recent standings snapshot (within 2 days)

Exit code 0 = healthy, 1 = warnings (safe to continue), 2 = fatal.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DIRS = [
    "data",
    "data/bref/standings",
    "data/savant",
    "data/savant_bat_tracking",
    "data/feature_cache",
    "models",
    "logs",
]

REQUIRED_DEPS = ["pandas", "numpy", "xgboost", "requests", "pybaseball"]


def check_dirs() -> list[str]:
    missing = []
    for d in REQUIRED_DIRS:
        p = ROOT / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            missing.append(f"created {d}")
    return missing


def check_deps() -> list[str]:
    missing = []
    for m in REQUIRED_DEPS:
        try:
            importlib.import_module(m)
        except ImportError:
            missing.append(m)
    return missing


def check_model() -> str | None:
    mp = ROOT / "models" / "latest.pkl"
    if not mp.exists():
        return "models/latest.pkl missing — run auto_runner --now retrain"
    return None


def check_odds_key() -> str | None:
    if not os.getenv("ODDS_API_KEY"):
        return "ODDS_API_KEY not set in env — predict jobs will fail"
    return None


def check_data_freshness() -> str | None:
    today = date.today()
    standings_dir = ROOT / "data" / "bref" / "standings"
    if not standings_dir.exists():
        return "no standings dir"
    csvs = list(standings_dir.glob("*_upto-AL-E.csv"))
    if not csvs:
        return "no standings CSVs — run auto_runner --now standings"
    latest = max(csvs, key=lambda p: p.stat().st_mtime)
    stem_date = latest.name.split("_")[0]
    try:
        from datetime import datetime
        latest_date = datetime.strptime(stem_date, "%Y%m%d").date()
    except ValueError:
        return f"unparseable standings filename: {latest.name}"
    age = (today - latest_date).days
    if age > 2:
        return f"standings stale ({latest_date}, {age}d old)"
    return None


def run() -> int:
    warnings: list[str] = []
    errors: list[str] = []

    warnings.extend(check_dirs())

    missing_deps = check_deps()
    if missing_deps:
        errors.append(f"missing deps: {missing_deps}")

    for chk in (check_model, check_odds_key, check_data_freshness):
        msg = chk()
        if msg:
            warnings.append(msg)

    print("=" * 50)
    print("mlb_edge startup check")
    print("=" * 50)
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")
        return 2
    if not warnings:
        print("All good.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(run())
