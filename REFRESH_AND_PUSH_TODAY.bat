@echo off
REM Force-refresh today's slate AND commit+push immediately.
REM Use this when the dashboard is showing stale TBDs that your
REM manual predict.bat run resolved but didn't push. The plain
REM predict.bat writes the CSV locally; without an immediate
REM commit+push, a subsequent automated workflow can overwrite
REM your fresh picks with earlier (less-resolved) lineup data.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

REM Pull latest first so we don't conflict with the cron.
git fetch origin main
git pull --rebase --autostash origin main

REM Run the predict pipeline for today.
call predict.bat 2026-05-26
if errorlevel 1 ( echo predict failed & pause & exit /b 1 )

REM Stage everything the slate run touches.
git add docs\data\picks_2026-05-26_diag.csv docs\data\picks_2026-05-26_news_overrides.csv docs\data\ data\state\ REFRESH_AND_PUSH_TODAY.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: 2026-05-26 manual refresh (lock-in)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    REM Defensive re-pull in case the cron raced us between predict and now.
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Slate refreshed and pushed. Dashboard will pick up within ~60s.
    echo.
    echo NOTE: Any remaining TBDs are data dependencies, not refresh
    echo issues — typically a probable starter not yet announced, or
    echo an SP with too few Statcast pitches to score (e.g. early
    echo callups). Those will resolve as lineups firm up.
) else (
    echo no slate changes to commit ^(everything already matches origin^)
)
pause
