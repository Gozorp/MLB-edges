@echo off
REM ===========================================================================
REM PUSH_HEALTHCHECK_DEPTH_FIX.bat
REM ---------------------------------------------------------------------------
REM WHAT: .github/workflows/health-check.yml   fetch-depth: 100  ->  0
REM
REM WHY:  refit_calibrator_heartbeat and weekly_backtest_heartbeat were RED
REM       with "no refit-calibrator: commit found in git log" -- FALSE ALARMS.
REM       Both weekly commits exist and ran 2026-05-24, but health-check.yml
REM       checked out only the last 100 commits. At ~24 commits/day
REM       health-check every 30m + daily-slate, depth 100 spans only ~4 days,
REM       so the Sunday weekly commits scrolled past the window by mid-week and
REM       git log --grep returned empty -- hitting the "no commit found" RED
REM       branch in tools/health_check.py. fetch-depth: 0 = full history.
REM       Heartbeat thresholds YELLOW 10d / RED 14d were already correct, so
REM       this one change flips both heartbeats to GREEN on the next cron.
REM
REM SAFE-PUSH per Rule 4: temp-copy -> fetch -> reset --hard origin/main ->
REM       restore -> syntax gate -> commit -> push.  Single-purpose per Rule 5.
REM       Why-in-commit per Rule 12.  This .bat ships as documentation, Rule 13.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM --- Rule 4: stash the edited file, sync to clean origin, restore it ---
copy /Y .github\workflows\health-check.yml "%TEMP%\hc_depth_fix.yml" >nul
git fetch origin main
git reset --hard origin/main
copy /Y "%TEMP%\hc_depth_fix.yml" .github\workflows\health-check.yml >nul

REM --- Guarantee the value even if the restored copy was stale ---
python -c "p=r'.github/workflows/health-check.yml'; s=open(p,encoding='utf-8').read(); s=s.replace('fetch-depth: 100','fetch-depth: 0'); open(p,'w',encoding='utf-8',newline='\n').write(s)"
if errorlevel 1 ( echo patch step failed & pause & exit /b 1 )

REM --- Syntax gate: change present + file structurally intact ---
python -c "s=open(r'.github/workflows/health-check.yml',encoding='utf-8').read(); assert 'fetch-depth: 0' in s and 'fetch-depth: 100' not in s, 'fetch-depth not set to 0'; assert 'actions/checkout@v4' in s and 'tools/health_check.py' in s, 'file looks mangled'; print('gate OK: fetch-depth: 0')"
if errorlevel 1 ( echo GATE FAILED -- not committing & pause & exit /b 1 )

REM --- Stage only this fix + this script ---
git add .github\workflows\health-check.yml PUSH_HEALTHCHECK_DEPTH_FIX.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "fix(health-check): full-depth checkout so weekly heartbeats stay visible" -m "refit_calibrator_heartbeat / weekly_backtest_heartbeat were false-RED: both weekly commits exist and ran 2026-05-24, but fetch-depth: 100 spans only ~4 days at ~24 commits/day, so the Sunday weekly commits scrolled out of git log --grep range and hit the 'no commit found' RED branch. fetch-depth: 0 = full history. Thresholds YELLOW 10d / RED 14d already correct. Rule 5 single-purpose, Rule 12 why-in-commit."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo health-check.yml now checks out full history. Both workflow
    echo heartbeats should flip RED to GREEN on the next health-check
    echo cron run, top of the next half-hour.
) else (
    echo Nothing to commit -- the fetch-depth fix may already be on origin.
)
echo.
pause
