@echo off
REM ------------------------------------------------------------------
REM mlb_edge auto-runner launcher
REM ------------------------------------------------------------------
REM Installs as a Windows startup task by copying this .bat (or a shortcut
REM to it) into the Startup folder:
REM     %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
REM
REM What it does:
REM   1. cd into the mlb_edge project root
REM   2. run the env/data integrity check
REM   3. launch auto_runner.py in a detached console that survives logout
REM
REM To install:
REM     1. Press Win+R, type:  shell:startup
REM     2. Drop a shortcut to this file in the folder that opens.
REM     3. Reboot — the runner starts automatically on next login.
REM
REM To remove: delete the shortcut from the Startup folder.
REM ------------------------------------------------------------------

SET PROJECT_ROOT=D:\mlb_edge\mlb_edge
SET PY=python
SET LOG=%PROJECT_ROOT%\logs\bootstrap.log

cd /d "%PROJECT_ROOT%" || (
    echo [%date% %time%] Failed to cd to %PROJECT_ROOT% >> "%LOG%"
    exit /b 1
)

echo [%date% %time%] boot: startup_check >> "%LOG%"
%PY% scripts\startup_check.py >> "%LOG%" 2>&1

echo [%date% %time%] boot: launching auto_runner >> "%LOG%"
start "mlb_edge_auto_runner" /min %PY% scripts\auto_runner.py
