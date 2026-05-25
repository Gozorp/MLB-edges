@echo off
REM Simpler ship: reset to origin, apply the one-line workflow fix,
REM run the backfill via a separate Python script (no inline-Python
REM quoting nightmares), then stage + commit + push.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

echo === Fetch + reset to origin/main ===
git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

echo === Apply workflow fix (one-line add of data/state/) ===
python -c "from pathlib import Path; p=Path('.github/workflows/daily-slate.yml'); s=p.read_text(); old='          git add picks_*.csv parlay_*.txt docs/data/ 2>/dev/null || true'; new='          git add picks_*.csv parlay_*.txt docs/data/ data/state/ 2>/dev/null || true'; assert s.count(old)==1, 'anchor missing'; p.write_text(s.replace(old, new, 1)); print('workflow patched')"
if errorlevel 1 ( echo workflow patch failed & pause & exit /b 1 )

echo === Run backfill: 5/6 -> 5/24 (force=True) ===
python _backfill_selflearn.py
if errorlevel 1 ( echo backfill failed & pause & exit /b 1 )

echo === Staging ===
git add .github/workflows/daily-slate.yml data/state/weights_state.json data/state/recalibration_log.jsonl _backfill_selflearn.py PUSH_SELFLEARN_V2.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "fix(self-learn): persist auto_weight_update outputs + backfill 5/6-5/24" -m "(1) Workflow fix: daily-slate.yml's git add line previously staged picks_*.csv + parlay_*.txt + docs/data/ but NOT data/state/. Every workflow run of auto_weight_update wrote new weights_state.json + appended to recalibration_log.jsonl on the runner FS, then those changes were discarded when the runner shut down. Add data/state/ to the staged paths." -m "(2) Backfill: ran awu.run with force=True over 5/6..5/24 (19 dates) so the audit log accumulates 19 missed entries of the all_picks_tier_weighted learning path. Each day saw 4-11 picks with definitive outcomes contribute signed updates across 6-8 weights. Net 19-day drift: team-quality weights -4.9%, SP-edge family -1.3%, bullpen weights ~unchanged."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
pause
