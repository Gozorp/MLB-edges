@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
REM ---- network gate (2026-07-16): wake-from-sleep runs fired before DNS
REM      was up -> getaddrinfo failures -> stale re-bakes. Wait up to 4min.
set NETTRIES=0
:netwait
%PY% -c "import socket;socket.getaddrinfo('statsapi.mlb.com',443)" >nul 2>&1 && goto netok
set /a NETTRIES+=1
if %NETTRIES% GEQ 24 goto netfail
timeout /t 10 /nobreak >nul
goto netwait
:netfail
echo ==== %DATE% %TIME% : SKIPPED - no network after 4min wait ==== >> "logs\slate.log"
exit /b 0
:netok
echo ==== %DATE% %TIME% : slate ==== >> "logs\slate.log"
%PY% tools\run_local_slate.py >> "logs\slate.log" 2>&1
%PY% tools\publish_local.py slate >> "logs\publish.log" 2>&1
