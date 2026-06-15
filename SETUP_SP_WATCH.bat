@echo off
REM ============================================================================
REM SETUP_SP_WATCH.bat -- register the SP-release watcher (tools/sp_release_check.py)
REM   as a Windows scheduled task.
REM
REM   *** STAGED -- NOT auto-registered. Run this manually ONLY when ready to enable. ***
REM   Recommended: run it stateside for a few days to monitor, OR enable at the
REM   ~6/21 ops swap. Do NOT flip it on silently going into the unattended trip.
REM
REM   Cadence: every 15 min. The script itself no-ops outside 06:00-16:00 local and
REM   rebuilds the slate ONLY when a previously-PENDING game's probable drops
REM   (one cheap statsapi call per tick; single-instance lock; daily rebuild cap).
REM   Triggers the FROZEN chain (run_local_slate + publish_local) -- no model change.
REM
REM   Disable / remove:  schtasks /delete /tn mlb_edge_sp_watch /f
REM ============================================================================
schtasks /create /tn mlb_edge_sp_watch /tr "C:\Python313\python.exe D:\mlb_edge\mlb_edge\tools\sp_release_check.py" /sc minute /mo 15 /st 06:00 /et 16:30 /k /f
echo.
echo Registered mlb_edge_sp_watch (every 15 min, 06:00-16:30 window).
echo Verify:  schtasks /query /tn mlb_edge_sp_watch
echo Disable: schtasks /delete /tn mlb_edge_sp_watch /f
