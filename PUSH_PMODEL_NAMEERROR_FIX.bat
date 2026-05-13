@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bug fix: NameError 'p_model' in parlay_builder._score_pick
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py
echo     The 5-caps push (17963c5) added new hard-cap rules that
echo     reference `p_model` as a local variable in CAP 2 and
echo     CAP 3 logic, but never extracted p_model from `row`.
echo     Result: every call to grade_picks() crashed with
echo        NameError: name 'p_model' is not defined
echo     The try/except in main_predict caught the error, logged
echo     a warning, and skipped the entire diag CSV rewrite.
echo
echo     Visible symptom: deployed diag CSVs never got the new
echo     `grade`, `pre_cap_score`, `pre_cap_grade`, `grade_reasons`
echo     columns; the cap audit found zero cap-era files.
echo
echo     Fix: 1 line added next to where f5/full are extracted:
echo        p_model = row.get("p_model")
echo
echo  Discovery path:
echo  - 1st verification (run #43, before this fix): no
echo    pre_cap_grade column in diag.  Diagnosed as
echo    main_predict not re-writing the diag CSV after grading.
echo  - Diag CSV rewrite fix shipped (96b8d39).
echo  - 2nd verification (run #44, after that fix): STILL no
echo    pre_cap_grade column.  Opened run #44's predict.py log,
echo    line 246: "parlay builder failed (continuing): name
echo    'p_model' is not defined".  Root cause located.
echo
echo  After this push lands + the NEXT daily-slate cron fires:
echo  - grade_picks() runs without NameError
echo  - graded DataFrame contains pre_cap_grade column
echo  - main_predict's now-fixed rewrite persists it to diag CSV
echo  - bake step copies to docs/data/
echo  - cap_audit picks it up
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_pmodel_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\parlay_builder.py"             "%TMPDIR%\mlb_edge\parlay_builder.py"             >nul
copy /Y "PUSH_PMODEL_NAMEERROR_FIX.bat"          "%TMPDIR%\PUSH_PMODEL_NAMEERROR_FIX.bat"          >nul

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
copy /Y "%TMPDIR%\PUSH_PMODEL_NAMEERROR_FIX.bat"          "PUSH_PMODEL_NAMEERROR_FIX.bat"          >nul

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add PUSH_PMODEL_NAMEERROR_FIX.bat
git status --short
git commit -m "Fix NameError: extract p_model as local in _score_pick. The 5-caps push (17963c5) added CAP 2 (F3 + non-elite opposing SP) and CAP 3 (PLATINUM calibration artifact) rules that reference `p_model` four times (lines 519, 526, 589, 610) but never pulled the value out of the input row. Every call to parlay_builder.grade_picks() raised NameError: name 'p_model' is not defined. main_predict's try/except swallowed the error, logged 'parlay builder failed (continuing)', and silently skipped the diag CSV rewrite — so deployed diag CSVs never gained the grade columns the cap audit depends on. The cap audit consequently always reported 'no cap-era data yet' regardless of how many slates ran. Fix: add p_model = row.get('p_model') alongside the existing f5/full extraction so the cap rules can dereference it. Discovered by reading run #44's predict.py log directly (the run completed 'Success' because the exception was caught) — verification phase paid off."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  After Claude re-triggers daily-slate one more time, expect:
echo    - predict.py log: NO "parlay builder failed" warning
echo    - predict.py log: "Re-wrote diagnostic table with grade
echo      columns to ..." line near the end
echo    - diag CSV: now ~31 columns (adds grade, pre_cap_score,
echo      pre_cap_grade, grade_reasons)
echo    - cap_audit: produces a real, non-empty output
echo ============================================================
pause
