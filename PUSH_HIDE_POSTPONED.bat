@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard fix: hide POSTPONED games from picks display
echo  -----------------------------------------------------------
echo  PROBLEM: user reported TB @ NYY and DET @ BAL appearing in
echo  Game Picks on 5/23 even though those matchups were
echo  postponed due to rain.  TB @ NYY ^(rescheduled 9/22^) and
echo  DET @ BAL ^(rescheduled 5/24^) are confirmed Postponed via
echo  MLB Stats API.  STL @ CIN is legitimately playing today
echo  ^(makeup of 5/22 PPD + doubleheader nightcap^).
echo
echo  ROOT CAUSE: picks_^<date^>_diag.csv is baked once per slate.
echo  If MLB then postpones games due to rain, the dashboard
echo  still shows the picks because there's no postponement
echo  filter at render time.  fetchMLBResults ALREADY pulls live
echo  status from statsapi ^(statusText field^), so this is purely
echo  a display-side filter — no pipeline changes needed.
echo
echo  CHANGES: single file patched, docs/index.html only.
echo    PATCH 1: insert _isPostponedRow^(row, results^) helper
echo    PATCH 2: loadSlate derives playableRows = rows.filter^(
echo             !_isPostponedRow^).  All three renderers
echo             ^(renderSlate, renderTopProbableOutcomes,
echo             renderBullpenOutlook^) use playableRows.
echo    PATCH 2b: status banner appends "N postponed hidden"
echo    PATCH 3a/b/c: silentRefresh path same filter
echo
echo  window.__slate.rows preserves ALL rows for Ask Claude /
echo  search interface — only the rendered picks are filtered.
echo
echo  Built per locked memory feedback_edit_tool_pivot:
echo  Python str.replace on clean origin tree, no Edit-tool calls.
echo  Dryrun verified 6/6 anchors unique against current origin
echo  ^(post-d9155ac series-indicator merge^), node --check passes.
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: MLB API /api/v1/schedule confirmed
echo                   TB@NYY detailedState=Postponed reschedule=9/22,
echo                   DET@BAL detailedState=Postponed reschedule=5/24,
echo                   STL@CIN games both legitimately playing today
echo    [E] Rule 3  -- node --check gate in this script
echo    [E] Rule 4  -- safe-push pattern ^(git add BEFORE any pull^)
echo    [E] Rule 5  -- single-file display-only change; zero
echo                   pipeline / production-pick paths touched
echo    [E] Rule 6  -- _isPostponedRow degrades to false on any
echo                   missing/malformed data ^(rows pass through^)
echo    [E] Rule 11 -- backward compat: pre-existing pipelines
echo                   write CSVs as before; only render is filtered
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_hide_postponed
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_HIDE_POSTPONED.bat"  "%TMPDIR%\PUSH_HIDE_POSTPONED.bat"  >nul
copy /Y "_patch_postponed.py"      "%TMPDIR%\_patch_postponed.py"      >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring push script + patch script...
copy /Y "%TMPDIR%\PUSH_HIDE_POSTPONED.bat"  "PUSH_HIDE_POSTPONED.bat"  >nul
copy /Y "%TMPDIR%\_patch_postponed.py"      "_patch_postponed.py"      >nul

echo Applying docs/index.html patches...
python _patch_postponed.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

echo File size + tail check on docs/index.html...
python -c "s=open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-80:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Cleaning up companion patch script ^(keep it out of the commit^)...
del /f /q _patch_postponed.py

echo Staging + committing ^(git add BEFORE any pull per locked memory^)...
git add docs/index.html
git add PUSH_HIDE_POSTPONED.bat
git status --short
git commit -m "Dashboard: hide POSTPONED games from picks display. User reported TB@NYY + DET@BAL appearing in Game Picks for 5/23 even though those matchups were rained out (TB@NYY rescheduled to 9/22, DET@BAL rescheduled to 5/24 — both confirmed Postponed via MLB Stats API). Root cause: picks_<date>_diag.csv is baked once per slate; if MLB then postpones a game, the dashboard still renders the pick because there's no postponement filter. fetchMLBResults ALREADY pulls live status from statsapi (statusText field includes 'Postponed' / 'Suspended' / 'Cancelled') so this is purely a display-side filter; no pipeline changes needed. Single-file change to docs/index.html: (1) NEW _isPostponedRow(row, results) helper. Strips any (G2)/(G3) doubleheader suffix from row.matchup before splitting AWAY @ HOME, looks up in results map (keyed by AWAY@HOME and HOME@AWAY without spaces), returns true if statusText matches /postpon|suspend|cancel/i. Best-effort: missing/malformed data returns false. (2) loadSlate derives playableRows = rows.filter(!_isPostponedRow) right after the bullpenMeta fetch; all three renderers (renderSlate, renderTopProbableOutcomes, renderBullpenOutlook) consume playableRows. (3) Status banner appends ', N postponed hidden' when postponedCount > 0. (4) silentRefresh path applies the same filter. window.__slate.rows preserves ALL rows for the Ask Claude / search interface — only the rendered picks are filtered. Built per locked memory feedback_edit_tool_pivot: Python str.replace on clean origin tree, no Edit-tool calls on the 5082-line file. Dryrun verified 6/6 anchors unique against post-d9155ac origin, node --check passes on extracted JS. Verified by MLB API probe: TB@NYY detailedState=Postponed reschedule=9/22, DET@BAL detailedState=Postponed reschedule=5/24, STL@CIN both games legitimately playing (makeup of 5/22 PPD + doubleheader nightcap). Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed, Rule 3 node --check, Rule 4 safe-push (git add BEFORE any pull), Rule 5 single-file display-only (zero pipeline/pick paths touched), Rule 6 helper degrades to false on missing data, Rule 11 backward compat (CSV pipeline unchanged), Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS - postponed-game filter deployed.
echo
echo  Validate ^(after Cloudflare Pages deploys, ~60-90 sec^):
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^)
echo    2. 5/23 slate now shows ONLY playable games
echo    3. Status line: "Loaded 2026-05-23 ^(14 games, 2 postponed hidden^)"
echo    4. TB @ NYY and DET @ BAL gone from Top Probable Outcomes
echo    5. STL @ CIN still shows ^(both games are legitimately playing^)
echo ============================================================
pause
