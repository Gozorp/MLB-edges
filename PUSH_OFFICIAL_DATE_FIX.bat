@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Fix: data_ingestion ignored officialDate, leaked
echo  rescheduled games into the wrong slate
echo  -----------------------------------------------------------
echo  ROOT CAUSE: mlb_edge/data_ingestion.py fetched
echo  /api/v1/schedule?date=YYYY-MM-DD and accepted every game
echo  MLB returned, without checking officialDate.  MLB lists
echo  postponed games under their ORIGINAL gameDate ^(for
echo  display continuity^), but their officialDate is the
echo  reschedule date.  Result: TB@NYY ^(officialDate=9/22^) and
echo  DET@BAL ^(officialDate=5/24^) leaked into
echo  picks_2026-05-23_diag.csv even though they aren't on
echo  today's books.
echo
echo  TWO FIXES IN ONE COMMIT:
echo
echo  1. PIPELINE ^(mlb_edge/data_ingestion.py^):
echo     fetch_schedule_mlb_api now skips any game whose
echo     officialDate != requested slate date.  Prevents
echo     leakage on future runs.  log.info notes each skip.
echo
echo  2. DISPLAY ^(docs/index.html^):
echo     re-enable narrow _isPostponedRow filter ^(postpone/
echo     suspend/cancel only - NOT Final^) to clean up today's
echo     stale CSV that was baked before the pipeline fix.
echo     Final games stay visible with HIT/MISS grading.
echo     Status banner: "Loaded ... (14 games, 2 postponed hidden)"
echo
echo  Future slate runs won't trigger the display filter
echo  because the CSV won't contain rescheduled games.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul
set TMPDIR=%TEMP%\mlb_edge_official_date_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_OFFICIAL_DATE_FIX.bat"  "%TMPDIR%\PUSH_OFFICIAL_DATE_FIX.bat"  >nul
copy /Y "_patch_official_date.py"     "%TMPDIR%\_patch_official_date.py"     >nul

git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

copy /Y "%TMPDIR%\PUSH_OFFICIAL_DATE_FIX.bat"  "PUSH_OFFICIAL_DATE_FIX.bat"  >nul
copy /Y "%TMPDIR%\_patch_official_date.py"     "_patch_official_date.py"     >nul

python _patch_official_date.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

python -c "import ast; ast.parse(open('mlb_edge/data_ingestion.py',encoding='utf-8').read()); print('ast.parse OK')"
if errorlevel 1 (echo PYTHON SYNTAX FAILED & pause & exit /b 1)

python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} blocks')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
del /f /q _patch_official_date.py

git add docs/index.html
git add mlb_edge/data_ingestion.py
git add PUSH_OFFICIAL_DATE_FIX.bat
git status --short
git commit -m "Pipeline + dashboard: filter games by officialDate, not gameDate. User correctly identified that TB@NYY and DET@BAL appearing in 5/23 picks were never officially on 5/23's slate. MLB Stats API /schedule?date=2026-05-23 returns those entries because they were originally scheduled for 5/23, but their officialDate is the reschedule date (TB@NYY officialDate=2026-09-22, DET@BAL officialDate=2026-05-24). Two fixes in one commit. PIPELINE: mlb_edge/data_ingestion.py fetch_schedule_mlb_api now skips any game whose officialDate != requested slate date (logs each skip at INFO). Prevents the leakage on all future slate runs. DISPLAY: docs/index.html re-enables the narrow _isPostponedRow filter (postpone/suspend/cancel only) so today's already-baked stale CSV still gets cleaned at render time. Final games stay visible with HIT/MISS grading. Status banner reads 'Loaded YYYY-MM-DD (N games, M postponed hidden)'. Once the next slate run uses the patched pipeline the display filter will be a no-op because the CSV won't contain rescheduled games."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo SUCCESS - both fixes deployed.
pause
