@echo off
REM ------------------------------------------------------------------
REM nightly_backstop.bat — Windows Task Scheduler entrypoint.
REM
REM Runs the full nightly pipeline: refresh data + retrain + predict +
REM audit. Logs to %PROJECT_ROOT%\logs\nightly_backstop_YYYYMMDD.log.
REM
REM Schedule with:
REM   schtasks /create /sc once /st 00:30 /sd 04/26/2026 ^
REM     /tn "mlb_edge_nightly_backstop" ^
REM     /tr "D:\mlb_edge\mlb_edge\scripts\nightly_backstop.bat"
REM ------------------------------------------------------------------

SET PROJECT_ROOT=D:\mlb_edge\mlb_edge
SET PY=pythonw

cd /d "%PROJECT_ROOT%" >nul 2>&1 || exit /b 1

REM pythonw.exe runs without a console window. stdout/stderr are captured
REM into the script's own log files (logs/nightly_backstop_YYYYMMDD.log)
REM so we don't lose visibility — only the conhost popup is suppressed.
REM --skip-retrain added 2026-04-26 — model is frozen at v12 with the
REM diagnosed-best feature config (single-year SP, light shrinkage,
REM PLATINUM re-enabled). User explicitly wants no more retrains until
REM 7 days of forward outcome data accumulate.
%PY% scripts\nightly_backstop.py --skip-retrain
exit /b %ERRORLEVEL%
