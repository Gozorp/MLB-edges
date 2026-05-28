@echo off
REM Surface final scores on PENDING_SP_DATA rows. Today MIN@CHW + NYY@KC
REM on 5/27 ended up as blank dashes because the model declined to pick
REM (rookie + IL returnee with <100 Statcast pitches). The dashboard
REM now shows "FINAL AWY a-h HOM" in a muted chip for those rows so the
REM user at least sees the game outcome.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Back up the file with our fix.
copy /Y docs\index.html docs\index.html.postgame_backup >nul
if errorlevel 1 ( echo backup failed & pause & exit /b 1 )

git fetch origin main
git reset --hard origin/main

copy /Y docs\index.html.postgame_backup docs\index.html >nul
del docs\index.html.postgame_backup

git add docs\index.html PUSH_POSTGAME_PENDING.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "fix(dashboard): show FINAL score on PENDING_SP_DATA rows after game ends" -m "Today MIN@CHW + NYY@KC on 5/27 rendered as blank dashes in the RESULT column. The model legitimately declined to pick (rookie David Sandlin has 0 career Statcast pitches; Gerrit Cole returning from IL has 12.2 IP which doesn't clear the Savant scrape's qualified-pitcher minimum). The render flow had no branch for 'final game, no pick made' — it fell through to '—'." -m "New branch in The Slate's resultCell logic: when result.isFinal=true and accuracy=null (no pick to grade), render 'FINAL AWY a-h HOM' in a muted gray chip with tooltip 'Model declined to pick (insufficient SP data). Final: X won.' Surfaces the outcome without faking a model accuracy grade."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Hard refresh dashboard ^(Ctrl+Shift+R^). MIN@CHW + NYY@KC RESULT
    echo cells should now show "FINAL MIN 2-15 CHW" / "FINAL NYY 7-0 KC".
) else (
    echo no changes to commit
)
pause
