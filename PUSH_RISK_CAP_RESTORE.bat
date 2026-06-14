@echo off
REM PUSH_RISK_CAP_RESTORE.bat -- RETURN task (~7/14): restore MAX_DAILY_RISK_UNITS 10 -> 15.
REM Mirror of PUSH_RISK_CAP_TRAVEL.bat. Only run when you've DECIDED to restore (or keep 10).
cd /d "%~dp0"
set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
echo === Deploy: restore MAX_DAILY_RISK_UNITS 10 -^> 15 (post-travel) ===
git rebase --abort 1>nul 2>nul
git merge --abort 1>nul 2>nul
if exist ".git\index.lock" del /f /q ".git\index.lock"
git fetch origin main || (echo FETCH FAILED & pause & exit /b 1)
git reset --hard origin/main || (echo RESET FAILED & pause & exit /b 1)
%PY% _patch_risk_cap_restore.py --apply
if errorlevel 1 (echo PATCH FAILED & pause & exit /b 1)
%PY% -m py_compile mlb_edge\config.py
if errorlevel 1 (echo COMPILE FAILED & pause & exit /b 1)
git add mlb_edge\config.py
git diff --cached --quiet && (echo Nothing to commit ^(already 15?^) & pause & exit /b 0)
git commit -m "risk: restore MAX_DAILY_RISK_UNITS 10->15 post SFO->Japan travel"
git pull --rebase --autostash origin main
git push origin main
echo.
echo === DONE. 'main -^> main' above = pushed to cloud + local now at cap=15. ===
pause
