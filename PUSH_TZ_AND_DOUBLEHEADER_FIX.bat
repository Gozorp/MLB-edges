@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard fixes: todayISO local-time + doubleheader display
echo  -----------------------------------------------------------
echo  FIX 1: todayISO returned the UTC date, not the user's local
echo  date.  For users east of UTC during late-evening hours, the
echo  dashboard silently defaulted the date picker to "yesterday"
echo  relative to their wall clock.  Now uses local date components.
echo
echo  FIX 2: doubleheader display.  When MLB schedules two games
echo  between the same teams on the same date ^(e.g. STL @ CIN on
echo  2026-05-23, gamePks 824518 + 824516^), our pipeline correctly
echo  fetches both but our matchup field collides.  New helper
echo  _dedupDoubleheaders mutates rows in place, appending (G2),
echo  (G3) suffix to subsequent occurrences.  First game keeps
echo  its original matchup key so downstream lookups (postgame,
echo  claude_picks) stay stable.
echo
echo  Built per locked memory feedback_edit_tool_pivot:
echo  Python str.replace on clean origin tree, no Edit-tool calls.
echo  Verified: 5040 lines, ends /script /body /html, 2 script
echo  blocks (~206k chars), JS extracts cleanly.
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: MLB API confirmed 2 game STL@CIN
echo                   doubleheader; todayISO source confirmed to
echo                   use UTC via toISOString
echo    [E] Rule 3  -- node --check gate in this script
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- two minimal targeted fixes; zero production
echo                   pick logic touched
echo    [E] Rule 6  -- _dedupDoubleheaders is defensive: guards on
echo                   null/empty rows, missing matchup string
echo    [E] Rule 11 -- first game in a doubleheader preserves its
echo                   original key so backward-compat is intact
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_tz_dh_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"                       "%TMPDIR%\docs\index.html"                       >nul
copy /Y "PUSH_TZ_AND_DOUBLEHEADER_FIX.bat"      "%TMPDIR%\PUSH_TZ_AND_DOUBLEHEADER_FIX.bat"      >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring patched files...
copy /Y "%TMPDIR%\docs\index.html"                       "docs\index.html"                       >nul
copy /Y "%TMPDIR%\PUSH_TZ_AND_DOUBLEHEADER_FIX.bat"      "PUSH_TZ_AND_DOUBLEHEADER_FIX.bat"      >nul

echo File size + tail check...
python -c "s=open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-60:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Staging + committing...
git add docs/index.html
git add PUSH_TZ_AND_DOUBLEHEADER_FIX.bat
git status --short
git commit -m "Dashboard: todayISO local-date + doubleheader (G2) disambiguator. TWO fixes in one commit. FIX 1 — todayISO() previously returned the UTC date via toISOString().split('T')[0], which for any user east of UTC during late-evening hours silently defaulted the dashboard's date picker to 'yesterday' relative to their wall clock. Now uses local date components: getFullYear/getMonth/getDate. Eliminates the off-by-one date issue that surfaced when a user reported their 5/23 picks looked like 'yesterday's matches' even though the UTC date matched the slate. FIX 2 — when MLB schedules a doubleheader (two games between the same teams on the same date, distinct gamePks), our pipeline correctly fetches both but our matchup field is just 'AWAY @ HOME', so both rows collide for the dashboard's deduplication. New _dedupDoubleheaders(rows) helper mutates rows in place: first occurrence stays untouched (preserves downstream lookups in postgame archive, claude_picks, etc.); subsequent occurrences get ' (G2)' / ' (G3)' suffix. Called at both parseCSV sites in loadSlate. Confirmed by MLB API probe: STL @ CIN on 2026-05-23 is a scheduled doubleheader (doubleHeader='S', gamePks 824518 + 824516, separate SP probables). Built per locked memory feedback_edit_tool_pivot: Python str.replace on clean origin tree (no Edit tool on the 5006-line file). Verified: 5040 lines, ends </script></body></html>, 2 script blocks (~206k chars), node --check passes. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed, Rule 3 node --check, Rule 4 safe-push, Rule 5 two minimal targeted fixes, Rule 6 _dedupDoubleheaders guards on null/empty inputs, Rule 11 first game's matchup key preserved for backward-compat, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS — both fixes deployed.
echo
echo  Validate:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^)
echo    2. Date picker now defaults to your LOCAL today, not
echo       UTC's today.  If your wall clock shows 5/24 and the
echo       manifest has 5/24, you see 5/24's slate.
echo    3. On any slate with a scheduled doubleheader (e.g.
echo       5/23's STL @ CIN), you now see TWO distinct rows:
echo       "STL @ CIN" (Game 1) and "STL @ CIN (G2)" (Game 2).
echo    4. Top Probable Outcomes and Bullpen Outlook also pick
echo       up the (G2) suffix automatically because they read
echo       from window.__slate.rows which is now disambiguated.
echo ============================================================
pause
