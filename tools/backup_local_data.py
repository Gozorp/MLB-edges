#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/backup_local_data.py — automated daily backup of everything git does
NOT already protect (2026-07-10, security/stability hardening).

The repo itself (code, docs/data, models/latest.pkl, ledgers) is versioned on
GitHub — that's the offsite backup. What ISN'T protected is the laptop-local,
gitignored state this pipeline depends on:
  data/state/          weights/recal state           data/news_cache/  umpires, anchors
  data/postgame/       xwOBA logs                    data/shadow/      bp-fatigue shadow
  data/cache/, data/platoon_cache/                   models/           trained bundles
  jobs/                scheduler .bats               root untracked *.md design docs
  .env                 local secrets (stays local — E: is a local drive)

Writes  E:\\mlb_edge_backups\\mlb_edge_backup_YYYY-MM-DD.zip  (falls back to
C:\\mlb_edge_backups if E: is unavailable), keeps the newest 14, deletes older.
Idempotent per day (re-run overwrites today's zip atomically). Exit 0 on
success; nonzero + message on failure so Task Scheduler shows Last Result.

Register (done 2026-07-10):
  schtasks /create /tn mlb_edge_backup /sc DAILY /st 04:30
    /tr "\"C:\\Python313\\python.exe\" \"D:\\mlb_edge\\mlb_edge\\tools\\backup_local_data.py\""
"""
import datetime
import glob
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

DESTS = [r"E:\mlb_edge_backups", r"C:\mlb_edge_backups"]
KEEP = 14
INCLUDE_DIRS = ["data/state", "data/news_cache", "data/postgame", "data/shadow",
                "data/cache", "data/platoon_cache", "models", "jobs"]
INCLUDE_GLOBS = ["*.md", ".env"]          # root-level untracked docs + local env
EXCLUDE_EXT = {".tmp", ".lock"}


def dest_dir():
    for d in DESTS:
        drive = os.path.splitdrive(d)[0] + "\\"
        if os.path.exists(drive):
            os.makedirs(d, exist_ok=True)
            return d
    raise SystemExit("backup FAILED: no destination drive available")


def main():
    out_dir = dest_dir()
    today = datetime.date.today().isoformat()
    final = os.path.join(out_dir, "mlb_edge_backup_%s.zip" % today)
    tmp = final + ".tmp"

    n_files = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for d in INCLUDE_DIRS:
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for fn in files:
                    p = os.path.join(root, fn)
                    if os.path.splitext(fn)[1].lower() in EXCLUDE_EXT:
                        continue
                    try:
                        z.write(p, p)
                        n_files += 1
                    except OSError as e:
                        print("skip (busy?): %s (%r)" % (p, e))
        for pat in INCLUDE_GLOBS:
            for p in glob.glob(pat):
                if os.path.isfile(p):
                    try:
                        z.write(p, p)
                        n_files += 1
                    except OSError as e:
                        print("skip: %s (%r)" % (p, e))

    os.replace(tmp, final)   # atomic: a crash never leaves a half zip as current
    size_mb = os.path.getsize(final) / 1e6
    print("backup OK: %s  (%d files, %.1f MB)" % (final, n_files, size_mb))

    old = sorted(glob.glob(os.path.join(out_dir, "mlb_edge_backup_*.zip")))
    for p in old[:-KEEP]:
        try:
            os.remove(p)
            print("rotated out: %s" % p)
        except OSError as e:
            print("rotate failed: %s (%r)" % (p, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
