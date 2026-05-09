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
    git add PUSH_FIX.bat
    git add QUICK_PUSH.bat
    git add functions/
    git add src/
    git add wrangler.toml
    git add mlb_edge/post_calibrator.py
    git add mlb_edge/parlay_builder.py
    git add models/calibration_v1.json

    echo --- staged ---
    git diff --cached --name-only

    echo --- commit ---
    git commit -m "Model fixes from 144-pick historical eval: (1) post_calibrator.py applies binned-isotonic remap to f5_prob/full_prob (Brier 0.2562 -> 0.2439, -4.8%); fitted on 126 (prob,outcome) pairs Apr27-May7. (2) team_quality_modifier disabled (PLATINUM picks it pushed went 3-4 = 43% hit rate). (3) Large-negative-edge cap: edge_pp < -8pp forces grade<=C (Vegas-disagrees-strongly counter-vote)."
    echo commit exit: !errorlevel!

    echo --- push ---
    git push 2>&1
    echo push exit: !errorlevel!

    echo --- final state ---
    git log -2 --oneline
)
notepad "%LOG%"
exit /b 0
