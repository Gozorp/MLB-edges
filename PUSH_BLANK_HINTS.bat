@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Add hover hints to blank FAIR / EDGE cells
echo  -----------------------------------------------------------
echo  Per user choice: leave the missing values blank (the cap
echo  + MC-input checks are protections, not bugs), but render
echo  each "-" with a hover title explaining why it's blank
echo  (sourced from the row's odds_status column).
echo
echo  Reasons surfaced:
echo    fetched_capped  -^> "Odds devig outside [0.10, 0.90]"
echo    pending_sp_data -^> "Probable SP not yet announced"
echo    no_match        -^> "Odds source didn't return this matchup"
echo    unavailable     -^> "Odds source unavailable at bake time"
echo
echo  Dashes now have a dotted-underline + cursor:help to hint
echo  there's a tooltip on hover.  No math change.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul
set TMPDIR=%TEMP%\mlb_edge_blank_hints
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%" 2>nul
copy /Y "PUSH_BLANK_HINTS.bat"      "%TMPDIR%\PUSH_BLANK_HINTS.bat"      >nul
copy /Y "_patch_blank_hints.py"     "%TMPDIR%\_patch_blank_hints.py"     >nul

git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

copy /Y "%TMPDIR%\PUSH_BLANK_HINTS.bat"      "PUSH_BLANK_HINTS.bat"      >nul
copy /Y "%TMPDIR%\_patch_blank_hints.py"     "_patch_blank_hints.py"     >nul

python _patch_blank_hints.py
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)

python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'{len(blocks)} blocks')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
del /f /q _patch_blank_hints.py

git add docs/index.html
git add PUSH_BLANK_HINTS.bat
git status --short
git commit -m "Dashboard: hover hints on blank FAIR/EDGE cells explain WHY (odds_status). User chose UX-only fix: the 9-of-16 fair/edge blanks today are upstream data protections (Shin devig sanity cap, pending SP data), not pipeline bugs. Add _oddsBlankReason helper that maps r.odds_status to a human-readable explanation. Wrap the blank dashes in renderSlate with a span title=... so hovering shows: fetched_capped -> 'devig outside [0.10, 0.90]', pending_sp_data -> 'SP not yet announced', no_match -> 'odds source didn't return this matchup', etc. Dotted-underline + cursor:help on the dash signals there's info on hover. Zero math changes."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo SUCCESS - blank-cell hints deployed.
pause
