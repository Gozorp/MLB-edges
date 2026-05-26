@echo off
REM Commit 2 of the legacy-blowout teardown sequence. Severs the
REM apply_blowout_penalties call from auto_weight_update.run() so
REM the symmetric gradient loop becomes the sole learning path.
REM The daily +/-4% gradient cap is now a hard invariant for the
REM first time (the -15% blowout shock could previously exceed it).
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch from origin so the patch applies against a clean known
REM state. Same defensive pattern as the health-check commits.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/mlb_edge/auto_weight_update.py" -o mlb_edge\auto_weight_update.py
if errorlevel 1 ( echo curl auto_weight_update.py failed & pause & exit /b 1 )

python _patch_sever_blowout.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Smoke test: confirm the module imports and a dry-run completes
REM end-to-end without exception. Uses a known slate date.
python -c "import sys; sys.path.insert(0, '.'); from datetime import date; from mlb_edge import auto_weight_update as awu; awu.run(date(2026, 5, 20), dry_run=True); print('SMOKE OK')"
if errorlevel 1 ( echo smoke test failed & pause & exit /b 1 )

git add mlb_edge\auto_weight_update.py _patch_sever_blowout.py PUSH_SEVER_BLOWOUT.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "refactor(self-learn): sever chained blowout step, audit-log enum updated" -m "Commit 2 of the legacy-blowout teardown. Removes the apply_blowout_penalties call from auto_weight_update.run() (formerly lines 482-501 + line 527). The symmetric gradient loop apply_calibration_from_all_picks is now the sole learning path. learn_mode enum 'blowout_penalty' is replaced with 'no_learn' (only fires when learn_from_all=False or diag_df is empty — genuine no-op slates)." -m "Side effect: the daily +/-4% gradient cap (from the 2026-05-25 safeguard commit) is now a HARD INVARIANT. Previously the blowout shock at -15 percent per qualifying bust could exceed it on slates with a PLAT/DIAMOND blowout loss. Today the cap fully binds." -m "Evidence justifying the removal: data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/ — across 28 days, our losses are blowouts 31.9 percent of the time vs MLB baseline 30.1 percent. The 1.75pp delta is sampling noise on n=138, so the magnitude logic was capturing variance, not signal." -m "Imports of apply_blowout_penalties + blowout-specific constants are intentionally left in place — they become unused but harmless. Commit 3 cleans them up alongside the full purge of recursive_weight_update.py."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT ===
echo Commit 2 of 3 landed. Commit 3 (delete legacy file + CLI) follows next.
pause
