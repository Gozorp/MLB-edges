@echo off
REM ---------------------------------------------------------------------------
REM bake_guard.bat -- scheduled slate-integrity net (task: mlb_edge_bake_guard).
REM   1. bake_guard.py      -- publishes any slate whose picks stranded in root
REM                            (predict ran but run_local_slate's bake didn't).
REM   2. sidecar_backfill.py-- regenerates + publishes any DISPLAY sidecar
REM                            (spread/platoon/player_vectors/combo/feature_cov)
REM                            that never generated because run_local_slate's
REM                            later steps didn't run. This is what silently
REM                            broke display features on 2026-07-21.
REM Both are idempotent, additive, display-only (freeze-safe), and share
REM job_lock so they never race the pipeline. A no-op when everything is in sync.
REM ---------------------------------------------------------------------------
setlocal
cd /d D:\mlb_edge\mlb_edge
set "PATH=C:\Program Files\Git\cmd;C:\Python313;C:\Python313\Scripts;%PATH%"
if not exist logs mkdir logs
echo ==== %DATE% %TIME% bake_guard run ==== >> logs\bake_guard_task.log
C:\Python313\python.exe tools\bake_guard.py --push >> logs\bake_guard_task.log 2>&1
C:\Python313\python.exe tools\sidecar_backfill.py --push >> logs\sidecar_backfill_task.log 2>&1
endlocal
