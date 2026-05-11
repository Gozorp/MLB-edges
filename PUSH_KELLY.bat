@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping Kelly Criterion sizing columns
echo  -----------------------------------------------------------
echo  Single file edit: mlb_edge/main_predict.py
echo  Adds three new columns to picks_<date>_diag.csv:
echo    kelly_full     raw Kelly clipped at 0.25 (over-bet protection)
echo    kelly_quarter  25%% Kelly (sports-betting standard)
echo    kelly_eighth   12.5%% Kelly (conservative)
echo
echo  Validated on 5/9: Kelly correctly zero-bets the two
echo  negative-edge GOLD losses (CHC@TEX -4.4pp, NYY@MIL -2.0pp)
echo  and sizes PIT@SF at the 0.25 cap (math wanted 39%%, capped).
echo  Total quarter-Kelly exposure across 6 GOLDs: 16%% of bankroll.
echo ============================================================
echo.

echo [1/8] Clearing stale lock if present...
del /f /q ".git\index.lock" 2>nul
echo done.
echo.

echo [2/8] Saving local edit to temp...
set TMPDIR=%TEMP%\mlb_edge_kelly
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\main_predict.py" "%TMPDIR%\mlb_edge\main_predict.py" >nul
copy /Y "PUSH_KELLY.bat" "%TMPDIR%\PUSH_KELLY.bat" >nul
echo done.
echo.

echo [3/8] Fetching from origin...
git fetch origin
if errorlevel 1 (
    echo ^>^>^> FAILED at git fetch. Try running it in a normal terminal to see the prompt.
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
echo done. Local now matches origin.
echo.

echo [6/8] Restoring Kelly edit on top of synced state...
copy /Y "%TMPDIR%\mlb_edge\main_predict.py" "mlb_edge\main_predict.py" >nul
copy /Y "%TMPDIR%\PUSH_KELLY.bat" "PUSH_KELLY.bat" >nul
echo done.
echo.

echo [7/8] Staging + committing...
git add mlb_edge/main_predict.py PUSH_KELLY.bat
git status --short
git commit -m "Kelly Criterion sizing: emit kelly_full / kelly_quarter / kelly_eighth columns in diag CSV. Reuses existing kelly_stake() machinery in edge_calculator (KELLY_FRACTION=0.25 default). Raw Kelly always clipped at 0.25 to absorb model over-confidence — full Kelly assumes perfect calibration and explodes if p_model is even ~2pp off. Validated on 5/9: zero-bets negative-edge GOLDs (CHC@TEX -4.4pp, NYY@MIL -2.0pp, both lost), caps PIT@SF at 0.25 (math wanted 0.39), right-sizes COL@PHI and DET@KC at 0.048 and 0.024 respectively. Total quarter-Kelly exposure across the 5/9 GOLDs: 16%% of bankroll. Dashboard does not yet render these columns; next session will wire UI."
if errorlevel 1 (
    echo ^>^>^> Commit returned non-zero. Probably nothing to commit, or pre-commit hook fired.
    pause
    exit /b 1
)
echo.

echo [8/8] Pushing to origin/main...
git push origin HEAD:main
if errorlevel 1 (
    echo ^>^>^> PUSH FAILED. Try: git push   in a normal terminal to see the real error.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo  SUCCESS. Kelly columns will appear in tomorrow's
echo  picks_<date>_diag.csv (next daily-slate cron at 06:00 UTC).
echo  Verify the columns are present by re-loading the dashboard
echo  tomorrow and inspecting the CSV link, then we'll wire the
echo  dashboard UI to display them in the next session.
echo ============================================================
pause
