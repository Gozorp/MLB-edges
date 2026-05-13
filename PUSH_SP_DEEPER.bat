@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Deeper Probable Starter UI
echo  -----------------------------------------------------------
echo  Single file edit: docs/index.html
echo
echo  What changed (3 layers):
echo  1. _extractPitcherStats now pulls BOTH season + career
echo     stats from statsapi (hydrate type=[season,career]).
echo     Adds K/BB ratio, opp BAA, hits/9, plus per-pitcher
echo     career baseline row.
echo
echo  2. _fetchPitcherRecentStarts (NEW) — gameLog endpoint
echo     fetched per pitcher, returns last 3 starts with date,
echo     opp, IP, ER, K. Cached 6h in localStorage to avoid
echo     re-querying on row toggles.
echo
echo  3. _pitcherImpact rewritten from one-liner into a
echo     multi-section block:
echo       - Tier verdict (Elite/Above-avg/League-avg/Below-avg)
echo       - Notes: punch-out arm, command, HR-prone, etc.
echo       - Career baseline + trend (better/worse vs lifetime)
echo       - Recent form: last 3 starts with rolling ERA + hot/
echo         cold flag
echo
echo  Before:
echo    "League-average - neither shifts the line meaningfully."
echo  After:
echo    "Above-average - keeps his side in every inning he
echo     pitches.  Notes: punch-out arm 9.7 K/9; command 1.8
echo     BB/9.  Career: 3.42 ERA over 487 IP across 89 GS -
echo     flat relative to career baseline.  Recent form: 2.40
echo     ERA over last 3 starts."
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edit to temp...
set TMPDIR=%TEMP%\mlb_edge_sp_deeper
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"        "%TMPDIR%\docs\index.html"        >nul
copy /Y "PUSH_SP_DEEPER.bat"     "%TMPDIR%\PUSH_SP_DEEPER.bat"     >nul

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
copy /Y "%TMPDIR%\docs\index.html"        "docs\index.html"        >nul
copy /Y "%TMPDIR%\PUSH_SP_DEEPER.bat"     "PUSH_SP_DEEPER.bat"     >nul

echo Staging + committing...
git add docs/index.html PUSH_SP_DEEPER.bat
git status --short
git commit -m "Probable Starter UI: deeper SP analysis with career + recent form. _extractPitcherStats now extracts season AND career rows (hydrate type=[season,career]), surfaces K/BB ratio, opp BAA, hits/9. New _fetchPitcherRecentStarts hits the gameLog endpoint per pitcher for last-3-starts trend, cached 6h in localStorage. _pitcherImpact rewritten from a single-sentence verdict into a multi-section block: tier headline, signal-specific notes (punch-out arm vs contact-prone, command vs control-wobble, HR-prone, etc.), career baseline with ERA trend vs lifetime, and recent-form mini-table with rolling 3-start ERA and hot/cold flag. <p> wrappers swapped to <div> to accommodate nested block elements in the new richer markup."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS. After Cloudflare redeploys (~1-3 min), reload:
echo  https://mlb-edges.saladin-alfaatih.workers.dev/?cb=spdeep
echo
echo  Expand any game row to see the new Probable Starters
echo  block.  The career line + recent form section need a
echo  second to populate because they fire additional API
echo  calls in parallel with the existing season-stat call;
echo  localStorage caches the recent-starts data for 6h so
echo  subsequent expansions are instant.
echo ============================================================
pause
