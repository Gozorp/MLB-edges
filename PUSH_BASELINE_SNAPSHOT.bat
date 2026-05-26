@echo off
REM Commit 1 of the legacy-blowout teardown sequence: persist the 28-day
REM blowout-magnitude baseline snapshot. README + parameterized probe.py
REM + the raw pick x outcome join CSV + derived stats JSON. Lives under
REM data/baselines/ so it stays isolated from the active pipeline and
REM can be diffed file-by-file against future windows.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM probe.py and README.md are untracked working-tree files at this
REM point, so the `git reset --hard` above leaves them intact. We just
REM re-run the probe to (re)generate the CSV + JSON outputs, which
REM guarantees those derived files were produced by the exact probe.py
REM we are about to commit.
python data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\probe.py --start 2026-04-27 --end 2026-05-25 --out data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\
if errorlevel 1 ( echo probe.py failed & pause & exit /b 1 )

git add data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\probe.py data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\README.md data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\picks_resolved.csv data\baselines\blowout_magnitude_2026-04-27_to_2026-05-25\summary.json PUSH_BASELINE_SNAPSHOT.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "chore(observability): persist 28-day blowout-magnitude baseline" -m "Commit 1 of the legacy-blowout teardown sequence. Snapshots 28 days (2026-04-27 to 2026-05-25) of resolved picks joined to MLB final scores. Lives under data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/. Four files: README explaining what/why/finding, parameterized probe.py (stdlib only, reusable for future windows), the 299-row pick x outcome CSV, and a derived summary JSON." -m "Captured before deleting recursive_weight_update.apply_blowout_penalties so we have a frozen baseline to compare against. Headline finding: our losses are blowouts 31.9 percent of the time, MLB games are blowouts 30.1 percent of the time. The 1.75pp delta is well within sampling noise on n=138 losses, which is why we rejected the magnitude-port into the gradient loop. PLAT vs GOLD shows a marginal 20.0 vs 12.1 percent blow-loss-rate gap that warrants re-checking after another 30-60 days." -m "Future windows: rerun the probe with --start --end --out flags. Output structure matches this folder exactly so direct diffs work."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT ===
echo Commit 1 of 3 landed. Commit 2 (sever chained blowout step) follows next.
pause
