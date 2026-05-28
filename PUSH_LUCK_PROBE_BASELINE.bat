@echo off
REM Commit the luck-adjusted self-correction probe baseline + infrastructure.
REM Locks in: (1) the negative-result probe artifacts, (2) the reusable
REM backfill_game_xwoba.py script, (3) the locked schema doc.
REM Same recovery pattern as PUSH_LINEUP_BASELINE.bat.
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

REM Stage SPECIFIC files only (the chunk1-6 files in data/postgame/ from the
REM backfill stay untracked — they were intermediate, not deliverables).
git add tools\backfill_game_xwoba.py
git add data\postgame\game_xwoba_log_schema.md
git add data\postgame\game_xwoba_log.csv
git add data\baselines\luck_adjusted_2026-04-27_to_2026-05-26\README.md
git add data\baselines\luck_adjusted_2026-04-27_to_2026-05-26\probe.py
git add data\baselines\luck_adjusted_2026-04-27_to_2026-05-26\picks_with_xwoba.csv
git add data\baselines\luck_adjusted_2026-04-27_to_2026-05-26\summary.json
git add PUSH_LUCK_PROBE_BASELINE.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore(observability): persist luck-adjusted self-correction probe baseline + infrastructure" -m "Rule-2 pre-flight probe gating a proposed Bad Beat penalty mute (0.5x scalar) on apply_calibration_from_all_picks. Joined sample n=331 from 31-day archive: bad_beat n=33, bad_read n=94, null_zone n=47, win n=157. bad_beat cohort 5-game subsequent win rate = 54.79%, bad_read = 49.42%, delta = +5.37pp. Falls in locked null zone [-3pp, +8pp]. Verdict: NULL — no code change to apply_calibration_from_all_picks. Re-probe locked for 2026-06-26 with tighter +10pp KEEP criterion on the doubled sample." -m "Infrastructure added: tools/backfill_game_xwoba.py (stdlib+requests Statcast pitch-level aggregator using locked formula sum(estimated_woba_using_speedangle)/sum(woba_denom), per-team CWS->CHW/ATH->OAK abbrev mapping, game_pk join key to dodge doubleheader collisions). data/postgame/game_xwoba_log.csv = 400 games x 30 dates aggregated from Savant statcast_search/csv. data/postgame/game_xwoba_log_schema.md locks the methodology (correctly uses xwOBA numerator, not actual wOBA, which would have collapsed the Bad Beat bucket by mirroring scoreboard outcomes)." -m "Locked spec in memory: project_luck_adjusted_probe_thresholds.md (X=+0.025 xwOBA gate, Y=+8pp keep, -3pp kill, 5-game window, +10pp re-probe at 2026-06-26). Same docs-first/no-retroactive-tuning pattern as project_override_backtest_thresholds. Same baseline-folder structure as data/baselines/lineup_matchup_gap_2026-04-27_to_2026-05-26/ for direct file-by-file diff at the 60-day re-probe."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Luck-adjusted probe baseline + infrastructure locked in project history.
    echo Re-probe due 2026-06-26 ^(scheduled task wired separately^).
) else (
    echo no changes to commit
)
pause
