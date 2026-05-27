@echo off
REM Fix H2H abbreviation bug: "Chicago White Sox" -> CHW (was CWS) and
REM "Athletics" -> OAK (was ATH). Also bumps localStorage cache key
REM from v1 to v2 so existing stale entries get re-fetched.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

REM Back up the file with our fix.
copy /Y docs\index.html docs\index.html.h2hfix_backup >nul
if errorlevel 1 ( echo backup failed & pause & exit /b 1 )

REM Clear any stuck git state.
if exist .git\index.lock del /F /Q .git\index.lock
if exist .git\MERGE_HEAD del /F /Q .git\MERGE_HEAD
if exist .git\rebase-merge rmdir /S /Q .git\rebase-merge
if exist .git\rebase-apply rmdir /S /Q .git\rebase-apply
git rebase --abort 2>nul
git merge --abort 2>nul

REM Hard reset to clean origin state, then restore our fix.
git fetch origin main
git reset --hard origin/main
if errorlevel 1 ( echo reset failed & pause & exit /b 1 )

copy /Y docs\index.html.h2hfix_backup docs\index.html >nul
del docs\index.html.h2hfix_backup

REM Stage only what we want.
git add docs\index.html PUSH_H2H_FIX.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git diff --cached --stat
echo.

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "fix(dashboard): H2H abbrev map — CWS to CHW, Athletics to OAK" -m "MLB Stats API returns 'Chicago White Sox' which the abbrev map was converting to 'CWS', but our diag CSVs use 'CHW'. Same issue for 'Athletics' (mapped to 'ATH' but CSVs use 'OAK'). The team-name comparison failed, so the game lookup returned null and the row rendered as 'in progress' even though the game was final." -m "Bumped localStorage cache key v1 to v2 so any null entries cached under the buggy v1 keys get re-fetched fresh."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Refresh the dashboard ^(hard refresh: Ctrl+Shift+R^) to see the
    echo fixed rows. The "in progress" entries should now show the
    echo actual final scores.
) else (
    echo no staged changes to commit
)
pause
