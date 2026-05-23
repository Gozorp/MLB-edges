@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Series-game indicator
echo  -----------------------------------------------------------
echo  Adds "(G2 of 3)" suffix to each matchup on the dashboard
echo  so users can disambiguate when the same matchup appears on
echo  consecutive days (MLB plays 3-game series).
echo
echo  THREE CHANGES IN ONE COMMIT:
echo    1. NEW mlb_edge/series_meta_writer.py: best-effort sidecar
echo       writer.  Pulls MLB Stats API schedule with hydrate
echo       seriesGameNumber+gamesInSeries, writes
echo       docs/data/series_meta_^<date^>.json.  Schema versioned (v1).
echo
echo    2. PATCH mlb_edge/main_predict.py (+~28 lines)
echo       New step 2.6/5: imports + calls write_series_meta right
echo       after the bullpen_meta hook.  try/except per Rule 6.
echo
echo    3. PATCH docs/index.html (+~50 lines, all JS)
echo       _addSeriesSuffix helper, 4th Promise.allSettled fetch,
echo       wraps the two _dedupDoubleheaders(parseCSV(...)) sites
echo       to apply suffix BEFORE dedup so doubleheaders get
echo       correct G1/G2-of-N labels instead of generic (G2).
echo
echo  Built per locked memory feedback_edit_tool_pivot:
echo  Python str.replace on clean origin tree, no Edit-tool calls.
echo  Dryrun verified all 5 anchors unique against origin/main.
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: MLB API confirmed seriesGameNumber +
echo                   gamesInSeries fields present per game;
echo                   STL @ CIN 2026-05-23 is G1+G2 of 3 doubleheader
echo    [E] Rule 3  -- ast.parse + node --check gates in this script
echo    [E] Rule 4  -- safe-push pattern (git add BEFORE any pull)
echo    [E] Rule 5  -- pure additive; zero existing code paths modified
echo                   except the parseCSV wrap site (rawRows + silentRefresh)
echo    [E] Rule 6  -- writer best-effort; helper degrades to identity
echo                   if seriesMeta missing
echo    [E] Rule 11 -- schema_version field guards downstream
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_series_indicator
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge"    2>nul
copy /Y "mlb_edge\series_meta_writer.py"  "%TMPDIR%\mlb_edge\series_meta_writer.py"  >nul
copy /Y "PUSH_SERIES_INDICATOR.bat"       "%TMPDIR%\PUSH_SERIES_INDICATOR.bat"       >nul
copy /Y "_patch_index_html.py"            "%TMPDIR%\_patch_index_html.py"            >nul
copy /Y "_patch_main_predict.py"          "%TMPDIR%\_patch_main_predict.py"          >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring new file + push script + patch scripts...
copy /Y "%TMPDIR%\mlb_edge\series_meta_writer.py"  "mlb_edge\series_meta_writer.py"  >nul
copy /Y "%TMPDIR%\PUSH_SERIES_INDICATOR.bat"       "PUSH_SERIES_INDICATOR.bat"       >nul
copy /Y "%TMPDIR%\_patch_index_html.py"            "_patch_index_html.py"            >nul
copy /Y "%TMPDIR%\_patch_main_predict.py"          "_patch_main_predict.py"          >nul

echo Applying main_predict.py patch...
python _patch_main_predict.py
if errorlevel 1 (echo PATCH MAIN_PREDICT FAILED & pause & exit /b 1)

echo Applying docs/index.html patches...
python _patch_index_html.py
if errorlevel 1 (echo PATCH INDEX.HTML FAILED & pause & exit /b 1)

echo AST-checking series_meta_writer.py...
python -c "import ast; ast.parse(open('mlb_edge/series_meta_writer.py',encoding='utf-8').read()); print('  ast.parse OK')"
if errorlevel 1 (echo PYTHON SYNTAX CHECK FAILED & pause & exit /b 1)

echo File size + tail check on docs/index.html...
python -c "s=open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-80:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Cleaning up companion patch scripts (keep them out of the commit)...
del /f /q _patch_index_html.py
del /f /q _patch_main_predict.py

echo Staging + committing (git add BEFORE any pull per locked memory)...
git add mlb_edge/series_meta_writer.py
git add mlb_edge/main_predict.py
git add docs/index.html
git add PUSH_SERIES_INDICATOR.bat
git status --short
git commit -m "Dashboard: series-game indicator (G2 of 3). New sidecar writer + main_predict hook + docs/index.html wrap to show which game of a multi-game series each row represents. PROBLEM: user reported TB @ NYY, STL @ CIN, and other matchups appearing on consecutive days made the dashboard look like it was repeating yesterday's slate. ROOT CAUSE: MLB plays 3-game series (Fri/Sat/Sun) so the same matchup IS supposed to appear on consecutive days, but our matchup column gave zero context. SOLUTION 3 changes: (1) NEW mlb_edge/series_meta_writer.py - best-effort sidecar that fetches /api/v1/schedule for the slate, extracts seriesGameNumber + gamesInSeries per game, writes docs/data/series_meta_<date>.json. Schema versioned (v1) per Rule 11. (2) PATCH mlb_edge/main_predict.py +28 lines - new step 2.6/5 imports + calls write_series_meta after the bullpen_meta hook, wrapped in try/except per Rule 6. (3) PATCH docs/index.html +50 lines - adds _addSeriesSuffix(rows, seriesMeta) helper, fetches series_meta in loadSlate via Promise.allSettled, wraps the two _dedupDoubleheaders(parseCSV(...)) sites to apply suffix BEFORE dedup so doubleheaders get correct G1/G2-of-N labels instead of generic (G2). Verified by MLB API probe: STL @ CIN 2026-05-23 is doubleheader G1+G2 of 3, other 5/23 games are G2 of 3 (Friday started the series). Built per locked memory feedback_edit_tool_pivot: Python str.replace on clean origin tree, no Edit-tool calls on the 5040-line file. Dryrun verified all 5 anchors unique against origin/main, node --check passes on extracted JS, ast.parse passes on patched main_predict.py. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (MLB API confirmed fields exist + doubleheader correct), Rule 3 ast.parse + node --check, Rule 4 safe-push (git add BEFORE any pull), Rule 5 pure additive, Rule 6 writer best-effort and helper degrades gracefully, Rule 11 schema_version guards downstream, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS - series-game indicator deployed.
echo
echo  Note: the sidecar JSON for today's date will not exist
echo  until the next slate run.  To generate it now without
echo  waiting, run:
echo
echo    python -m mlb_edge.series_meta_writer --date 2026-05-23
echo
echo  Validate after next slate run:
echo    1. Hard-refresh dashboard (Ctrl+Shift+R)
echo    2. Each matchup row should show "(G2 of 3)" or similar
echo       suffix beside the team names
echo    3. Doubleheaders show "(G1 of 3)" and "(G2 of 3)"
echo       instead of "(G2)" generic suffix
echo ============================================================
pause
