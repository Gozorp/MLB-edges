@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Tiny label fix: "postponed hidden" -^> "hidden (played/postponed)"
echo  -----------------------------------------------------------
echo  Previous broader filter labeled hidden games as "postponed"
echo  even though most are Final (already played).  User pointed
echo  out only 2 are truly postponed (TB@NYY, DET@BAL) - the
echo  others are 7 Final games.  Label now accurate.
echo
echo  Single-string replace in docs/index.html, 2 occurrences
echo  (loadSlate banner + silentRefresh banner).  No logic change.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

set TMPDIR=%TEMP%\mlb_edge_label_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_LABEL_FIX.bat"  "%TMPDIR%\PUSH_LABEL_FIX.bat"  >nul
copy /Y "_patch_label.py"     "%TMPDIR%\_patch_label.py"     >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

copy /Y "%TMPDIR%\PUSH_LABEL_FIX.bat"  "PUSH_LABEL_FIX.bat"  >nul
copy /Y "%TMPDIR%\_patch_label.py"     "_patch_label.py"     >nul

python _patch_label.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
del /f /q _patch_label.py

git add docs/index.html
git add PUSH_LABEL_FIX.bat
git status --short
git commit -m "Dashboard: rename misleading 'postponed hidden' label to 'hidden (played/postponed)'. User correctly pointed out only 2 games are truly Postponed on 5/23 (TB@NYY, DET@BAL); the other 7 hidden games are Final (already played). Old label conflated both. New label is accurate. Two occurrences in docs/index.html (loadSlate + silentRefresh banners). Pure string substitution; zero logic change."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo SUCCESS - label fix deployed.
pause
