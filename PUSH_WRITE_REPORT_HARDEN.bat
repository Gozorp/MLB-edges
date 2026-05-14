@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Third pass: harden write_parlay_report against None
echo  -----------------------------------------------------------
echo  The float(None) chain has one more hiding place.
echo
echo  Diagnosis chronology:
echo    Pass 1 (commit 279a7cc): _score_pick early-exit for TBD.
echo      Didn't help - _compute_pqi_for_matchup runs BEFORE
echo      _score_pick in the grade_picks loop.
echo    Pass 2 (commit 065cb97): grade_picks loop-level
echo      short-circuit for TBD rows.  Didn't help - the error
echo      moved DOWNSTREAM into write_parlay_report.
echo    Pass 3 (this push): write_parlay_report had three
echo      unguarded float(r['p_model']) calls.  The "DO NOT
echo      PARLAY" section iterates rows OUTSIDE the parlay
echo      grades, which now includes the PENDING row (grade=C
echo      after my grade_picks fix).  float(None) crashes the
echo      whole report, which prevents the diag CSV rewrite
echo      that follows it in main_predict.
echo
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py - write_parlay_report()
echo     Three spots hardened against None p_model / edge_pp:
echo
echo     A. Per-grade A/A-/B+ listing - skip rows with None
echo        p_model (shouldn't reach here but defense-in-depth).
echo
echo     B. Edge-band filter excluded section - skip rows where
echo        either p_model or edge_pp is None.
echo
echo     C. DO NOT PARLAY listing - use safe formatter ("  n/a"
echo        placeholder) instead of raw float() that crashes on
echo        the PENDING_SP_DATA row.
echo
echo  After this push the next daily-slate cron should:
echo    - hit PENDING MIA @ MIN row again
echo    - grade_picks loop short-circuit returns score=0/grade=C
echo    - write_parlay_report iterates the avoid bucket
echo    - PENDING row prints with "p=  n/a" instead of crashing
echo    - main_predict's graded.to_csv finally writes 33 columns
echo    - diag CSV ships with grade/pre_cap_grade columns
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_writereport_harden
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\parlay_builder.py"             "%TMPDIR%\mlb_edge\parlay_builder.py"             >nul
copy /Y "PUSH_WRITE_REPORT_HARDEN.bat"           "%TMPDIR%\PUSH_WRITE_REPORT_HARDEN.bat"           >nul

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
copy /Y "%TMPDIR%\PUSH_WRITE_REPORT_HARDEN.bat"           "PUSH_WRITE_REPORT_HARDEN.bat"           >nul

echo Syntax-checking parlay_builder.py before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add PUSH_WRITE_REPORT_HARDEN.bat
git status --short
git commit -m "Harden write_parlay_report against None p_model in PENDING rows. Third pass on the same root cause. After the grade_picks loop short-circuit (commit 065cb97) set grade=C for PENDING_SP_DATA rows, those rows landed in the DO NOT PARLAY listing of write_parlay_report, which iterates with `float(r['p_model'])*100` - a None-unsafe formatter that raised TypeError and killed the whole report. main_predict's outer try/except swallowed it, the diag CSV rewrite that follows write_parlay_report never ran, and the deployed CSV stayed at 27 columns. Fix: three spots in write_parlay_report now use defensive None checks before calling float(): (A) per-grade A/A-/B+ section uses `continue` to skip None p_model rows (belt-and-suspenders - shouldn't reach here after the grade_picks fix, but a future regression elsewhere shouldn't kill the report), (B) edge-band-excluded section uses `continue` for None p_model or None edge_pp, (C) DO NOT PARLAY section uses a safe '  n/a' placeholder string so the PENDING row prints harmlessly. The architectural lesson: any function that receives a 'graded' DataFrame downstream of grade_picks must assume PENDING rows can flow through and handle their None columns gracefully."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.  This should finally close the loop.
echo  After the next daily-slate cron, expect the diag CSV
echo  to bake at 33 columns with grade/pre_cap_grade populated.
echo ============================================================
pause
