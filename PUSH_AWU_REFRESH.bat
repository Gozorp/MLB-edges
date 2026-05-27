@echo off
REM Manually fire awu.run for 2026-05-26 so weights_state_freshness
REM unstucks. The workflow's automatic awu.run kept short-circuiting
REM because outcomes for 5/26 weren't fully resolved during earlier
REM cron runs. Most West-Coast games are final now.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Sync to clean origin first.
git fetch origin main
git reset --hard origin/main

REM Force the awu.run for yesterday's slate (5/26 from PT perspective).
python -c "import sys; sys.path.insert(0, '.'); from datetime import date; from mlb_edge import auto_weight_update as awu; awu.run(date(2026, 5, 26), force=True)"
if errorlevel 1 ( echo awu.run failed & pause & exit /b 1 )

REM Stage state + audit log.
git add data\state\weights_state.json data\state\recalibration_log.jsonl PUSH_AWU_REFRESH.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "self-learn: manual awu.run for 2026-05-26 (unstuck weights_state_freshness)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Audit log updated. weights_state_freshness should flip to GREEN
    echo on the next health-check cron ^(top of next hour^).
) else (
    echo no changes to commit ^(awu produced zero delta?^)
)
pause
