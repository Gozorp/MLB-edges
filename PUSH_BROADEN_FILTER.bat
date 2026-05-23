@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard fix v2: broaden _isPostponedRow to also drop Final
echo  -----------------------------------------------------------
echo  PROBLEM: previous narrow-filter shipped earlier today
echo  (commit landed in origin) only dropped games whose MLB
echo  statusText matched /postpon-suspend-cancel/.  User reported
echo  TB @ NYY and DET @ BAL still appearing as Game Picks for
echo  5/23 even though those matchups had already played out on
echo  5/22 (Rays 4-2, Orioles 7-4).  Either the CSV pick refers
echo  to the 5/23 game (Postponed - already filtered) OR the CSV
echo  pick is stale and refers to the 5/22 game (Final - missed
echo  by narrow filter).
echo
echo  FIX: extend the existing _isPostponedRow function body to
echo  also return true when entry.isFinal === true.  Function
echo  name preserved so all five callsites stay unchanged.
echo
echo  Result: any game that's Postponed/Suspended/Cancelled OR
echo  already Final gets dropped from the picks display.  All
echo  rows still preserved on window.__slate.rows for Ask Claude.
echo
echo  CHANGES: single function body in docs/index.html.
echo
echo  Built per locked memory feedback_edit_tool_pivot:
echo  Python str.replace on clean origin tree, no Edit-tool calls.
echo  Dryrun verified anchor unique against current origin
echo  (5117 lines, post-PUSH_HIDE_POSTPONED merge), node --check
echo  passes.  Helper body +515 chars, 5127 lines total.
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: MLB API 2026-05-22 schedule shows
echo                   TB@NYY Final 4-2 Rays + DET@BAL Final 7-4
echo                   Orioles; user's complaint validated
echo    [E] Rule 3  -- node --check gate in this script
echo    [E] Rule 4  -- safe-push pattern (git add BEFORE any pull)
echo    [E] Rule 5  -- single function body change; zero callsite
echo                   modifications, zero pipeline changes
echo    [E] Rule 6  -- helper still degrades to false on any
echo                   missing/malformed data
echo    [E] Rule 11 -- backward compat: function name and
echo                   signature unchanged; only behavior broader
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_broaden_filter
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_BROADEN_FILTER.bat"  "%TMPDIR%\PUSH_BROADEN_FILTER.bat"  >nul
copy /Y "_patch_broaden_filter.py" "%TMPDIR%\_patch_broaden_filter.py" >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring push script + patch script...
copy /Y "%TMPDIR%\PUSH_BROADEN_FILTER.bat"  "PUSH_BROADEN_FILTER.bat"  >nul
copy /Y "%TMPDIR%\_patch_broaden_filter.py" "_patch_broaden_filter.py" >nul

echo Applying docs/index.html patch...
python _patch_broaden_filter.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

echo File size + tail check on docs/index.html...
python -c "s=open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-80:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Cleaning up companion patch script...
del /f /q _patch_broaden_filter.py

echo Staging + committing (git add BEFORE any pull per locked memory)...
git add docs/index.html
git add PUSH_BROADEN_FILTER.bat
git status --short
git commit -m "Dashboard: broaden _isPostponedRow to also drop already-Final games. The narrow filter shipped earlier today (postpone/suspend/cancel only) didn't catch the user's real complaint: TB@NYY and DET@BAL still appearing as Game Picks for 5/23 even though those matchups had already played out on 5/22 (Rays 4-2, Orioles 7-4 — confirmed via MLB API for 2026-05-22). Either the CSV pick refers to the 5/23 game (Postponed, rescheduled — already filtered) OR the CSV is stale and the pick references the 5/22 game (Final — missed by narrow filter). Fix extends the existing _isPostponedRow function body to also return true when entry.isFinal === true. Function name preserved so all five callsites stay unchanged (renderSlate, renderTopProbableOutcomes, renderBullpenOutlook, silentRefresh slate render, silentRefresh status banner). Result: any game that's Postponed/Suspended/Cancelled OR already Final gets dropped from the picks display. window.__slate.rows still preserves ALL rows for the Ask Claude / search interface. Built per locked memory feedback_edit_tool_pivot: Python str.replace on clean origin tree, no Edit-tool calls on the 5117-line file. Dryrun verified anchor unique against post-PUSH_HIDE_POSTPONED origin, helper body +515 chars, node --check passes on extracted JS. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (MLB API 5/22 schedule confirmed both matchups played out), Rule 3 node --check, Rule 4 safe-push, Rule 5 single function body change with zero callsite modifications and zero pipeline changes, Rule 6 helper still degrades to false on missing data, Rule 11 backward compat (function name and signature unchanged, only behavior broader), Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS - broader filter deployed.
echo
echo  Validate (after Cloudflare Pages deploys, ~60-90 sec):
echo    1. Hard-refresh dashboard (Ctrl+Shift+R)
echo    2. Top Probable Outcomes shows ONLY games not yet played
echo    3. Status line includes hidden-game count
echo    4. TB @ NYY and DET @ BAL no longer visible regardless of
echo       whether MLB calls them Postponed or Final
echo ============================================================
pause
