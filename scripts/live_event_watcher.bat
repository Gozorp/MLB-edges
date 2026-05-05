@echo off
REM ------------------------------------------------------------------
REM live_event_watcher.bat — Windows Task Scheduler entrypoint.
REM
REM Runs the live in-game event watcher continuously. Polls MLB Stats
REM API every 2 min during 15:30-23:30 PDT, detects game-state shifts
REM on bets we placed, and writes alerts to data/.live_alerts_*.jsonl
REM when our edge moves by 5+ pp.
REM
REM Schedule with:
REM   schtasks /Create /SC ONLOGON /TN "mlb_edge_live_event_watcher" ^
REM     /TR "D:\mlb_edge\mlb_edge\scripts\live_event_watcher.bat" /RL HIGHEST
REM ------------------------------------------------------------------

SET PROJECT_ROOT=D:\mlb_edge\mlb_edge
SET PY=pythonw

cd /d "%PROJECT_ROOT%" >nul 2>&1 || exit /b 1

%PY% scripts\live_event_watcher.py
exit /b %ERRORLEVEL%
