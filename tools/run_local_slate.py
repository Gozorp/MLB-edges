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
            log("  baked %s: %d games, %d non-SKIP tiers" % (dfp.name, len(rows), len(staked)))
    log("=" * 64)
    flush()

if __name__ == "__main__":
    main()
