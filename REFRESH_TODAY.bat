@echo off
REM Refresh today's slate (predict + bake + push). Use this when lineups
REM or probable starters have been announced since the last cron run,
REM unsticking PENDING_SP_DATA rows.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git rebase --abort 2>nul
git merge --abort 2>nul

REM Compute today's UTC date (matches the workflow logic).
for /f "tokens=*" %%i in ('powershell -nologo -command "(Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')"') do set TODAY=%%i
echo Refreshing slate for %TODAY%

git fetch origin main
git reset --hard origin/main

REM Step 1: predict (writes root-level CSVs).
call predict.bat %TODAY%
if errorlevel 1 ( echo predict failed & pause & exit /b 1 )

REM Step 2: bake — copy root CSVs into docs/data/ where the dashboard reads.
if exist picks_%TODAY%_diag.csv ( copy /Y picks_%TODAY%_diag.csv docs\data\ ) else ( echo no root picks CSV produced & pause & exit /b 1 )
if exist picks_%TODAY%_news_overrides.csv ( copy /Y picks_%TODAY%_news_overrides.csv docs\data\ )
if exist picks_totals_%TODAY%.csv ( copy /Y picks_totals_%TODAY%.csv docs\data\ )
if exist parlay_%TODAY%.txt ( copy /Y parlay_%TODAY%.txt docs\data\ )

REM Step 3: stage only the things we want to commit.
git add docs\data\picks_%TODAY%_*.csv docs\data\picks_totals_%TODAY%.csv docs\data\parlay_%TODAY%.txt REFRESH_TODAY.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: %TODAY% manual refresh (PENDING_SP_DATA cleanup)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo %TODAY% slate refreshed + pushed. Dashboard picks up within ~60s.
    echo Any remaining TBDs are picks_locked rows ^(past Preview status^)
    echo or genuinely-unannounced SPs that the next cron will catch.
) else (
    echo no slate changes to commit ^(picks already match origin^)
)
pause
