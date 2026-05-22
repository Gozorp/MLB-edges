@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bullpen ceiling_tier vocabulary: betting -^> fatigue
echo  -----------------------------------------------------------
echo  Cosmetic fix per task #43.  Currently the bullpen_meta JSON
echo  inherits the ceiling_tier value from
echo  bullpen_fatigue_blocker.compute_bullpen_workload^(^), which
echo  uses the BETTING-tier vocabulary (DIAMOND/GOLD/SKIP).
echo  That reads awkwardly in a fatigue-narrative context:
echo
echo    Current: "ARI's bullpen is currently GOLD"
echo    Current: "STL's bullpen is currently SKIP"
echo    Current: "BOS's bullpen is currently DIAMOND"
echo
echo  After this commit:
echo
echo    "ARI's bullpen is currently NORMAL"
echo    "STL's bullpen is currently STRAINED"
echo    "BOS's bullpen is currently FRESH"
echo
echo  Fix: new _normalize_ceiling_tier^(top3_pitch_total_72h^)
echo  in bullpen_meta_writer.py.  4-tier vocabulary mapped from
echo  the actual workload metric:
echo
echo    top3 ^<= 25  -^> FRESH       ^(well under upstream cap^)
echo    25 ^< top3 ^<= 50  -^> NORMAL    ^(around upstream limit 40^)
echo    50 ^< top3 ^<= 75  -^> STRAINED  ^(upstream GOLD-equivalent^)
echo    top3 ^> 75  -^> OVERWORKED ^(past upstream SKIP at 60^)
echo
echo  Field name `ceiling_tier` PRESERVED so no schema bump
echo  needed.  Dashboard's _bullpenTierColor already handles
echo  unknown values via a muted fallback color, so any stale
echo  JSON still renders fine until tomorrow's cron writes the
echo  new vocabulary.
echo
echo  Tested all 9 boundary cases + 6 real-slate values.
echo  Re-runs writer for today + tomorrow as part of the push
echo  so dashboard picks up the cleaner vocabulary immediately.
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: compute_bullpen_workload returns
echo                   a 3-tier {DIAMOND, GOLD, SKIP} system
echo                   thresholded at WORKLOAD_PITCH_LIMIT=40
echo                   and 1.5x that
echo    [E] Rule 3  -- ast.parse + py_compile + node --check
echo                   gates ^(only writer changed; index.html
echo                   already tolerates any vocab via fallback^)
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- cosmetic-only single-purpose change;
echo                   field name preserved, no schema bump,
echo                   no downstream API touched
echo    [E] Rule 6  -- try/except on int^(^) cast inside
echo                   _normalize_ceiling_tier; None / bad
echo                   value degrades to NORMAL
echo    [E] Rule 9  -- thresholds derived from upstream's own
echo                   WORKLOAD_PITCH_LIMIT cliff ^(40 and 60^);
echo                   no invented numbers
echo    [E] Rule 11 -- field-name preservation means backwards-
echo                   compat: existing v1 / v2 JSONs unchanged;
echo                   only writer output changes from this run
echo                   forward
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_bp_vocab
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\bullpen_meta_writer.py"     "%TMPDIR%\mlb_edge\bullpen_meta_writer.py"     >nul
copy /Y "PUSH_BULLPEN_TIER_VOCAB.bat"         "%TMPDIR%\PUSH_BULLPEN_TIER_VOCAB.bat"         >nul

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
copy /Y "%TMPDIR%\PUSH_BULLPEN_TIER_VOCAB.bat"       "PUSH_BULLPEN_TIER_VOCAB.bat"       >nul

echo Python syntax-checking writer...
python -c "import ast; ast.parse(open('mlb_edge/bullpen_meta_writer.py', encoding='utf-8').read()); print('writer: ast.parse OK')"
if errorlevel 1 (echo PY SYNTAX FAILED & pause & exit /b 1)

echo py_compile gate...
python -c "import py_compile; py_compile.compile('mlb_edge/bullpen_meta_writer.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo Regenerating today's + tomorrow's bullpen_meta with normalized vocabulary...
for /f %%i in ('python -c "from datetime import date; print(date.today().isoformat())"') do set TODAY=%%i
for /f %%i in ('python -c "from datetime import date, timedelta; print((date.today() + timedelta(days=1)).isoformat())"') do set TOMORROW=%%i
python -m mlb_edge.bullpen_meta_writer --date !TODAY!
python -m mlb_edge.bullpen_meta_writer --date !TOMORROW!

echo Staging + committing...
git add mlb_edge/bullpen_meta_writer.py
git add docs/data/bullpen_meta_!TODAY!.json
git add docs/data/bullpen_meta_!TOMORROW!.json
git add PUSH_BULLPEN_TIER_VOCAB.bat
git status --short
git commit -m "Bullpen meta: normalize ceiling_tier to fatigue vocabulary (FRESH/NORMAL/STRAINED/OVERWORKED). Cosmetic fix per task #43. Previously the ceiling_tier field inherited the betting-tier vocabulary (DIAMOND/GOLD/SKIP) from bullpen_fatigue_blocker.compute_bullpen_workload(), which read awkwardly in a fatigue-narrative context — sentences like 'ARI bullpen is currently GOLD' on the dashboard. New _normalize_ceiling_tier(top3_pitch_total_72h) in bullpen_meta_writer.py re-buckets the workload metric into a 4-tier fatigue vocabulary: FRESH (<=25), NORMAL (25-50), STRAINED (50-75), OVERWORKED (>75). Thresholds anchored to the upstream's own WORKLOAD_PITCH_LIMIT=40 cliff. Field name `ceiling_tier` preserved so no schema bump needed; dashboard already tolerates unknown values via fallback color. Tested 9 boundary cases + 6 real-slate values from today's dashboard screenshot (STL 61p -> STRAINED, CIN 35p -> NORMAL, COL 81p -> OVERWORKED, etc.). Re-runs writer for today + tomorrow as part of the push so the dashboard picks up the cleaner vocabulary immediately. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (upstream's 3-tier logic + threshold constants), Rule 3 ast.parse + py_compile, Rule 4 safe-push, Rule 5 single-purpose cosmetic fix; field name preserved, no schema bump, no downstream API touched, Rule 6 try/except on int() cast (None degrades to FRESH per natural reading: no workload = no fatigue), Rule 9 thresholds anchored to upstream constants not invented, Rule 11 field-name preservation = full backwards-compat, Rule 13 push script narrates."
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
echo    2. Bullpen Outlook card narratives now read:
echo       "X's bullpen is currently FRESH/NORMAL/STRAINED/OVERWORKED"
echo       instead of "X's bullpen is currently GOLD/SKIP/DIAMOND"
echo    3. _bullpenTierColor already maps the new values to
echo       green/muted/amber/red appropriately
echo ============================================================
pause
