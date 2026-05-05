@echo off
REM ------------------------------------------------------------------
REM game_end_watcher.bat — Windows Task Scheduler entrypoint.
REM
REM Runs the long-running game-end watcher in a minimized console.
REM Triggers intraday recalibration when MLB games end during the day.
REM
REM Schedule with:
REM   schtasks /Create /SC ONLOGON /TN "mlb_edge_game_end_watcher" ^
REM     /TR "D:\mlb_edge\mlb_edge\scripts\game_end_watcher.bat" /RL HIGHEST
REM
REM Logs to %PROJECT_ROOT%\logs\game_end_watcher_YYYYMMDD.log
REM ------------------------------------------------------------------

SET PROJECT_ROOT=D:\mlb_edge\mlb_edge
SET PY=pythonw

cd /d "%PROJECT_ROOT%" >nul 2>&1 || exit /b 1

REM pythonw.exe is the GUI Python entrypoint — runs without spawning a
REM visible console window. The watcher's own logging goes to
REM logs/game_end_watcher_YYYYMMDD.log so we keep full visibility.
%PY% scripts\game_end_watcher.py
exit /b %ERRORLEVEL%
