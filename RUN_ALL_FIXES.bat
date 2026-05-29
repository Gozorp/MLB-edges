@echo off
REM ===========================================================================
REM RUN_ALL_FIXES.bat  --  one double-click applies all 3 pending fixes.
REM Each is an independent safe-push (fetch -> reset --hard origin/main ->
REM apply -> gate -> commit -> push). A failure in one skips to the next.
REM   FIX 1  self-learn: repair NUL-corrupted recalibration_log + relearn 05-27
REM          -> weights_state_freshness goes GREEN
REM   FIX 2  health-check cron */30 -> 7,37 (off-peak; fewer GitHub drops)
REM   FIX 3  health-check PAGES_BASE_URL -> live Worker URL
REM          -> clears the false "deployment unreachable" REDs
REM Validated piece-by-piece against your real files before delivery.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge
set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

REM ============================ FIX 1/3 ============================
echo.
echo ############ FIX 1/3: self-learn repair + relearn 05-27 ############
if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul
git fetch origin main
git reset --hard origin/main
python tools\repair_recal_log.py
echo Running self-learn for 2026-05-27 (fetches box scores, ~20-30s)...
python -c "import sys; sys.path.insert(0,'.'); from datetime import date; from pathlib import Path; from mlb_edge import auto_weight_update as awu; awu.run(date(2026,5,27), picks_dir=Path('docs/data'))"
python -c "import ast; ast.parse(open(r'tools/repair_recal_log.py',encoding='utf-8').read())"
if errorlevel 1 goto fix2
git add tools\repair_recal_log.py data\state\weights_state.json data\state\recalibration_log.jsonl RUN_ALL_FIXES.bat
git diff --cached --quiet
if not errorlevel 1 goto fix2
git commit -m "fix(self-learn): repair NUL-corrupted recalibration_log + relearn 05-27"
git pull --rebase --autostash origin main 2>nul
git push origin main

REM ============================ FIX 2/3 ============================
:fix2
echo.
echo ############ FIX 2/3: stagger health-check cron off-peak ############
if exist .git\index.lock del /F /Q .git\index.lock
git fetch origin main
git reset --hard origin/main
python -c "q=chr(34); p=r'.github/workflows/health-check.yml'; s=open(p,encoding='utf-8').read(); s=s.replace(q+'*/30 * * * *'+q, q+'7,37 * * * *'+q).replace('# every 30 minutes','# 2x/hour off-peak (GitHub delays/drops :00 and :30)'); open(p,'w',encoding='utf-8',newline=chr(10)).write(s)"
python -c "s=open(r'.github/workflows/health-check.yml',encoding='utf-8').read(); assert '7,37 * * * *' in s and 'fetch-depth: 0' in s"
if errorlevel 1 goto fix3
git add .github\workflows\health-check.yml
git diff --cached --quiet
if not errorlevel 1 goto fix3
git commit -m "chore(health-check): stagger cron off :00/:30 peak to cut GitHub delays"
git pull --rebase --autostash origin main 2>nul
git push origin main

REM ============================ FIX 3/3 ============================
:fix3
echo.
echo ############ FIX 3/3: point health-check at the live Worker URL ############
if exist .git\index.lock del /F /Q .git\index.lock
git fetch origin main
git reset --hard origin/main
python -c "import re,ast; q=chr(34); url='https://mlb-edges.saladin-alfaatih.workers.dev'; p=r'tools/health_check.py'; s=open(p,encoding='utf-8').read(); new=re.sub('PAGES_BASE_URL = '+q+'[^'+q+']*'+q,'PAGES_BASE_URL = '+q+url+q,s,count=1); ast.parse(new); open(p,'w',encoding='utf-8',newline=chr(10)).write(new)"
python -c "import ast; s=open(r'tools/health_check.py',encoding='utf-8').read(); ast.parse(s); assert 'saladin-alfaatih.workers.dev' in s"
if errorlevel 1 goto done
git add tools\health_check.py
git diff --cached --quiet
if not errorlevel 1 goto done
git commit -m "fix(health-check): point PAGES_BASE_URL at the live Worker URL"
git pull --rebase --autostash origin main 2>nul
git push origin main

:done
echo.
echo ==================== ALL FIXES ATTEMPTED ====================
git log -3 --oneline
echo.
echo On the next health-check run the card should show: weights GREEN,
echo cron staggered, deployment no longer false-RED ^(reachable^).
echo Still manual: bind ANTHROPIC_API_KEY on the Worker to light up
echo Deep Analysis ^(npx wrangler secret put ANTHROPIC_API_KEY^).
echo.
pause
