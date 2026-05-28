@echo off
REM Run + bake + push the 2026-05-28 slate.
REM Same pattern as REFRESH_527_FIX.bat: hard-reset to clean origin,
REM run predict.bat, copy root CSVs into docs/data/, commit, push.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

git fetch origin main
git reset --hard origin/main

set TARGET=2026-05-28

REM Step 1: predict (writes root CSVs).
call predict.bat %TARGET%
if errorlevel 1 ( echo predict failed & pause & exit /b 1 )

REM Step 2: bake into docs/data/.
copy /Y picks_%TARGET%_diag.csv docs\data\ >nul
if exist picks_%TARGET%_news_overrides.csv copy /Y picks_%TARGET%_news_overrides.csv docs\data\ >nul
if exist picks_totals_%TARGET%.csv copy /Y picks_totals_%TARGET%.csv docs\data\ >nul
if exist parlay_%TARGET%.txt copy /Y parlay_%TARGET%.txt docs\data\ >nul

REM Step 3: stage + commit + push.
git add docs\data\picks_%TARGET%_*.csv docs\data\picks_totals_%TARGET%.csv docs\data\parlay_%TARGET%.txt RUN_528_SLATE.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: %TARGET% manual run+bake"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo %TARGET% slate is live. Dashboard updates within ~60s.
) else (
    echo no changes to commit
)
pause
