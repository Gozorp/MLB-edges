@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping umpire visibility + weekly refresh cron
echo  -----------------------------------------------------------
echo  Audit verdict: umpire was WIRED into the model but the DB
echo  was 14 days stale and the signal was invisible to the
echo  dashboard / Claude.  Two surgical fixes:
echo
echo    1. main_predict.py       expose ump_k_pct_delta and
echo                             ump_bb_pct_delta in the diag CSV
echo                             so dashboard + Claude can see
echo                             WHICH umpire is influencing each
echo                             projection.
echo    2. umpire-refresh.yml    new GitHub Actions cron rebuilding
echo                             data/umpire_*.parquet every Monday
echo                             04:00 UTC.  Closes the staleness
echo                             gap permanently.
echo ============================================================
echo.

echo [1/8] Clearing stale lock if present...
del /f /q ".git\index.lock" 2>nul
echo done.
echo.

echo [2/8] Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_umpire
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\.github\workflows" 2>nul
copy /Y "mlb_edge\main_predict.py"                "%TMPDIR%\mlb_edge\main_predict.py"                >nul
copy /Y ".github\workflows\umpire-refresh.yml"    "%TMPDIR%\.github\workflows\umpire-refresh.yml"    >nul
copy /Y "PUSH_UMPIRE.bat"                         "%TMPDIR%\PUSH_UMPIRE.bat"                         >nul
echo done.
echo.

echo [3/8] Fetching from origin...
git fetch origin
if errorlevel 1 (
    echo ^>^>^> FAILED at git fetch. Try running it in a normal terminal.
    pause
    exit /b 1
)
echo done.
echo.

echo [4/8] Local HEAD vs origin/main:
echo Local:
git rev-parse --short HEAD
echo Origin:
git rev-parse --short origin/main
echo.

echo [5/8] Hard-resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (
    echo ^>^>^> FAILED at reset. Another git process may be running.
    pause
    exit /b 1
)
echo done.
echo.

echo [6/8] Restoring edits on top of synced state...
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"                "mlb_edge\main_predict.py"                >nul
copy /Y "%TMPDIR%\.github\workflows\umpire-refresh.yml"    ".github\workflows\umpire-refresh.yml"    >nul
copy /Y "%TMPDIR%\PUSH_UMPIRE.bat"                         "PUSH_UMPIRE.bat"                         >nul
echo done.
echo.

echo [7/8] Staging + committing...
git add mlb_edge/main_predict.py .github/workflows/umpire-refresh.yml PUSH_UMPIRE.bat
git status --short
git commit -m "Umpire: surface ump_k_pct_delta and ump_bb_pct_delta in diag CSV + weekly refresh cron. The umpire signal was already wired into the model (v13 feature, see build_pipeline.py:695-697) but invisible in downstream artifacts. Audit found the DB was 14 days stale (last rebuild 2026-04-26 vs season in mid-May). New workflow .github/workflows/umpire-refresh.yml rebuilds data/umpire_assignments.parquet and data/umpire_effects.parquet every Monday at 04:00 UTC via the existing scripts/build_umpire_db.py, using the same fresh-clone push pattern as claude-brain and claude-postgame. The two new diag columns let the dashboard surface umpire context (next session) and let Claude reference the umpire's K%%/BB%% deltas when reasoning about pitcher-friendly vs hitter-friendly projections."
if errorlevel 1 (
    echo ^>^>^> Commit returned non-zero. Possibly nothing to commit.
    pause
    exit /b 1
)
echo.

echo [8/8] Pushing to origin/main...
git push origin HEAD:main
if errorlevel 1 (
    echo ^>^>^> PUSH FAILED. Try: git push   in a normal terminal.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo  SUCCESS. Two things to verify after the push lands:
echo
echo  1. Tomorrow's daily-slate (06:00 UTC) will emit the new
echo     ump_k_pct_delta and ump_bb_pct_delta columns.  Check
echo     docs/data/picks_2026-05-11_diag.csv for them.
echo
echo  2. Next Monday's 04:00 UTC umpire-refresh cron will fire
echo     for the first time.  Watch
echo     https://github.com/Gozorp/MLB-edges/actions
echo     for the green check.  Or run it manually now via
echo     'workflow_dispatch' to validate it works end-to-end
echo     without waiting until Monday.
echo ============================================================
pause
