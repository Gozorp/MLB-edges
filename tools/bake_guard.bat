@echo off
REM ---------------------------------------------------------------------------
REM bake_guard.bat -- scheduled safety net that publishes any slate stranded in
REM the repo root (predict.py ran but run_local_slate's bake didn't). Idempotent
REM and additive: a no-op when docs/data is already in sync. See tools/bake_guard.py.
REM ---------------------------------------------------------------------------
setlocal
cd /d D:\mlb_edge\mlb_edge
set "PATH=C:\Program Files\Git\cmd;C:\Python313;C:\Python313\Scripts;%PATH%"
if not exist logs mkdir logs
echo ==== %DATE% %TIME% bake_guard run ==== >> logs\bake_guard_task.log
C:\Python313\python.exe tools\bake_guard.py --push >> logs\bake_guard_task.log 2>&1
endlocal
