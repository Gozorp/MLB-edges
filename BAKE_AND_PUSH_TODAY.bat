@echo off
REM Bake today's already-generated root-level picks_*.csv into docs/data/
REM and push to origin. Use this when predict.bat ran successfully (root
REM CSVs are fresh) but the dashboard still shows stale picks because
REM no one copied them into docs/data/.
REM
REM This is the manual equivalent of the "Bake new slate files into
REM docs/data/" step from .github/workflows/daily-slate.yml lines 161-167.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git pull --rebase --autostash origin main

REM Bake — copy the root CSVs that predict.bat produced into docs/data/.
if exist picks_2026-05-26_diag.csv (
    copy /Y picks_2026-05-26_diag.csv docs\data\picks_2026-05-26_diag.csv
) else ( echo no root picks_2026-05-26_diag.csv to bake & pause & exit /b 1 )

if exist picks_2026-05-26_news_overrides.csv (
    copy /Y picks_2026-05-26_news_overrides.csv docs\data\picks_2026-05-26_news_overrides.csv
)

if exist picks_totals_2026-05-26.csv (
    copy /Y picks_totals_2026-05-26.csv docs\data\picks_totals_2026-05-26.csv
)

if exist parlay_2026-05-26.txt (
    copy /Y parlay_2026-05-26.txt docs\data\parlay_2026-05-26.txt
)

git add docs\data\picks_2026-05-26_diag.csv docs\data\picks_2026-05-26_news_overrides.csv docs\data\picks_totals_2026-05-26.csv docs\data\parlay_2026-05-26.txt BAKE_AND_PUSH_TODAY.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: 2026-05-26 manual bake (resolves 3 of 5 TBDs via late lineups)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Picks baked and pushed. Dashboard updates within ~60s.
    echo.
    echo Resolved this run: MIA @ TOR, NYY @ KC, ARI @ SF — late lineups.
    echo Still TBD: CHC @ PIT, CIN @ NYM — real data dependencies
    echo ^(thin SP Statcast data, probable SP not yet announced^).
) else (
    echo no staged changes to commit
)
pause
