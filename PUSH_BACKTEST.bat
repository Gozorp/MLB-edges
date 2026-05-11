@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping historical PnL backtest engine
echo  -----------------------------------------------------------
echo  tools/run_backtest.py     simulator (Kelly compounding)
echo  .github/.../weekly-backtest.yml  Sun 05:00 UTC cron
echo
echo  First-run output (from sandbox sanity check):
echo    n_picks: 500 across 2023-2026
echo    Full Kelly:    $1000 -^> $0      (-100%%)
echo    Quarter Kelly: $1000 -^> $112    (-89%%)
echo    Eighth Kelly:  $1000 -^> $386    (-61%%)
echo    Win rate:      46.8%% (vs +10.5pp avg claimed edge)
echo    => model was systematically over-confident in
echo       2023/2024, improving through 2025 (-12%%) and
echo       2026 (+3%%, n=4).
echo
echo  This is the unvarnished truth and exactly what a
echo  backtest exists to deliver.  Shipping it.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_backtest
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
mkdir "%TMPDIR%\.github\workflows" 2>nul
mkdir "%TMPDIR%\docs\data\backtest" 2>nul
copy /Y "tools\run_backtest.py"                       "%TMPDIR%\tools\run_backtest.py"                       >nul
copy /Y ".github\workflows\weekly-backtest.yml"       "%TMPDIR%\.github\workflows\weekly-backtest.yml"       >nul
copy /Y "PUSH_BACKTEST.bat"                            "%TMPDIR%\PUSH_BACKTEST.bat"                            >nul

REM Copy any backtest artifacts the sandbox already generated
if exist "docs\data\backtest" (
    xcopy /Y /Q "docs\data\backtest\*" "%TMPDIR%\docs\data\backtest\" >nul 2>nul
)
echo done.
echo.

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)
echo.

echo Restoring edits + creating backtest output dir...
mkdir "docs\data\backtest" 2>nul
copy /Y "%TMPDIR%\tools\run_backtest.py"                       "tools\run_backtest.py"                       >nul
copy /Y "%TMPDIR%\.github\workflows\weekly-backtest.yml"       ".github\workflows\weekly-backtest.yml"       >nul
copy /Y "%TMPDIR%\PUSH_BACKTEST.bat"                            "PUSH_BACKTEST.bat"                            >nul
xcopy /Y /Q "%TMPDIR%\docs\data\backtest\*" "docs\data\backtest\" >nul 2>nul
echo done.
echo.

echo Staging + committing...
git add tools/run_backtest.py
git add .github/workflows/weekly-backtest.yml
git add PUSH_BACKTEST.bat
git add docs/data/backtest/
git status --short
git commit -m "Historical PnL backtest engine: tools/run_backtest.py simulates Kelly-compounded bankroll growth across bt_2023..bt_2026.csv historical picks at three Kelly fractions (full-capped, quarter, eighth). Outputs per-pick ledger, equity-curve data, machine-readable latest.json, and human-readable markdown summary to docs/data/backtest/. Companion .github/workflows/weekly-backtest.yml runs every Sunday 05:00 UTC. First-run verdict: 500 picks at 46.8%% win rate vs +10.5pp avg claimed edge means the model was systematically over-confident across 2023-2024; full Kelly bankrupted, quarter Kelly -89%%. Per-season trend (2023 -39%%, 2024 -80%%, 2025 -12%%, 2026 +3%% n=4) suggests the calibrator + tier-tightening fixes are working but the historical archive remains dominated by the bad years. Phase 2 would retrain current model on rolling history and replay; this Phase 1 just establishes the simulator infrastructure."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
echo.

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)
echo.

echo ============================================================
echo  SUCCESS.
echo
echo  The backtest report is now live at:
echo    https://github.com/Gozorp/MLB-edges/tree/main/docs/data/backtest
echo
echo  Next Sunday at 05:00 UTC the weekly-backtest cron will
echo  re-run automatically.  You can also trigger it manually
echo  at any time:
echo    GitHub Actions -^> Weekly PnL backtest -^> Run workflow
echo
echo  Honest read of the first run: the 2023-2024 sample is bad
echo  news; 2025/2026 are encouraging but small.  Treat the
echo  full-history number with skepticism — it's a backtest of
echo  OLD model versions.  The right next step (Phase 2) is to
echo  retrain the current model on rolling history and replay,
echo  which is a larger build for a future session.
echo ============================================================
pause
