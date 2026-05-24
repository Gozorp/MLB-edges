@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Add POSTPONED badge to picks display
echo  -----------------------------------------------------------
echo  Per user choice: keep all 16 visible, but replace the
echo  generic TBD badge on postponed games with a clearly-labeled
echo  POSTPONED chip ^(amber color^) so users can see they're
echo  not bettable.  No filtering - just better labeling.
echo
echo  Changes ^(all in docs/index.html^):
echo    1. _resultChipHtml: render POSTPONED chip in amber
echo    2. _gameMLStatus: return POSTPONED when statusText matches
echo    3. _totalStatus: same
echo    4. _pitcherKStatus: same
echo    5. _sectionSummary: include PPD in tally
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul
set TMPDIR=%TEMP%\mlb_edge_postponed_badge
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_POSTPONED_BADGE.bat"   "%TMPDIR%\PUSH_POSTPONED_BADGE.bat"   >nul
copy /Y "_patch_postponed_badge.py"  "%TMPDIR%\_patch_postponed_badge.py"  >nul

git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

copy /Y "%TMPDIR%\PUSH_POSTPONED_BADGE.bat"   "PUSH_POSTPONED_BADGE.bat"   >nul
copy /Y "%TMPDIR%\_patch_postponed_badge.py"  "_patch_postponed_badge.py"  >nul

python _patch_postponed_badge.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} blocks')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
del /f /q _patch_postponed_badge.py

git add docs/index.html
git add PUSH_POSTPONED_BADGE.bat
git status --short
git commit -m "Dashboard: add POSTPONED badge so rained-out games are visually distinct from TBD. User clarified picks ARE for today's scheduled games (SP names in CSV match MLB API 5/23 probables, not 5/22 actuals); they're just hard to distinguish from genuinely-pending games because both get generic TBD chip. Add POSTPONED status (amber chip) returned by _gameMLStatus, _totalStatus, _pitcherKStatus when results entry has statusText matching /postpon|suspend|cancel/i. Add POSTPONED case to _resultChipHtml. Include PPD count in _sectionSummary tally. No filtering - all 16 games still render. Net +547 chars."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo SUCCESS - POSTPONED badges deployed.
pause
