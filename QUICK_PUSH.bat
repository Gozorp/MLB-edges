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
    git add mlb_edge/main_predict.py
    git add models/calibration_v1.json
    git add tools/refit_post_calibrator.py

    echo --- staged ---
    git diff --cached --name-only

    echo --- commit ---
    git commit -m "Odds sanity cap: Shin-devigged fair_prob outside [0.10, 0.90] is treated as missing — caught 6 absurd values across May 6/7/9 slates (e.g. HOU @ CIN reporting 99.6 percent fair_prob for HOU). Tagged as odds_status=fetched_capped so the parser bug rate can be monitored, and downstream falls back to no-market path instead of baking impossible Vegas implieds into the slate."
    echo commit exit: !errorlevel!

    echo --- push ---
    git push 2>&1
    echo push exit: !errorlevel!

    echo --- final state ---
    git log -2 --oneline
)
notepad "%LOG%"
exit /b 0
