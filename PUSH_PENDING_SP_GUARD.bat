@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  PENDING_SP_DATA early-exit guard for _score_pick
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py
echo     Add a short-circuit at the very top of _score_pick:
echo
echo       _pm = row.get("p_model")
echo       if pick == "TBD" or _pm is None or pd.isna(_pm):
echo           return 0, ["pending_sp_data --- ungraded ..."]
echo
echo     The guard fires before any cap or rule runs.  When a
echo     row is PENDING_SP_DATA (probable starter not yet
echo     announced), the upstream pipeline emits pick="TBD" and
echo     None across every probability/edge column.  Every cap
echo     rule below assumes those columns are numeric; calling
echo     float(None) raises TypeError that gets swallowed by
echo     main_predict's outer try/except, which then aborts the
echo     entire diag CSV rewrite as collateral damage --- losing
echo     grade columns for *every other* row on the slate.
echo
echo  Diagnosis from Run #53 (5/14 manual trigger):
echo    Log line 356:
echo      WARNING mlb_edge.main_predict: parlay builder failed
echo      (continuing): float() argument must be a string or a
echo      real number, not 'NoneType'
echo    Slate had MIA @ MIN with pick=TBD, probable SP not
echo    announced yet.  All grade columns missing on disk after
echo    bake.
echo
echo  After this lands + next cron run:
echo    - PENDING rows return (0, "pending_sp_data --- ungraded")
echo    - grade_picks completes for all OTHER rows on the slate
echo    - diag CSV gains 33 columns including grade/pre_cap_grade
echo    - dashboard renders full grade pills again
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_pending_sp
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\parlay_builder.py"             "%TMPDIR%\mlb_edge\parlay_builder.py"             >nul
copy /Y "PUSH_PENDING_SP_GUARD.bat"              "%TMPDIR%\PUSH_PENDING_SP_GUARD.bat"              >nul

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
copy /Y "%TMPDIR%\PUSH_PENDING_SP_GUARD.bat"              "PUSH_PENDING_SP_GUARD.bat"              >nul

echo Syntax-checking parlay_builder.py before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add PUSH_PENDING_SP_GUARD.bat
git status --short
git commit -m "Fail-gracefully on PENDING_SP_DATA rows in _score_pick. Add an early-exit guard at the top of parlay_builder._score_pick that returns (0, 'pending_sp_data --- ungraded') whenever pick == 'TBD' or p_model is None/NaN. Discovered by Run #53 (5/14 manual daily-slate) which contained MIA @ MIN with the probable starter not yet announced. The parlay grader attempted to numeric-cast None when CAP 3's float(f5) ran on that row, raising TypeError that main_predict's outer try/except swallowed --- which silently aborted the entire diag CSV rewrite for the whole slate. Collateral damage was severe: every other row on the slate lost its grade column. Fix is the architecturally correct move per the contract that _score_pick assumes a complete mathematical profile: detect the missing-data state up front and return a clean zero-impact output so the rest of build_diagnostic_table can finish appending all 33 columns. No future cap or rule can now accidentally trip over a NoneType in a pending game."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Time-sensitive: the 14:00 UTC cron is the next scheduled
echo  daily-slate run.  Push this BEFORE 14:00 UTC and the cron
echo  will execute against the fixed code automatically.  If MIA
echo  @ MIN's SP has been announced by then, the slate will fully
echo  grade; if not, the PENDING row will short-circuit safely
echo  and the other 10 rows will keep their grade columns.
echo ============================================================
pause
