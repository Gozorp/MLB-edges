@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard hot-fix: signals_to_recheck shape mismatch
echo  -----------------------------------------------------------
echo  Error seen: TypeError on .slice(...).map  when loading 5/9
echo  Cause: claude-postgame.yml writes signals_to_recheck as a
echo  string per matchup; the dashboard JS expected an array
echo  Fix: coerce both shapes into an array before .slice/.map
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edit to temp...
set TMPDIR=%TEMP%\mlb_edge_dashfix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"        "%TMPDIR%\docs\index.html"        >nul
copy /Y "PUSH_DASH_FIX.bat"      "%TMPDIR%\PUSH_DASH_FIX.bat"      >nul
echo.

echo Fetching from origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring fix...
copy /Y "%TMPDIR%\docs\index.html"        "docs\index.html"        >nul
copy /Y "%TMPDIR%\PUSH_DASH_FIX.bat"      "PUSH_DASH_FIX.bat"      >nul

echo Staging + committing...
git add docs/index.html PUSH_DASH_FIX.bat
git status --short
git commit -m "Dashboard hotfix: coerce signals_to_recheck to array. claude-postgame.yml emits this field as a single string per matchup per the schema in claude_postgame_prompt.md, but the dashboard JS at docs/index.html:2518 assumed an array and called .slice(0,5).map(...) on it, which throws TypeError when the value is a string. Coerce string -> single-element array before slice/map so both shapes work (legacy hand-written postgame JSONs may have arrays, auto-generated ones have strings)."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS. Reload the dashboard with a cache-bust:
echo  https://mlb-edges.saladin-alfaatih.workers.dev/?cb=dashfix
echo  Date selector 5/9 should now load without the TypeError.
echo ============================================================
pause
