@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard: show favored team + prob in F5/FULL/PICK columns
echo  -----------------------------------------------------------
echo  Single file edit: docs/index.html
echo
echo  What changes in the slate table:
echo
echo  BEFORE                          AFTER
echo  PICK: TOR                       PICK: TOR 67.4%%
echo  F5:   55.1%%                     F5:   TOR 55.1%%
echo  FULL: 67.4%%                     FULL: TOR 67.4%%
echo
echo  Why this matters: makes Stage 1/2 disagreements visible
echo  at a glance.  When F5 favors one team and FULL favors
echo  the other (the WSH @ MIA 5/10 case), you'll see
echo  F5: MIA 55.1%% next to FULL: WSH 51.9%% in the same row —
echo  the exact pattern that drove the f5_prob misread bug.
echo  Visible disagreement = faster human override.
echo
echo  Two new helper functions added:
echo    favTeamProb(matchup, homeSideProb) — resolves home-side
echo      probability to favored team + their win prob
echo    pickWithProb(pickTeam, pickSideProb) — combines pick
echo      team with its pick-side win prob (p_model)
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edit to temp...
set TMPDIR=%TEMP%\mlb_edge_pick_display
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"             "%TMPDIR%\docs\index.html"             >nul
copy /Y "PUSH_PICK_DISPLAY.bat"       "%TMPDIR%\PUSH_PICK_DISPLAY.bat"       >nul

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
copy /Y "%TMPDIR%\docs\index.html"             "docs\index.html"             >nul
copy /Y "%TMPDIR%\PUSH_PICK_DISPLAY.bat"       "PUSH_PICK_DISPLAY.bat"       >nul

echo Staging + committing...
git add docs/index.html PUSH_PICK_DISPLAY.bat
git status --short
git commit -m "Dashboard: show favored team + win prob in F5/FULL/PICK columns. Two new helpers in docs/index.html: favTeamProb(matchup, homeSideProb) resolves the home-side probability (which is what f5_prob and full_prob carry in the CSV) to the team it actually favors and prints that team's win prob; pickWithProb(pickTeam, pickSideProb) combines the pick team abbreviation with the pick-side p_model. Slate-table rendering updated so PICK shows 'TOR 67.4%%' instead of just 'TOR', F5 shows 'TOR 55.1%%' instead of '55.1%%', and FULL shows 'TOR 67.4%%' instead of '67.4%%'. The key UX benefit is that Stage 1/2 disagreements become visible at a glance: when F5 favors MIA at 55.1%% but FULL favors WSH at 51.9%% (e.g. the 5/10 WSH @ MIA row), the table now reads 'F5: MIA 55.1%%, FULL: WSH 51.9%%' side by side, which is exactly the pattern the f5_prob misread bug surfaced on. Visible disagreement = faster human override."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS. After Cloudflare redeploys (~1-3 min), reload:
echo  https://mlb-edges.saladin-alfaatih.workers.dev/?cb=pickdisp
echo
echo  Inspect any Stage 1/2 split row to verify F5 and FULL
echo  now name different teams when they disagree.
echo ============================================================
pause
