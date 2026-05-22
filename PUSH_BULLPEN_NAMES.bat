@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bullpen names — resolve pitcher_id -^> fullName
echo  -----------------------------------------------------------
echo  User feedback: bullpen entries show "#656464" instead of
echo  the pitcher's actual name.  Fix: single batch call to MLB
echo  Stats API /people?personIds=^<csv^> per slate; inject
echo  `name` field into every reliever entry in the JSON.
echo
echo  Bump: SCHEMA_VERSION 1 -^> 2.  Dashboard's schema check
echo  now accepts both v1 and v2 so a stale v1 JSON keeps
echo  working ^(name field just renders as `#^<id^>` fallback^).
echo
echo  Tested end-to-end: 5 real pitcher IDs resolved cleanly
echo  to ^"Gerrit Cole^", ^"Kevin Ginkel^", ^"Ranger Suarez^",
echo  ^"Zach Davies^", ^"Dylan Cease^".  Bogus IDs degrade
echo  gracefully ^(empty dict^).
echo
echo  Files changed:
echo    1. mlb_edge/bullpen_meta_writer.py
echo       - SCHEMA_VERSION bumped to 2
echo       - new _resolve_pitcher_names^(ids^) helper using
echo         urllib + the /people?personIds= batch endpoint
echo       - top-level writer collects unique pitcher_ids
echo         from all relievers, makes ONE API call, threads
echo         name_map through to _per_team_block
echo       - each reliever entry gains `name` field
echo         ^(None if resolution failed; dashboard falls back^)
echo    2. docs/index.html
echo       - schema check accepts schema_version 1 OR 2
echo       - _bullpenTeamNarrative uses name in "Most-used arm"
echo         sentence ^(falls back to `pitcher #^<id^>`^)
echo       - _bullpenFatigueTable uses name in the Pitcher
echo         column ^(falls back to `#^<id^>`^)
echo    3. PUSH_BULLPEN_NAMES.bat ^(this file^)
echo
echo  After this commit:
echo    - existing bullpen_meta_2026-05-22.json ^(v1, no names^)
echo      keeps rendering as `#^<id^>` ^(backwards-compat^)
echo    - tomorrow's automated cron writes v2 with names
echo    - to populate names IMMEDIATELY, also re-run
echo      GENERATE_BULLPEN_META_NOW.bat after this lands
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: MLB Stats API /people?personIds=
echo                   tested live, returned fullName for all 5
echo                   real IDs
echo    [E] Rule 3  -- ast.parse + py_compile + node --check
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- single targeted feature: name resolution
echo                   only; did NOT also touch ceiling_tier
echo                   normalization or other queued followups
echo    [E] Rule 6  -- name resolution wrapped in try/except;
echo                   any failure leaves name=None and writer
echo                   continues
echo    [E] Rule 11 -- backwards-compat: schema v1 JSONs still
echo                   render correctly via `#^<id^>` fallback
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_bp_names
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "mlb_edge\bullpen_meta_writer.py"   "%TMPDIR%\mlb_edge\bullpen_meta_writer.py"   >nul
copy /Y "docs\index.html"                   "%TMPDIR%\docs\index.html"                   >nul
copy /Y "PUSH_BULLPEN_NAMES.bat"            "%TMPDIR%\PUSH_BULLPEN_NAMES.bat"            >nul

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
copy /Y "%TMPDIR%\mlb_edge\bullpen_meta_writer.py"   "mlb_edge\bullpen_meta_writer.py"   >nul
copy /Y "%TMPDIR%\docs\index.html"                   "docs\index.html"                   >nul
copy /Y "%TMPDIR%\PUSH_BULLPEN_NAMES.bat"            "PUSH_BULLPEN_NAMES.bat"            >nul

echo Python syntax-checking writer...
python -c "import ast; ast.parse(open('mlb_edge/bullpen_meta_writer.py', encoding='utf-8').read()); print('writer: ast.parse OK')"
if errorlevel 1 (echo PY SYNTAX FAILED & pause & exit /b 1)

echo py_compile gate...
python -c "import py_compile; py_compile.compile('mlb_edge/bullpen_meta_writer.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} script blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Regenerating today's + tomorrow's bullpen_meta with names...
for /f %%i in ('python -c "from datetime import date; print(date.today().isoformat())"') do set TODAY=%%i
for /f %%i in ('python -c "from datetime import date, timedelta; print((date.today() + timedelta(days=1)).isoformat())"') do set TOMORROW=%%i
python -m mlb_edge.bullpen_meta_writer --date !TODAY!
python -m mlb_edge.bullpen_meta_writer --date !TOMORROW!

echo Staging + committing...
git add mlb_edge/bullpen_meta_writer.py
git add docs/index.html
git add docs/data/bullpen_meta_!TODAY!.json
git add docs/data/bullpen_meta_!TOMORROW!.json
git add PUSH_BULLPEN_NAMES.bat
git status --short
git commit -m "Bullpen meta: resolve pitcher_id -> fullName via MLB Stats API batch endpoint. User feedback: every reliever entry was showing '#656464' instead of the pitcher's actual name. Fix: new _resolve_pitcher_names() helper does a single GET to /api/v1/people?personIds=<csv> per slate (one HTTP call regardless of how many relievers), parses fullName, and injects `name` field into every reliever entry in the JSON. SCHEMA_VERSION bumped 1->2 to reflect the new field. Dashboard's schema check now accepts both v1 and v2, so a stale v1 JSON keeps working (name falls back to '#<id>'). _bullpenTeamNarrative and _bullpenFatigueTable updated to use the name field with the same fallback. End-to-end tested: 5 real pitcher IDs resolved cleanly (Gerrit Cole, Kevin Ginkel, Ranger Suarez, Zach Davies, Dylan Cease). Bogus IDs degrade gracefully (empty dict). Best-effort wrap: any API failure leaves name=None and the writer continues without raising. Re-runs the writer for today + tomorrow as part of the push so dashboard picks up named data immediately rather than waiting for the next 07:00 UTC cron. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (live test of /people?personIds= confirmed fullName field), Rule 3 ast.parse + py_compile + node --check, Rule 4 safe-push, Rule 5 single targeted feature (name resolution only; ceiling_tier vocabulary fix queued separately as task #43), Rule 6 try/except on URL fetch, Rule 11 backwards-compat preserved (v1 JSONs still render), Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^)
echo    2. Bullpen Outlook card: "Most-used arm:" now shows
echo       a pitcher name instead of "#656464"
echo    3. Detail panel fatigue table: Pitcher column shows
echo       names instead of #ids
echo    4. Deep Analysis ML narrative bullpen section: named
echo ============================================================
pause
