@echo off
REM Commit the 60-day luck-adjusted self-correction re-probe baseline.
REM Locks in: (1) the 60-day NULL-result probe artifacts, (2) the extended
REM game_xwoba_log.csv (787 game_pk rows / 60 dates). Same recovery pattern
REM as PUSH_LUCK_PROBE_BASELINE.bat.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Hard-sync to clean origin. Untracked baseline files survive reset.
git fetch origin main
git reset --hard origin/main

REM game_xwoba_log.csv is TRACKED, so reset --hard reverts it to the 400-row
REM origin version. Restore the merged 787-row file before staging.
copy /Y D:\mlb_edge\game_xwoba_log_merged_0626.csv data\postgame\game_xwoba_log.csv

REM Stage SPECIFIC files only (the _xwoba_incr_*.csv and _*_tmp.py helpers
REM stay untracked — they were intermediate, not deliverables).
git add data\postgame\game_xwoba_log.csv
git add data\baselines\luck_adjusted_2026-04-27_to_2026-06-26\README.md
git add data\baselines\luck_adjusted_2026-04-27_to_2026-06-26\probe.py
git add data\baselines\luck_adjusted_2026-04-27_to_2026-06-26\picks_with_xwoba.csv
git add data\baselines\luck_adjusted_2026-04-27_to_2026-06-26\summary.json
git add PUSH_LUCK_PROBE_REPROBE.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore(observability): persist 60-day luck-adjusted self-correction re-probe baseline" -m "Locked 2026-06-26 re-probe (Rule 2, tighter +10pp KEEP criterion on the doubled sample). Joined n=659: bad_beat n=64 (169/296 5-game window = 57.09%), bad_read n=190 (449/900 = 49.89%), null_zone n=85, win n=320. Delta = +7.21pp. WIDENED from the 30-day +5.37pp as the sample doubled (hypothesis strengthening), but still inside the locked null zone [-3pp, +10pp]. Verdict: NULL — no code change to apply_calibration_from_all_picks. 90-day re-probe scheduled 2026-07-26, same +10pp criterion." -m "xwOBA archive extended to 787 game_pk rows / 60 dates (2026-04-27 .. 2026-06-25) via tools/backfill_game_xwoba.py pulled to a separate --out file then merged by game_pk (the script OVERWRITES its out target, so never aimed at game_xwoba_log.csv directly). probe.py is a clean rebuild — the 05-26 copy had a corrupted duplicated tail; this one truncates to the single valid module end and ast-parses. Locked spec unchanged: project_luck_adjusted_probe_thresholds.md (X=+0.025, Y=+10pp re-probe, KILL=-3pp, 5-game window)."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo 60-day luck-adjusted re-probe baseline locked in project history.
    echo Verdict NULL +7.21pp. 90-day re-probe due 2026-07-26.
) else (
    echo no changes to commit
)
pause
