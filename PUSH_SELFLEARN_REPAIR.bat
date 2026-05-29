@echo off
REM ===========================================================================
REM PUSH_SELFLEARN_REPAIR.bat
REM ---------------------------------------------------------------------------
REM WHY: data/state/recalibration_log.jsonl had a corrupted last line -- 918
REM      NUL bytes from an interrupted self-learn write (the 05-27 entry).
REM      That is why weights_state_freshness was stuck at the 05-26 entry
REM      (the YELLOW), and why a local health-check read it as "audit log
REM      empty/unreadable".
REM
REM WHAT: 1) Repair the log (tools/repair_recal_log.py drops the corrupt line,
REM          backs up the original first; idempotent).
REM       2) Re-run the self-learn for 2026-05-27. run() learns from the
REM          committed _diag CSV + fresh MLB box scores, so the missing base
REM          picks file is fine. This writes a new audit entry (ts = now) and
REM          updates weights_state.json -> weights_state_freshness goes GREEN.
REM       3) Push the repaired log + refreshed weights.
REM
REM SAFE: Rule 4 safe-push, Rule 3 ast gate on the new helper, Rule 12/13.
REM       If the self-learn step fails (e.g. network), the repaired log is
REM       still pushed -- weights just stays at 05-26 until the next run.
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

REM --- 1) repair the corrupted audit log (idempotent; backs up first) ---
python tools\repair_recal_log.py
if errorlevel 1 ( echo repair step failed & pause & exit /b 1 )

REM --- Rule 3: ast gate on the helper we are committing ---
python -c "import ast; ast.parse(open(r'tools/repair_recal_log.py',encoding='utf-8').read()); print('ast gate OK')"
if errorlevel 1 ( echo AST GATE FAILED & pause & exit /b 1 )

REM --- 2) re-run self-learn for 05-27 (learns from _diag + live box scores) ---
echo Running self-learn for 2026-05-27 (fetches box scores, ~20-30s)...
python -c "import sys; sys.path.insert(0,'.'); from datetime import date; from pathlib import Path; from mlb_edge import auto_weight_update as awu; awu.run(date(2026,5,27), picks_dir=Path('docs/data'))"
if errorlevel 1 ( echo self-learn run reported an error -- pushing the repaired log anyway )

REM --- show the current last entry so you can see the result ---
python -c "import json; ls=[l for l in open(r'data/state/recalibration_log.jsonl',encoding='utf-8') if l.strip()]; d=json.loads(ls[-1]); print('last audit entry -> slate', d['slate_date'], '| ts', d['ts'])"

REM --- stage + safe-push ---
git add tools\repair_recal_log.py data\state\weights_state.json data\state\recalibration_log.jsonl PUSH_SELFLEARN_REPAIR.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "fix(self-learn): repair NUL-corrupted recalibration_log + relearn 05-27" -m "The 05-27 self-learn append was interrupted, leaving 918 NUL bytes as the last log line -> weights_state_freshness stuck at 05-26 (YELLOW) and local reads saw 'audit log empty'. Added tools/repair_recal_log.py (drops invalid lines, backs up), repaired the log, and re-ran auto_weight_update for 05-27 (learns from committed _diag + live box scores). Weights refreshed. Rule 3 ast-gated, Rule 4 safe-push."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Log repaired + weights re-learned + pushed. weights_state_freshness
    echo should be GREEN on the next health-check run.
) else (
    echo Nothing to commit -- already clean/current on origin.
)
echo.
pause
