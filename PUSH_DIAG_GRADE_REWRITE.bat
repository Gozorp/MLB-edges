@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bug fix: persist grade columns back to diag CSV
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/main_predict.py
echo     The parlay block already builds a `graded` DataFrame
echo     containing grade, pre_cap_score, pre_cap_grade, and
echo     grade_reasons. But it only wrote that DataFrame to
echo     parlay_^<date^>.txt, never back to the diag CSV.
echo
echo     As a result the deployed diag CSV ended up with the
echo     pre-grading table from earlier in the function and the
echo     cap audit could never find any cap-era files.
echo
echo     Fix: after grade_picks returns, re-write the diag CSV
echo     using `graded` instead of `table`.
echo
echo  Why this is the blocking bug for cap audit:
echo    - 5-caps push (17963c5) added column-writing logic to
echo      parlay_builder.grade_picks()
echo    - cap_audit() in run_backtest.py filters on the presence
echo      of `pre_cap_grade` in the diag CSV header
echo    - With those columns missing on disk, cap_audit reads
echo      zero cap-era files and writes an empty audit
echo
echo  After this push lands + the next daily-slate cron fires:
echo    - Diag CSV will have 4 new columns at the end:
echo      grade_score, grade, pre_cap_score, pre_cap_grade
echo    - Plus `grade_reasons` already in graded
echo    - cap_audit will pick it up and produce a non-empty
echo      output (even on a single slate)
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_diag_grade
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\main_predict.py"      "%TMPDIR%\mlb_edge\main_predict.py"      >nul
copy /Y "PUSH_DIAG_GRADE_REWRITE.bat"   "%TMPDIR%\PUSH_DIAG_GRADE_REWRITE.bat"   >nul

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
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"      "mlb_edge\main_predict.py"      >nul
copy /Y "%TMPDIR%\PUSH_DIAG_GRADE_REWRITE.bat"   "PUSH_DIAG_GRADE_REWRITE.bat"   >nul

echo Staging + committing...
git add mlb_edge/main_predict.py
git add PUSH_DIAG_GRADE_REWRITE.bat
git status --short
git commit -m "Persist grade columns back to diag CSV (cap-audit unblocker). main_predict was writing the pre-grading table to picks_<date>_diag.csv on line 800, then calling parlay_builder.grade_picks() to build the `graded` DataFrame (containing grade, pre_cap_score, pre_cap_grade, grade_reasons), then writing only parlay_<date>.txt. The diag CSV that gets baked to docs/data/ never received the grade columns, which silently broke the cap audit's filter on `pre_cap_grade in df.columns`. Fix: after the parlay report is written, re-write the diag CSV using `graded` so the deployed file carries the grade columns the weekly cap audit depends on. Discovered while verifying the first cap audit output: 5/13 manual daily-slate rerun #43 committed a 27-column diag CSV that had every new lineup_shape column but no grade. Without this fix, cap_audit always returns 'no cap-era data yet'."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Next steps:
echo  1. Re-trigger daily-slate workflow (manual dispatch)
echo  2. Verify diag CSV header includes pre_cap_grade
echo  3. Trigger weekly-backtest workflow (manual dispatch)
echo  4. Read docs/data/backtest/cap_audit_latest.md
echo
echo  Expected first-output state:
echo    n_cap_fires_total: 1-5 (depending on slate)
echo    Most caps: 0 fires (only 1 slate of data)
echo    All firings concentrated in CAP 1 or CAP 4
echo    (the high-precision negative-edge cases)
echo ============================================================
pause
