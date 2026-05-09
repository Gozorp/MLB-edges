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
    git add .github/workflows/claude-postgame.yml
    git add .github/workflows/claude-weekly.yml
    git add PUSH_FIX.bat
    git add QUICK_PUSH.bat
    git add functions/
    git add src/
    git add wrangler.toml
    git add mlb_edge/post_calibrator.py
    git add mlb_edge/parlay_builder.py
    git add mlb_edge/main_predict.py
    git add mlb_edge/claude_analyzer.py
    git add models/calibration_v1.json
    git add tools/refit_post_calibrator.py
    git add tools/run_claude_postgame.py
    git add tools/run_claude_weekly.py

    echo --- staged ---
    git diff --cached --name-only

    echo --- commit ---
    git commit -m "Claude integration (Opus 4.6) - 4 features: (1) nightly post-mortem cron at 03:30 UTC sends each completed game's pick + outcome to Claude, writes per-game verdict/headline/hypothesis to docs/data/postgame/<date>.json, dashboard surfaces in expanded panel; (2) Ask Claude Q&A widget on the dashboard, hits Worker /api/claude/ask which forwards to Anthropic with the loaded slate as context; (3) /api/claude/commentary endpoint for in-game live commentary; (4) Sunday weekly memo cron generates Markdown review of last 7 days. Plus: odds sanity cap (fair_prob outside [0.10, 0.90] treated as missing) catches the parser bug seen May 6/7/9. Setup: ANTHROPIC_API_KEY needed as GitHub secret + Cloudflare Worker secret."
    echo commit exit: !errorlevel!

    echo --- push ---
    git push 2>&1
    echo push exit: !errorlevel!

    echo --- final state ---
    git log -2 --oneline
)
notepad "%LOG%"
exit /b 0
