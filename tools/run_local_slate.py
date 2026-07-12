#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_local_slate.py -- local end-to-end slate pipeline (Phase 1 self-hosting).

Replicates the cloud daily-slate.yml WITHOUT the git push:
  1. predict.py --skip-weights   -> picks_<date>_diag.csv (+ parlay) at repo root
  2. main_totals --mode predict  -> picks_totals_<date>.csv at repo root
  3. bake: copy picks_*_diag.csv / *_news_overrides.csv / picks_totals_*.csv /
     parlay_*.txt  ->  docs/data/   (local only, no commit/push)
  4. rebuild docs/data/manifest.json

Run from anywhere; it cd's to the repo root. Optional arg: YYYY-MM-DD slate date
(else predict.py's own default). Writes a concise local_slate_run.log for review;
predict/totals verbose output streams live to the console.
"""
import sys
import os
import glob
import re
import json
import shutil
import subprocess
import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

LOG = []
def log(s=""):
    print(s)
    LOG.append(str(s))

def flush():
    try:
        open("local_slate_run.log", "w", encoding="utf-8").write("\n".join(LOG) + "\n")
    except OSError as e:
        print("could not write log:", e)

def run(cmd, label, fatal=True):
    log("")
    log("=== %s ===" % label)
    log("  $ " + " ".join(cmd))
    sys.stdout.flush()
    r = subprocess.run(cmd)   # inherit stdio -> live console
    log("  -> exit code: %d" % r.returncode)
    if r.returncode != 0 and fatal:
        log("  FATAL: %s failed (see console output above)" % label)
        flush()
        sys.exit(r.returncode)
    return r.returncode

def newest_diag_date():
    fs = sorted(glob.glob("picks_*_diag.csv"), key=os.path.getmtime)
    if not fs:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fs[-1]))
    return m.group(1) if m else None

def main():
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    t0 = datetime.datetime.now()
    log("=" * 64)
    log("LOCAL SLATE PIPELINE   start " + t0.isoformat(timespec="seconds"))
    log("repo root: " + ROOT)
    log("python:    " + PY)
    log("=" * 64)

    # 0) standings snapshot refresh (2026-07-10 flaw fix: the B-R scraper died
    #    in April and team-quality gaps ran on 78-day-old records; this keeps
    #    the bref-format snapshot same-day from statsapi). Non-fatal.
    run([PY, "tools/refresh_standings_snapshot.py"],
        "standings snapshot refresh", fatal=False)

    # 1) predict
    run([PY, "predict.py"] + ([date_arg] if date_arg else []) + ["--skip-weights"],
        "predict.py --skip-weights", fatal=True)

    slate = date_arg or newest_diag_date()
    log("")
    log("slate date resolved: %s" % slate)

    # 2) totals (non-fatal -- matches cloud continue-on-error)
    if slate and os.path.exists("models/totals_latest.pkl"):
        run([PY, "-m", "mlb_edge.main_totals", "--mode", "predict",
             "--date", slate, "--out", "picks_totals_%s.csv" % slate],
            "totals predict", fatal=False)
    else:
        log("\n(totals skipped: no slate date or totals model)")

    # 3) bake -> docs/data/
    log("")
    log("=== bake -> docs/data/ ===")
    dd = Path("docs/data")
    dd.mkdir(parents=True, exist_ok=True)
    n = 0
    for pat in ("picks_*_diag.csv", "picks_*_news_overrides.csv",
                "picks_totals_*.csv", "parlay_*.txt"):
        for f in glob.glob(pat):
            _dst = str(dd / os.path.basename(f))
            shutil.copy2(f, _dst + ".tmp")
            os.replace(_dst + ".tmp", _dst)  # atomic: an interrupted copy (AV lock) can't leave a torn file
            n += 1
    log("  copied %d files into docs/data/" % n)

    # 3.5) input-coverage sidecar (display/telemetry only; feeds the July
    #      coverage-soft-cap prereg with accumulated per-bake data).
    #      flush() first: the sidecar parses local_slate_run.log for the
    #      Savant/FanGraphs endpoint counts of THIS run, not the previous one.
    if slate:
        flush()
        run([PY, "tools/feature_coverage_report.py", slate],
            "feature coverage sidecar", fatal=False)

    # 3.55) K-prop odds ingestion (shadow legs for the combo pool). Chain-safe
    #       no-op without ODDS_API_KEY; 20h internal cache means at most ONE
    #       real API fetch per day even from the hourly job (quota guard).
    if slate:
        run([PY, "tools/kprop_odds.py", slate],
            "kprop odds sidecar", fatal=False)

    # 3.6) correlated-combo sidecar (display-only: ML+F5 within-game double
    #      w/ ledger-derived correlation + unanimous consensus gates; feeds
    #      the COMBO chip card that replaced Best Pick). Non-fatal.
    if slate:
        run([PY, "tools/correlated_combo.py", slate],
            "correlated combo sidecar", fatal=False)

    # 4) manifest.json
    dates = set()
    for f in dd.glob("picks_*_diag.csv"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if m:
            dates.add(m.group(1))
    (dd / "manifest.json.tmp").write_text(json.dumps({"dates": sorted(dates, reverse=True)}, indent=2))
    os.replace(str(dd / "manifest.json.tmp"), str(dd / "manifest.json"))  # atomic
    log("  manifest.json -> %d dates" % len(dates))

    # summary
    el = (datetime.datetime.now() - t0).total_seconds()
    log("")
    log("=" * 64)
    log("DONE in %.0fs.  slate=%s" % (el, slate))
    if slate:
        dfp = dd / ("picks_%s_diag.csv" % slate)
        if dfp.exists():
            import csv
            csv.field_size_limit(10 ** 7)
            rows = list(csv.DictReader(open(dfp, encoding="utf-8")))
            staked = [r for r in rows if (r.get("tier") or "") not in ("SKIP", "PENDING_SP_DATA", "")]
            # game_picks = count Top Probable Outcomes will show in Game Picks
            # (mirrors the frontend _topGameMLPicks guardrail: edge>0, grade in
            # A/B/C, not SKIP/pending). Passive record so a thin slate is visible
            # in the log; -1 = count failed (never raises into the chain).
            try:
                _GW = {"A", "A-", "B+", "B", "B-", "C"}

                def _fe(x):
                    try:
                        return float(x)
                    except Exception:
                        return None
                game_picks = sum(
                    1 for r in rows
                    if (_fe(r.get("edge_pp")) or 0) > 0
                    and (r.get("grade") or "").strip() in _GW
                    and "SKIP" not in (r.get("tier") or "").upper()
                    and "PENDING_SP_DATA" not in (r.get("tier") or ""))
            except Exception:
                game_picks = -1
            log("  baked %s: %d games, %d non-SKIP tiers, game_picks=%d"
                % (dfp.name, len(rows), len(staked), game_picks))
    log("=" * 64)
    flush()

if __name__ == "__main__":
    main()
