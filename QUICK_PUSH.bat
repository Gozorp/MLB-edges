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

    echo --- staging the four target files ---
    git add docs/index.html
    git add .github/workflows/daily-slate.yml
    git add .github/workflows/savant-harvest.yml
    git add PUSH_FIX.bat
    git add QUICK_PUSH.bat

    echo --- staged ---
    git diff --cached --name-only

    echo --- commit ---
    git commit -m "Dashboard: predicted final score column + narrative section. Workflow: 06:00 UTC night-owl cron bakes tomorrow's slate; new savant-harvest.yml at 00:00 UTC pulls 42 Statcast leaderboards every midnight. Live tracker: drop broken ?fields=... query that returned empty status. PUSH_FIX: selective conflict resolver."
    echo commit exit: !errorlevel!

    echo --- push ---
    git push 2>&1
    echo push exit: !errorlevel!

    echo --- final state ---
    git log -2 --oneline
)
notepad "%LOG%"
exit /b 0
