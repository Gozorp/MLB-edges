@echo off
REM Tap-on-shoulder runaway alarm: audit entries gain
REM runaway_ceiling_alarm + runaway_features. Triggers when any weight
REM hits 1.4 * base (10pp from the new 1.5 * base ceiling). Also logs
REM a warning so the GH Action workflow log surfaces it.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

python _patch_runaway_tripwire.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

git add mlb_edge/auto_weight_update.py _patch_runaway_tripwire.py PUSH_RUNAWAY_TRIPWIRE.bat
git commit -m "feat(self-learn): runaway-ceiling tripwire in audit entries" -m "One-line alarm so a signal-stacking runaway never has to be caught by manual log-reading. Sets runaway_ceiling_alarm=true and lists runaway_features whenever any weight's new value >= 1.4 * base (10pp shy of the 1.5 * base ceiling). Also emits a log.warning so the GitHub Action workflow run summary surfaces it. The 1.4 threshold gives ~10% headroom above normal upward drift while still firing well before the ceiling clips."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
pause
