@echo off
REM Lean push: clear stale lock, stage changes, commit, push.  Logs to quick_push.log.
set "LOG=%~dp0quick_push.log"
> "%LOG%" 2>&1 (
    echo === QUICK_PUSH start %date% %time% ===
    cd /d "%~dp0"
    cd

    echo --- killing any orphan git/python holding .git ---
    taskkill /F /IM git.exe 2>nul
    taskkill /F /IM python.exe 2>nul

    echo --- forcibly remove all .git lock files ---
    if exist ".git\index.lock"           del /f /q ".git\index.lock"           2>nul && echo deleted index.lock
    if exist ".git\HEAD.lock"            del /f /q ".git\HEAD.lock"            2>nul && echo deleted HEAD.lock
    if exist ".git\ORIG_HEAD.lock"       del /f /q ".git\ORIG_HEAD.lock"       2>nul && echo deleted ORIG_HEAD.lock
    if exist ".git\refs\heads\main.lock" del /f /q ".git\refs\heads\main.lock" 2>nul && echo deleted main.lock
    if exist ".git\refs\stash.lock"      del /f /q ".git\refs\stash.lock"      2>nul && echo deleted stash.lock

    echo --- git fetch ---
    git fetch origin main
    echo --- git reset --mixed origin/main ---
    git reset --mixed origin/main

    echo --- staging target files ---
    git add docs/index.html
    git add .github/workflows/daily-slate.yml
    git add .github/workflows/savant-harvest.yml
    git add .github/workflows/refit-calibrator.yml
    git add PUSH_FIX.bat
    git add QUICK_PUSH.bat
    git add functions/
    git add src/
    git add wrangler.toml
    git add mlb_edge/post_calibrator.py
    git add mlb_edge/parlay_builder.py
    git add models/calibration_v1.json
    git add tools/refit_post_calibrator.py

    echo --- staged ---
    git diff --cached --name-only

    echo --- commit ---
    git commit -m "Auto-refit calibrator: tools/refit_post_calibrator.py walks docs/data/picks_*.csv, pairs each model_prob with the actual game outcome (via MLB statsapi), and re-fits the binned-isotonic table with Beta(8) prior shrinkage + weighted PAV monotonicity. New refit-calibrator.yml workflow runs Sundays at 04:00 UTC, commits the updated models/calibration_v1.json if changed. Pure stdlib + urllib — no extra deps."
    echo commit exit: !errorlevel!

    echo --- push ---
    git push 2>&1
    echo push exit: !errorlevel!

    echo --- final state ---
    git log -2 --oneline
)
notepad "%LOG%"
exit /b 0
