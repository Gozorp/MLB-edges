@echo off
cd /d "%~dp0"
echo Registering nightly job (mlb_edge_refit): Daily Variance Report + calibrator refit + publish,
echo running every day at 00:25 (staggered off 00:00 so it can't collide with mlb_edge_slate).
schtasks /create /tn "mlb_edge_refit" /tr "%~dp0jobs\job_refit.bat" /sc DAILY /st 00:25 /f
echo.
schtasks /query /tn "mlb_edge_refit" /fo LIST 2>nul | findstr /i "TaskName Next Status"
echo.
echo (Undo:  schtasks /delete /tn "mlb_edge_refit" /f )
pause
