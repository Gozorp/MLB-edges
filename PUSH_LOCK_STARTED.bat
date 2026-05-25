@echo off
REM Lock picks for games already in progress: pipeline-level fix.
REM main_predict.run() now captures the prior CSV before any to_csv
REM overwrites it, fetches MLB schedule status per game, and after
REM grade_picks() restores the LOCK_COLUMNS (pick, p_model, f5/full/fair
REM probs, edge_pp, grade, grade_reasons, tier, signals, kelly fields)
REM from the prior row for any game whose abstractGameState is past
REM "Preview". Tags stress_warnings with "locked_at_first_pitch".
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

echo === Fetch + reset to origin/main (clean base) ===
git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

echo === Re-applying _patch_lock_started_picks.py ===
python _patch_lock_started_picks.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Staging + committing ===
git add mlb_edge\main_predict.py _patch_lock_started_picks.py PUSH_LOCK_STARTED.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(pipeline): lock pick for games already past first pitch" -m "User-visible problem: when daily-slate re-bakes during the day, a game whose state changed (e.g. bullpen-fatigue stress warning fires post-lineup-posting) can flip its pick after first pitch. Example today: CHC @ PIT flipped PIT -> CHC at the 18:28 UTC bake driven by bullpen_fatigue on PIT - but the game had already been on the slate for hours with PIT as the pick." -m "Fix: main_predict.run() now captures the prior CSV before any to_csv overwrites it, fetches MLB schedule status, and after grade_picks() restores LOCK_COLUMNS (pick, p_model, pick_prob, f5/full/fair probs, edge_pp, grade, grade_reasons, grade_score, pre_cap_score/grade, tier, signals, why_skipped, ev_per_dollar, kelly_*) from the prior row for any game whose abstractGameState is past 'Preview' AND whose prior row had a real (non-TBD) pick. Subsequent rewrites by platoon-brain / bvp-brain attach see the locked values too." -m "Tags stress_warnings with 'locked_at_first_pitch' so the lock is visible in audits. Live tracker / post-game result columns / bullpen meta sidecars still update normally. PENDING_SP_DATA rows are not locked - if a SP gets announced after first pitch (rare), the fresh model output stands."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
