@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping Over/Under (totals) integration
echo  -----------------------------------------------------------
echo  All three pieces in one push:
echo    1. .github/workflows/daily-slate.yml
echo       new step: run main_totals predict, emit
echo       picks_totals_^<date^>.csv (continue-on-error so a
echo       totals failure never blocks the moneyline slate)
echo    2. .github/workflows/bake-data.yml
echo       paths filter + cp step now include picks_totals_*.csv
echo    3. docs/index.html
echo       new O/U column after Pred (color-coded pill: green
echo       OVER, red UNDER, gray no-signal). Fetches
echo       picks_totals_^<date^>.csv at slate load and joins on
echo       AWAY @ HOME matchup key. Backward-compatible: shows
echo       "-" when the totals CSV doesn't exist yet.
echo
echo  Existing infrastructure being unlocked:
echo    - models/totals_latest.pkl  trained on 2023-2025
echo    - bt_totals_2023/2024/2025.csv backtest archive
echo    - mlb_edge/main_totals.py CLI (predict mode)
echo    All built, none of it was being run on cron until now.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_ou
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\.github\workflows" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y ".github\workflows\daily-slate.yml" "%TMPDIR%\.github\workflows\daily-slate.yml" >nul
copy /Y ".github\workflows\bake-data.yml"   "%TMPDIR%\.github\workflows\bake-data.yml"   >nul
copy /Y "docs\index.html"                   "%TMPDIR%\docs\index.html"                   >nul
copy /Y "PUSH_OU.bat"                       "%TMPDIR%\PUSH_OU.bat"                       >nul
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

echo Restoring edits...
copy /Y "%TMPDIR%\.github\workflows\daily-slate.yml" ".github\workflows\daily-slate.yml" >nul
copy /Y "%TMPDIR%\.github\workflows\bake-data.yml"   ".github\workflows\bake-data.yml"   >nul
copy /Y "%TMPDIR%\docs\index.html"                   "docs\index.html"                   >nul
copy /Y "%TMPDIR%\PUSH_OU.bat"                       "PUSH_OU.bat"                       >nul
echo done.
echo.

echo Staging + committing...
git add .github/workflows/daily-slate.yml
git add .github/workflows/bake-data.yml
git add docs/index.html
git add PUSH_OU.bat
git status --short
git commit -m "Over/Under (totals) integration: wire the existing main_totals predict mode into the daily-slate cron, surface as a color-coded O/U column on the dashboard. The model and backtest archive (bt_totals_2023/2024/2025.csv) have existed since the initial commit but were orphaned — no workflow ran predict mode, so picks_totals_today.csv was 19 days stale. Now: every daily-slate run emits picks_totals_<date>.csv alongside picks_<date>_diag.csv; bake-data copies it into docs/data/; dashboard joins on AWAY @ HOME matchup key and renders side (OVER/UNDER) + Vegas line + edge in runs as a pill. Totals failure is continue-on-error so it never blocks the moneyline slate."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
echo.

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)
echo.

echo ============================================================
echo  SUCCESS.
echo
echo  What happens next automatically:
echo  - The next daily-slate cron (06:00, 11:00, 14:00, 17:00,
echo    or 21:00 UTC, whichever fires next) will run main_totals
echo    predict and emit picks_totals_^<tomorrow^>.csv.
echo  - bake-data copies it into docs/data/ on commit.
echo  - Cloudflare Workers auto-redeploys the dashboard.
echo  - Reload the dashboard with a cache-bust:
echo    https://mlb-edges.saladin-alfaatih.workers.dev/?cb=ou1
echo  - You'll see the new O/U column starting with the next
echo    slate that the cron bakes.
echo
echo  To force a fresh slate right now: go to GitHub Actions
echo  -^> "Daily slate run" -^> Run workflow -^> Run.
echo ============================================================
pause
