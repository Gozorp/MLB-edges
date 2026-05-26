@echo off
REM HOTFIX: restore MIN_RELATIVE_WEIGHT constant in auto_weight_update.py.
REM Commit 3 of the teardown (8283396) removed the import but missed
REM the line-314 reference. Silent NameError on every awu.run() since
REM — predict.bat's "-- continuing" graceful fallback hid it.
REM
REM This .bat: ships the constant restoration, then immediately runs
REM awu.run(date(2026, 5, 25)) to backfill yesterday's missed learning
REM iteration (the one that should have fired this morning).
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch from origin so the patch applies against clean known state.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/mlb_edge/auto_weight_update.py" -o mlb_edge\auto_weight_update.py
if errorlevel 1 ( echo curl auto_weight_update.py failed & pause & exit /b 1 )

python _patch_restore_min_weight.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Smoke: dry-run yesterday's date to confirm the constant is in scope
REM and the gradient loop runs cleanly end-to-end.
python -c "import sys; sys.path.insert(0, '.'); from datetime import date; from mlb_edge import auto_weight_update as awu; awu.run(date(2026, 5, 25), dry_run=True); print('SMOKE OK')"
if errorlevel 1 ( echo smoke failed & pause & exit /b 1 )

git add mlb_edge\auto_weight_update.py _patch_restore_min_weight.py PUSH_RESTORE_MIN_WEIGHT.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(self-learn): restore MIN_RELATIVE_WEIGHT after teardown removed import" -m "Hotfix for the Commit 3 teardown (8283396). MIN_RELATIVE_WEIGHT was dropped from the import block when recursive_weight_update.py was purged, but the floor=MIN_RELATIVE_WEIGHT*base reference at auto_weight_update.py:314 survived. Every awu.run() since 8283396 raised NameError, which predict.bat absorbed via its '-- continuing' graceful fallback. The daily-slate workflow kept generating picks fine, but the learning loop has been skipping every iteration. Same silent-amnesia failure mode as the original 2026-05-23 persistence bug, different root cause." -m "Fix: define MIN_RELATIVE_WEIGHT = 0.25 inline near the other learn-rate constants (NEW_CEILING_MULT, STRESS_MASK_FACTOR, WARMUP_THRESHOLD). 0.25 is the legacy value from recursive_weight_update.py and the documented 25 percent floor in the selflearn-safeguards memory." -m "Lesson: when removing imports in a refactor, AST-grep for references first, not just imports. The patch script ran successfully because it only validated import-cleanup, not reference-cleanup. Detection took ~6 hours because the failure was silent."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === BACKFILL YESTERDAY ===
echo Now running awu.run for 5/25 for real (not dry-run) to backfill
echo the missed learning iteration. Picks for 5/25 are resolved by now
echo so the gradient has real residuals to learn from.
python -c "import sys; sys.path.insert(0, '.'); from datetime import date; from mlb_edge import auto_weight_update as awu; awu.run(date(2026, 5, 25), force=True)"
if errorlevel 1 ( echo backfill failed & pause & exit /b 1 )

echo.
echo === STAGE + PUSH BACKFILLED STATE ===
git add data\state\weights_state.json data\state\recalibration_log.jsonl
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "self-learn: backfill awu.run for 2026-05-25 after MIN_RELATIVE_WEIGHT hotfix"
    git push origin main
    echo backfill committed and pushed
) else (
    echo no state changes to commit (gradient produced zero delta?)
)

git log -1 --oneline
echo.
echo === DONE ===
pause
