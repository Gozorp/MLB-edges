@echo off
REM Recover from the unstaged-changes failure in PUSH_TBD_DESIGN_FIXES.bat.
REM The commit went through but git pull --rebase tripped on _patch_tbd_design.py
REM (the helper script itself was unstaged).  Pull with --autostash + push.
cd /d D:\mlb_edge\mlb_edge

echo === git status ===
git status --short

echo === Stage helper + bat for posterity ===
git add _patch_tbd_design.py PUSH_TBD_DESIGN_FIXES.bat RECOVER_AND_PUSH_TBD.bat 2>nul

echo === Amend so the helper is part of the original commit ===
git commit --amend --no-edit
if errorlevel 1 ( echo amend failed; continuing & echo. )

echo === Pull with autostash ===
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === Latest commits ===
git log -3 --oneline
pause
