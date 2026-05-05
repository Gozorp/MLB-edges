@echo off
REM ===========================================================================
REM  run_v7_pipeline.bat
REM  ------------------
REM  v7 pipeline: adds real catcher-framing CSAE (replacing dead 1.0 constants
REM  for home_catcher_penalty / away_catcher_penalty). Cache key bumped v3 -> v4
REM  in build_pipeline.py, so each season's feature cache must rebuild.
REM
REM  PREREQUISITES (verify before running):
REM    * run_v6_pipeline.bat has completed successfully
REM    * bt_2023_v6.csv, bt_2024_v6.csv, bt_2025_v6.csv all exist
REM    * models/latest.pkl and models/totals_latest.pkl saved (v6 retrain)
REM
REM  What it does (sequential):
REM    1. Backtest 2025 with cache v4 -> bt_2025_v7.csv   (~55 min cache rebuild)
REM    2. Backtest 2024 with cache v4 -> bt_2024_v7.csv   (~55 min cache rebuild)
REM    3. Backtest 2023 with cache v4 -> bt_2023_v7.csv   (~55 min cache rebuild)
REM    4. compare_v7.py  (v6 vs v7 ROI delta)
REM    5. Retrain F5  -> models/latest.pkl         (~5 sec)
REM    6. Retrain totals -> models/totals_latest.pkl (~10 sec)
REM
REM  Total wall-clock: ~2.5-3 hours for cache rebuild, ~15 sec retrains.
REM
REM  Resumable: if a bt_*_v7.csv already exists, that step is skipped. Safe
REM  to re-run if interrupted (Ctrl-C, shutdown, power loss).
REM
REM  Logs:
REM    run_v7_pipeline.log  - top-level timeline (start/end per step)
REM    bt_2025_v7.log       - full 2025 backtest output
REM    bt_2024_v7.log       - full 2024 backtest output
REM    bt_2023_v7.log       - full 2023 backtest output
REM    train_f5_v7.log      - F5 retrain output
REM    train_totals_v7.log  - totals retrain output
REM
REM  Watch progress in another window:   type run_v7_pipeline.log
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set MASTER_LOG=run_v7_pipeline.log

call :log "==================================================================="
call :log "STARTING v7 PIPELINE  (cwd: %CD%)"
call :log "  = v6 + catcher framing (CSAE). Rebuilds feature cache v3 -> v4."
call :log "==================================================================="

REM -------------------------------------------------------------------
REM Sanity: pybaseball must be importable
REM -------------------------------------------------------------------
python -c "import pybaseball" 1>nul 2>nul
if errorlevel 1 (
    call :log "FATAL: pybaseball not installed. Run: pip install pybaseball"
    goto :fail
)

REM -------------------------------------------------------------------
REM Sanity: v6 must be complete (we compare against bt_*_v6.csv)
REM -------------------------------------------------------------------
if not exist bt_2023_v6.csv (
    call :log "FATAL: bt_2023_v6.csv missing. Run run_v6_pipeline.bat first."
    goto :fail
)
if not exist bt_2024_v6.csv (
    call :log "FATAL: bt_2024_v6.csv missing. Run run_v6_pipeline.bat first."
    goto :fail
)
if not exist bt_2025_v6.csv (
    call :log "FATAL: bt_2025_v6.csv missing. Run run_v6_pipeline.bat first."
    goto :fail
)

REM -------------------------------------------------------------------
REM Step 1: 2025 backtest  (cache v4 auto-builds on first run)
REM -------------------------------------------------------------------
if exist bt_2025_v7.csv (
    call :log "SKIP  [1/6] 2025 backtest  (bt_2025_v7.csv already exists)"
) else (
    call :log "START [1/6] 2025 backtest  -> bt_2025_v7.log"
    python -m mlb_edge.main --mode backtest --season 2025 --out bt_2025_v7.csv > bt_2025_v7.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [1/6] 2025 backtest  (see bt_2025_v7.log)"
        goto :fail
    )
    call :log "DONE  [1/6] 2025 backtest"
)

REM -------------------------------------------------------------------
REM Step 2: 2024 backtest
REM -------------------------------------------------------------------
if exist bt_2024_v7.csv (
    call :log "SKIP  [2/6] 2024 backtest  (bt_2024_v7.csv already exists)"
) else (
    call :log "START [2/6] 2024 backtest  -> bt_2024_v7.log"
    python -m mlb_edge.main --mode backtest --season 2024 --out bt_2024_v7.csv > bt_2024_v7.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [2/6] 2024 backtest  (see bt_2024_v7.log)"
        goto :fail
    )
    call :log "DONE  [2/6] 2024 backtest"
)

REM -------------------------------------------------------------------
REM Step 3: 2023 backtest
REM -------------------------------------------------------------------
if exist bt_2023_v7.csv (
    call :log "SKIP  [3/6] 2023 backtest  (bt_2023_v7.csv already exists)"
) else (
    call :log "START [3/6] 2023 backtest  -> bt_2023_v7.log"
    python -m mlb_edge.main --mode backtest --season 2023 --out bt_2023_v7.csv > bt_2023_v7.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [3/6] 2023 backtest  (see bt_2023_v7.log)"
        goto :fail
    )
    call :log "DONE  [3/6] 2023 backtest"
)

REM -------------------------------------------------------------------
REM Step 4: compare v6 vs v7
REM -------------------------------------------------------------------
call :log "START [4/6] compare v6 vs v7"
python compare_v7.py > compare_v7_output.txt 2>&1
if errorlevel 1 (
    call :log "FAIL  [4/6] compare_v7.py  (see compare_v7_output.txt)"
    goto :fail
)
call :log "DONE  [4/6] compare v6 vs v7  (results in compare_v7_output.txt)"
type compare_v7_output.txt
type compare_v7_output.txt >> %MASTER_LOG%

REM -------------------------------------------------------------------
REM Step 5: retrain F5 model
REM -------------------------------------------------------------------
call :log "START [5/6] retrain F5 model"
python -m mlb_edge.main --mode train --seasons 2023,2024,2025 --save models/latest.pkl > train_f5_v7.log 2>&1
if errorlevel 1 (
    call :log "FAIL  [5/6] F5 retrain  (see train_f5_v7.log)"
    goto :fail
)
call :log "DONE  [5/6] F5 retrain"

REM -------------------------------------------------------------------
REM Step 6: retrain totals model
REM -------------------------------------------------------------------
call :log "START [6/6] retrain totals model"
python -m mlb_edge.main_totals --mode train --seasons 2023,2024,2025 --save models/totals_latest.pkl > train_totals_v7.log 2>&1
if errorlevel 1 (
    call :log "FAIL  [6/6] totals retrain  (see train_totals_v7.log)"
    goto :fail
)
call :log "DONE  [6/6] totals retrain"

call :log "==================================================================="
call :log "PIPELINE COMPLETE"
call :log "==================================================================="
endlocal
exit /b 0

:fail
call :log "==================================================================="
call :log "PIPELINE FAILED - see per-step .log files for details"
call :log "==================================================================="
endlocal
exit /b 1

REM -------------------------------------------------------------------
REM Timestamped log helper. Writes to console AND master log file.
REM -------------------------------------------------------------------
:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> %MASTER_LOG%
exit /b 0
