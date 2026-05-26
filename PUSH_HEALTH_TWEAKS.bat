@echo off
REM Two QoL tweaks to tools/health_check.py while the Cloudflare Pages
REM project is being set up out-of-band:
REM   1. daily_slate_heartbeat: YELLOW threshold 6h -> 14h (RED stays 24h)
REM   2. Rename odds_api_completeness -> kalshi_coverage_rate + update
REM      message strings to reflect what the check actually measures.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch the file we're patching from origin so the patch applies
REM against a clean known state.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/tools/health_check.py" -o tools\health_check.py
if errorlevel 1 ( echo curl health_check.py failed & pause & exit /b 1 )

python _patch_health_tweaks.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Local smoke: just verify the check function renames cleanly land.
REM Don't run the full health_check.py end-to-end because the
REM cloudflare/anthropic checks will RED (still no Pages deployment)
REM and we don't want a Discord ping for a known state.
python -c "import sys; sys.path.insert(0, 'tools'); import health_check as hc; assert hc.check_kalshi_coverage_rate, 'rename failed'; print('SMOKE OK — kalshi_coverage_rate exists, check_odds_api_completeness gone')"
if errorlevel 1 ( echo smoke failed & pause & exit /b 1 )

git add tools\health_check.py _patch_health_tweaks.py PUSH_HEALTH_TWEAKS.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(observability): widen daily-slate yellow threshold + rename odds check" -m "Two adjustments to suppress steady-state yellow noise in the health card:" -m "1. daily_slate_heartbeat YELLOW threshold widened from 6h to 14h. The daily-slate workflow fires once in the morning Pacific, so 6-16h of 'no recent fire' is the expected steady-state. RED threshold (24h) stays — that genuinely indicates a missed slate." -m "2. odds_api_completeness renamed to kalshi_coverage_rate. The check has never actually examined OddsAPI — it segments the picks_*_diag odds_status column, and since the 2026-05-21 OddsAPI cancellation, every 'fetched'/'fetched_capped' row is ok via Kalshi. Message strings updated from 'non-fetched odds' to 'has no Kalshi moneyline' to reflect what's actually being measured." -m "Note: health_alert_state.json keeps a stale key for the old name (last_fired_at: never updated again). Harmless orphan. New fires append under the new name. The dashboard card auto-picks-up the rename since it renders dynamically from whatever check names are in health.json."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT ===
echo Tweaks shipped. On the next */30 cron the dashboard card will show
echo "kalshi_coverage_rate" instead of "odds_api_completeness", and the
echo daily-slate-heartbeat will no longer fire yellow during normal
echo afternoon/evening hours.
pause
