@echo off
REM ============================================================================
REM SETUP_T30_WATCH.bat -- register the per-game T-30 refresh+lock watcher
REM   (tools/t30_watch.py) as a Windows scheduled task.
REM
REM   *** FEATURE BRANCH ONLY (feat/t30-rolling-scheduler).  STAGED -- DO NOT RUN
REM       until you are back from the trip and have monitored it. ***
REM
REM   Production (main) stays on the stable daily/midday cron while you are away.
REM   This watcher is NOT on main and is NOT registered by default.
REM
REM   Cadence: every 15 min.  t30_watch.py itself no-ops outside game hours,
REM   single-instances, locks each game at the first tick >= its first-pitch-30min,
REM   and writes SHADOW output to offline_t30\ (never docs/data) unless you later
REM   wire it to publish.  Add --rebuild to lock a freshly-rebuilt slate.
REM
REM   Enable:  (after merging the branch / on return)
REM     schtasks /create /tn mlb_edge_t30_watch /tr "C:\Python313\python.exe D:\mlb_edge\mlb_edge\tools\t30_watch.py --rebuild" /sc minute /mo 15 /st 08:00 /et 23:30 /k /f
REM   Verify: schtasks /query /tn mlb_edge_t30_watch
REM   Disable: schtasks /delete /tn mlb_edge_t30_watch /f
REM ============================================================================
echo This is STAGED. T-30 watcher is branch-only and not enabled.
echo To enable on return, run the schtasks /create line documented inside this file.
