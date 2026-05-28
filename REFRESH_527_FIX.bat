@echo off
REM Targeted refresh for the 2026-05-27 slate. Two games (MIN @ CHW,
REM NYY @ KC) ended Final per MLB API but the CSV still shows them as
REM TBD PENDING_SP_DATA. Re-run predict + bake to see if fresh data
REM resolves them.
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

set TARGET=2026-05-27

REM Step 1: predict (writes root CSVs).
call predict.bat %TARGET%
if errorlevel 1 ( echo predict failed & pause & exit /b 1 )

REM Step 2: show what predict produced for the two stuck games BEFORE baking.
echo.
echo === FRESH predict output for the stuck games ===
findstr "MIN @ CHW NYY @ KC" picks_%TARGET%_diag.csv
echo.

REM Step 3: bake into docs/data/.
copy /Y picks_%TARGET%_diag.csv docs\data\
if exist picks_%TARGET%_news_overrides.csv copy /Y picks_%TARGET%_news_overrides.csv docs\data\
if exist picks_totals_%TARGET%.csv copy /Y picks_totals_%TARGET%.csv docs\data\
if exist parlay_%TARGET%.txt copy /Y parlay_%TARGET%.txt docs\data\

REM Step 4: stage + commit + push.
git add docs\data\picks_%TARGET%_*.csv docs\data\picks_totals_%TARGET%.csv docs\data\parlay_%TARGET%.txt REFRESH_527_FIX.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: %TARGET% targeted refresh (resolve stuck MIN@CHW + NYY@KC)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Refreshed %TARGET% slate. Dashboard updates within ~60s.
) else (
    echo no changes to commit ^(predict produced identical output^)
)
pause
