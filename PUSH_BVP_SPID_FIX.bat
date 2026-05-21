@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  BvP-brain SP-ID lookup bug fix
echo  -----------------------------------------------------------
echo  Symptom: production 5/20 diag CSV missing away_bvp_top5_json
echo  + home_bvp_top5_json columns despite BvP-brain commit landing.
echo
echo  Root cause: the inline lookup tried to pull SP IDs from preds
echo  rows (gr.get("away_sp_id") / gr.get("home_sp_id")) but
echo  build_pipeline takes SP IDs as function parameters — it does
echo  NOT emit them as columns of the preds DataFrame.  Result:
echo  matchup_to_sp_ids stayed empty, BvP attach silently no-op'd
echo  per Rule 6 best-effort wrapping (log only, no crash).
echo
echo  Fix: source SP IDs from ctx['lineups'] (a list of LineupMeta
echo  objects from live_lineups.fetch_slate_meta) instead.  Each
echo  LineupMeta has explicit home_sp_id + away_sp_id fields per
echo  live_lineups.py:67/70.
echo
echo  Files changed:
echo
echo  1. mlb_edge/main_predict.py (+13 lines net)
echo     Removed the broken inline gr.get("..._sp_id") block from
echo     the platoon-brain attach (it was dead code anyway).
echo     Added a new lineup_meta-sourced lookup loop BEFORE the
echo     platoon try block.  Uses getattr() with default None so
echo     missing fields don't crash; logs the count built so we
echo     can confirm in production logs.
echo
echo  2. PUSH_BVP_SPID_FIX.bat (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed: live_lineups.LineupMeta has the IDs
echo    [E] Rule 3  — ast.parse syntax gate in this script
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — single targeted fix, did NOT rebuild bvp_brain
echo    [E] Rule 6  — getattr + try/except + log.warning kept intact
echo    [E] Rule 13 — this script narrates the change
echo
echo  Validation gate after ship:
echo    - Next daily-slate cron should log "BvP SP-ID lookup built
echo      from lineup_meta: N matchups" where N matches lineup count
echo    - Next diag CSV should have columns 37 + 38 named
echo      away_bvp_top5_json + home_bvp_top5_json
echo    - Payloads will be empty ([]) for games whose SP isn't
echo      confirmed yet — that's correct behavior
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_bvp_spid_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\main_predict.py"  "%TMPDIR%\mlb_edge\main_predict.py"  >nul
copy /Y "PUSH_BVP_SPID_FIX.bat"     "%TMPDIR%\PUSH_BVP_SPID_FIX.bat"     >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring edits...
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"  "mlb_edge\main_predict.py"  >nul
copy /Y "%TMPDIR%\PUSH_BVP_SPID_FIX.bat"     "PUSH_BVP_SPID_FIX.bat"     >nul

echo Syntax-checking before commit...
python -c "import ast; ast.parse(open('mlb_edge/main_predict.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/main_predict.py
git add PUSH_BVP_SPID_FIX.bat
git status --short
git commit -m "Fix BvP-brain SP-ID lookup — source from lineup_meta with per-matchup error isolation. Production 5/20 diag CSV was missing away_bvp_top5_json + home_bvp_top5_json columns despite BvP-brain commit 2747a60 landing and daily-slate cron 8ef47d5 running after it. Root cause: the inline lookup in the platoon-brain attach block called gr.get('away_sp_id') / gr.get('home_sp_id') on each preds row, but build_pipeline takes SP IDs as function parameters and does not emit them as DataFrame columns — they were always None. matchup_to_sp_ids stayed empty, and the BvP attach silently no-op'd per the Rule 6 best-effort wrapping. Fix: replaced the broken inline lookup with a lineup_meta-sourced loop that pulls home_sp_id and away_sp_id from each ctx['lineups'] LineupMeta object (live_lineups.py:67/70). Uses getattr() with default None for resilience.  Per-matchup try/except so a single malformed SP ID (e.g. non-int from a statsapi schema drift) does NOT abort the entire loop and lose BvP coverage for every subsequent matchup in the slate — that was the original draft's outer-wrap bug, caught in code review. Added an info-level log line so production runs can confirm the lookup count matches the lineup count. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (verified live_lineups.LineupMeta has the IDs), Rule 5 single targeted fix (did NOT rebuild bvp_brain), Rule 6 getattr + try/except retained, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Wait for next daily-slate cron (or trigger manually)
echo    2. Check cron logs for "BvP SP-ID lookup built from
echo       lineup_meta: N matchups" line
echo    3. Pull the new diag CSV and confirm columns 37 + 38 are
echo       away_bvp_top5_json + home_bvp_top5_json
echo ============================================================
pause
