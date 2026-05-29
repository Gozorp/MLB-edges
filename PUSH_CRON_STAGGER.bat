@echo off
REM ===========================================================================
REM PUSH_CRON_STAGGER.bat
REM ---------------------------------------------------------------------------
REM WHY: The health-check cron is "*/30 * * * *" (:00 and :30), but GitHub
REM      Actions fired it only every 1.4-4.4h over the last day. GitHub's
REM      scheduler is best-effort and delays/drops runs worst at the top and
REM      half of the hour (peak load). Moving off :00/:30 to odd minutes is
REM      GitHub's own recommended mitigation.
REM
REM WHAT: .github/workflows/health-check.yml cron "*/30 * * * *" -> "7,37 * * * *"
REM       (still twice an hour, just off-peak). This is a MITIGATION, not a
REM       guarantee -- reliable hourly needs an external trigger (see chat:
REM       Cloudflare Worker Cron once the Worker is back up).
REM
REM SAFE: Rule 4 safe-push, Rule 5 single-purpose, Rule 12/13.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

git fetch origin main
git reset --hard origin/main

REM --- stagger the cron off the :00/:30 peak ---
python -c "q=chr(34); p=r'.github/workflows/health-check.yml'; s=open(p,encoding='utf-8').read(); s=s.replace(q+'*/30 * * * *'+q, q+'7,37 * * * *'+q).replace('# every 30 minutes','# 2x/hour at off-peak min (GitHub delays/drops :00 and :30)'); open(p,'w',encoding='utf-8',newline=chr(10)).write(s); print('cron staggered:', '7,37 * * * *' in s)"
if errorlevel 1 ( echo cron-stagger step failed & pause & exit /b 1 )

REM --- gate: change present, fetch-depth fix intact ---
python -c "s=open(r'.github/workflows/health-check.yml',encoding='utf-8').read(); assert '7,37 * * * *' in s,'cron not staggered'; assert '*/30 * * * *' not in s,'old cron still present'; assert 'fetch-depth: 0' in s,'fetch-depth regressed'; print('gate OK')"
if errorlevel 1 ( echo GATE FAILED -- not committing & pause & exit /b 1 )

git add .github\workflows\health-check.yml PUSH_CRON_STAGGER.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore(health-check): stagger cron off :00/:30 peak to cut GitHub delays" -m "GitHub fired the */30 cron only every 1.4-4.4h (best-effort scheduler drops/delays runs, worst at :00 and :30). Moved to 7,37 -- still 2x/hour, off-peak. Mitigation only; reliable hourly needs an external trigger. Rule 4 safe-push, Rule 5 single-purpose."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Cron staggered to 7,37. Takes effect on GitHub's next scheduler cycle.
) else (
    echo Nothing to commit -- already current on origin.
)
echo.
pause
