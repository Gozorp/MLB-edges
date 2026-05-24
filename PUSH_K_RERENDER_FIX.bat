@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Fix: K-rerender overwrote Top Outcomes with full rows
echo  -----------------------------------------------------------
echo  Bug: _maybeRerenderTopOutcomes ^(triggered when pitcher K
echo  boxscores arrive^) called renderTopProbableOutcomes with
echo  slate.rows ^(all 16^) instead of filtered rows.  Result:
echo  loadSlate's initial render correctly excluded postponed
echo  games, then K-prefetch re-render brought them back.
echo
echo  Fix: apply the same _isPostponedRow filter at the
echo  re-render callsite.  Single-line change.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul
set TMPDIR=%TEMP%\mlb_edge_k_rerender_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_K_RERENDER_FIX.bat"  "%TMPDIR%\PUSH_K_RERENDER_FIX.bat"  >nul
copy /Y "_patch_k_rerender.py"     "%TMPDIR%\_patch_k_rerender.py"     >nul

git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

copy /Y "%TMPDIR%\PUSH_K_RERENDER_FIX.bat"  "PUSH_K_RERENDER_FIX.bat"  >nul
copy /Y "%TMPDIR%\_patch_k_rerender.py"     "_patch_k_rerender.py"     >nul

python _patch_k_rerender.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} blocks')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
del /f /q _patch_k_rerender.py

git add docs/index.html
git add PUSH_K_RERENDER_FIX.bat
git status --short
git commit -m "Dashboard: fix K-rerender that overwrote Top Outcomes with all 16 rows. _maybeRerenderTopOutcomes (called when pitcher K boxscores arrive from prefetch) re-rendered Top Probable Outcomes with slate.rows (the unfiltered list) instead of the playableRows derived in loadSlate. Result: initial render correctly excluded postponed games (TB@NYY, DET@BAL) but K-prefetch's re-render brought them back. Apply same _isPostponedRow filter at the re-render callsite. Single-line fix."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo SUCCESS - K-rerender filter applied.
pause
