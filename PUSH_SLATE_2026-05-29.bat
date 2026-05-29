@echo off
REM ===========================================================================
REM PUSH_SLATE_2026-05-29.bat
REM ---------------------------------------------------------------------------
REM WHAT: Publish the locally-regenerated 2026-05-29 slate to origin so the
REM       dashboard shows tonight's refresh instead of the early-AM version.
REM
REM WHY:  origin/docs/data has the 05-29 slate from the ~3am night-owl cron.
REM       A fresher local regen (19:05 PT) is baked locally but never pushed
REM       (diag/totals/parlay/bullpen_meta/series_meta all differ from origin).
REM       This bakes the root CSVs into docs/data and pushes them.
REM       NOTE: 6 of 15 games are scored; 9 are TBD (probable SP not yet
REM       announced). The TBDs auto-resolve on the morning daily-slate cron.
REM
REM SAFE: Uses pull --rebase --autostash (NOT reset --hard) so the fresh local
REM       slate is preserved. Stages only the 05-29 files. Mirrors the
REM       "Bake new slate files" step of daily-slate.yml + BAKE_AND_PUSH_TODAY.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM --- sync to origin WITHOUT discarding the fresh local slate ---
git fetch origin main
git pull --rebase --autostash origin main

REM --- bake: copy root 05-29 CSVs/TXT into docs/data (idempotent) ---
for %%F in (picks_2026-05-29_diag.csv picks_2026-05-29_news_overrides.csv picks_totals_2026-05-29.csv parlay_2026-05-29.txt) do (
    if exist "%%F" ( copy /Y "%%F" "docs\data\%%F" >nul & echo baked %%F )
)

REM --- stage ONLY the 05-29 artifacts (root + docs/data) + this script ---
git add picks_2026-05-29_diag.csv picks_2026-05-29_news_overrides.csv picks_totals_2026-05-29.csv parlay_2026-05-29.txt 2>nul
git add docs\data\picks_2026-05-29_diag.csv docs\data\picks_2026-05-29_news_overrides.csv docs\data\picks_totals_2026-05-29.csv docs\data\parlay_2026-05-29.txt docs\data\bullpen_meta_2026-05-29.json docs\data\series_meta_2026-05-29.json 2>nul
git add PUSH_SLATE_2026-05-29.bat 2>nul

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "daily-slate: 2026-05-29 manual bake + push (refresh tomorrow's slate; 6 scored / 9 TBD pending SP)"
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo 2026-05-29 slate pushed. Dashboard updates within ~60s
    echo ^(select 2026-05-29 in the date picker^). The 9 TBD games fill in
    echo automatically when the morning cron runs with confirmed pitchers.
) else (
    echo Nothing to commit -- local 05-29 already matches origin.
)
echo.
pause
