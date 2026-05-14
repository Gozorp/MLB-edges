@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Harden grade_picks() against PENDING_SP_DATA edge cases
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py - grade_picks()
echo
echo  Background: Run #54 (manual 5/14 daily-slate after the first
echo  PENDING guard landed) STILL failed with the same float(None)
echo  TypeError.  The previous guard was placed inside _score_pick
echo  but _compute_pqi_for_matchup runs BEFORE _score_pick is
echo  called inside grade_picks's iteration loop - and that's
echo  where the float(None) originates (SP xERA is None on rows
echo  where the probable starter isn't announced yet).
echo
echo  Two-part fix:
echo
echo  PART A - PENDING short-circuit at the grade_picks() loop
echo  level.  When pick=="TBD" or p_model is None/NaN, write
echo  default grade columns directly and `continue` past the
echo  rest of the per-row body.  No pqi compute, no team_quality
echo  compute, no _score_pick call.  Architecturally correct:
echo  the contract "this row has a complete profile" is now
echo  enforced at the function that depends on it.
echo
echo  PART B - belt-and-suspenders try/except around the
echo  pqi_diff injection.  The team_quality block already had
echo  one; pqi was the lone unguarded block.  Now if any
echo  future edge case slips past PART A, the row still
echo  recovers and the iteration continues for every other
echo  row on the slate.
echo
echo  Validation evidence from Run #54:
echo    Log line 233:
echo      "WARNING mlb_edge.main_predict: parlay builder failed
echo       (continuing): float() argument must be a string or
echo       a real number, not 'NoneType'"
echo    Diagnostic table at line 247:
echo      "MIA @ MIN  TBD  None  None  None  None ..."
echo    Diag CSV column count after bake: 27 (no grade columns).
echo
echo  After this push the next daily-slate cron will:
echo    - hit the MIA @ MIN PENDING row
echo    - short-circuit at the top of the loop
echo    - emit grade=C, grade_reasons="pending_sp_data --- ungraded"
echo    - continue grading every other row on the slate
echo    - bake a 33-column diag CSV
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_grade_harden
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\parlay_builder.py"             "%TMPDIR%\mlb_edge\parlay_builder.py"             >nul
copy /Y "PUSH_GRADE_PICKS_HARDEN.bat"            "%TMPDIR%\PUSH_GRADE_PICKS_HARDEN.bat"            >nul

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
copy /Y "%TMPDIR%\mlb_edge\parlay_builder.py"             "mlb_edge\parlay_builder.py"             >nul
copy /Y "%TMPDIR%\PUSH_GRADE_PICKS_HARDEN.bat"            "PUSH_GRADE_PICKS_HARDEN.bat"            >nul

echo Syntax-checking parlay_builder.py before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add PUSH_GRADE_PICKS_HARDEN.bat
git status --short
git commit -m "Harden grade_picks against PENDING_SP_DATA at the loop level. The first PENDING guard (commit 279a7cc) was placed inside _score_pick, but _compute_pqi_for_matchup runs BEFORE _score_pick is called from grade_picks's iteration loop --- and that's where float(None) originated when a TBD row has both SP xERAs missing. Run #54 still emitted the same TypeError and the diag CSV still came back with 27 columns instead of 33. Two-part fix in grade_picks: (A) detect pick=='TBD' or p_model is None/NaN at the very top of each iteration, write default grade columns directly, and `continue` past the rest of the per-row body (no pqi compute, no team_quality compute, no _score_pick call). (B) wrap the pqi_diff injection in try/except matching the existing pattern for team_quality_mod, so any future edge case beyond PENDING that produces a numeric exception inside pqi will be logged at debug level and the iteration continues. Architecturally this enforces the 'complete profile' contract at the function that depends on it, instead of trusting upstream rows to all be well-formed."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Time-sensitive: 14:00 UTC scheduled cron is the next
echo  natural firing.  If pushed before then, the scheduled run
echo  validates the fix automatically.  Or trigger a manual run
echo  immediately to confirm the 33-column diag CSV gets baked.
echo ============================================================
pause
