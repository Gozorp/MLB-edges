@echo off
REM ===========================================================================
REM  run_v8_pipeline.bat
REM  ------------------
REM  v8 pipeline: adds per-hitter, lineup-aware offense via mlb_edge.lineup.
REM  Cache key bumped v3 -> v5 in build_pipeline.py (v4 was the planned
REM  catcher-framing bump). Each season's feature cache must fully rebuild.
REM
REM  New features (3 added to FULL_FEATURES_EXTRA):
REM    - lineup_vs_sp_gap     (home_lineup_xwoba - away_lineup_xwoba,
REM                            weighted by batting-order PA share, split by
REM                            opposing SP handedness)
REM    - lineup_wrcplus_gap
REM    - lineup_hardhit_gap
REM
REM  All three are constrained monotone +1 in config.FULL_MONOTONE, so the
REM  booster cannot learn a perverse sign on them during overfitting.
REM
REM  Fallback cascade inside lineup_aggregate (per-batter):
REM    1. hand-split YTD (p_throws == opposing SP hand, min 50 PA)
REM    2. overall YTD (any hand, min 50 PA)
REM    3. hitter_fallback -> MLB Stats API season-prior (2025 -> 2024 -> 2023,
REM       min 100 PA) via fallback_stats.hitter_fallback
REM    4. team aggregate (team_offense_fallback dict)
REM
REM  PREREQUISITES (verify before running):
REM    * run_v7_pipeline.bat has completed successfully
REM    * bt_2023_v7.csv, bt_2024_v7.csv, bt_2025_v7.csv all exist
REM    * models/latest.pkl and models/totals_latest.pkl saved (v7 retrain)
REM
REM  What it does (sequential):
REM    1. Backtest 2025 with cache v5 -> bt_2025_v8_lineup.csv  (~55 min rebuild)
REM    2. Backtest 2024 with cache v5 -> bt_2024_v8_lineup.csv  (~55 min rebuild)
REM    3. Backtest 2023 with cache v5 -> bt_2023_v8_lineup.csv  (~55 min rebuild)
REM    4. Retrain F5 + full -> models/latest.pkl                (~15 sec)
REM    5. Retrain totals -> models/totals_latest.pkl            (~10 sec)
REM    6. 2026 OOS backtest raw vs filled (uses retrained model) (~8 min)
REM
REM  Total wall-clock: ~2.5-3 hours cache rebuild + ~25 sec retrains + 8 min.
REM
REM  Resumable: if a bt_*_v8_lineup.csv already exists, that step is skipped.
REM  Safe to re-run if interrupted (Ctrl-C, shutdown, power loss).
REM
REM  Logs:
REM    run_v8_pipeline.log         - top-level timeline (start/end per step)
REM    bt_2025_v8_lineup.log       - full 2025 backtest output
REM    bt_2024_v8_lineup.log       - full 2024 backtest output
REM    bt_2023_v8_lineup.log       - full 2023 backtest output
REM    train_f5_v8_lineup.log      - F5 + full retrain output
REM    train_totals_v8_lineup.log  - totals retrain output
REM    bt_2026_v8_lineup.log       - 2026 OOS lineup-fill backtest output
REM
REM  Watch progress in another window:   type run_v8_pipeline.log
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set MASTER_LOG=run_v8_pipeline.log

call :log "==================================================================="
call :log "STARTING v8 PIPELINE  (cwd: %CD%)"
call :log "  = v7 + per-hitter lineup-aware offense. Rebuilds feature cache v3 to v5."
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
REM Sanity: v7 must be complete (we need a baseline to compare against)
REM -------------------------------------------------------------------
if not exist bt_2025_v7.csv (
    call :log "WARN: bt_2025_v7.csv missing. v8 will still run, but no v7 comparison."
)

REM -------------------------------------------------------------------
REM Step 1: 2025 backtest  (cache v5 auto-builds on first run)
REM -------------------------------------------------------------------
if exist bt_2025_v8_lineup.csv (
    call :log "SKIP  [1/6] 2025 backtest  (bt_2025_v8_lineup.csv already exists)"
) else (
    call :log "START [1/6] 2025 backtest  (log: bt_2025_v8_lineup.log)"
    python -m mlb_edge.main --mode backtest --season 2025 --out bt_2025_v8_lineup.csv > bt_2025_v8_lineup.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [1/6] 2025 backtest  (see bt_2025_v8_lineup.log)"
        goto :fail
    )
    call :log "DONE  [1/6] 2025 backtest"
)

REM -------------------------------------------------------------------
REM Step 2: 2024 backtest
REM -------------------------------------------------------------------
if exist bt_2024_v8_lineup.csv (
    call :log "SKIP  [2/6] 2024 backtest  (bt_2024_v8_lineup.csv already exists)"
) else (
    call :log "START [2/6] 2024 backtest  (log: bt_2024_v8_lineup.log)"
    python -m mlb_edge.main --mode backtest --season 2024 --out bt_2024_v8_lineup.csv > bt_2024_v8_lineup.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [2/6] 2024 backtest  (see bt_2024_v8_lineup.log)"
        goto :fail
    )
    call :log "DONE  [2/6] 2024 backtest"
)

REM -------------------------------------------------------------------
REM Step 3: 2023 backtest
REM -------------------------------------------------------------------
if exist bt_2023_v8_lineup.csv (
    call :log "SKIP  [3/6] 2023 backtest  (bt_2023_v8_lineup.csv already exists)"
) else (
    call :log "START [3/6] 2023 backtest  (log: bt_2023_v8_lineup.log)"
    python -m mlb_edge.main --mode backtest --season 2023 --out bt_2023_v8_lineup.csv > bt_2023_v8_lineup.log 2>&1
    if errorlevel 1 (
        call :log "FAIL  [3/6] 2023 backtest  (see bt_2023_v8_lineup.log)"
        goto :fail
    )
    call :log "DONE  [3/6] 2023 backtest"
)

REM -------------------------------------------------------------------
REM Step 4: retrain F5 + full model on 2023-2025 cache v5
REM -------------------------------------------------------------------
call :log "START [4/6] retrain F5 + full (with lineup features)"
python -m mlb_edge.main --mode train --seasons 2023,2024,2025 --save models/latest.pkl > train_f5_v8_lineup.log 2>&1
if errorlevel 1 (
    call :log "FAIL  [4/6] F5 retrain  (see train_f5_v8_lineup.log)"
    goto :fail
)
call :log "DONE  [4/6] F5 + full retrain"

REM -------------------------------------------------------------------
REM Step 5: retrain totals model
REM -------------------------------------------------------------------
call :log "START [5/6] retrain totals model"
python -m mlb_edge.main_totals --mode train --seasons 2023,2024,2025 --save models/totals_latest.pkl > train_totals_v8_lineup.log 2>&1
if errorlevel 1 (
    call :log "FAIL  [5/6] totals retrain  (see train_totals_v8_lineup.log)"
    goto :fail
)
call :log "DONE  [5/6] totals retrain"

REM -------------------------------------------------------------------
REM Step 6: 2026 OOS backtest -- raw vs MLB-API-filled, with the
REM         retrained model that has lineup features. This is the
REM         headline verdict: does the lineup signal improve 2026
REM         OOS Brier / accuracy vs. v7 on the same 417 games?
REM -------------------------------------------------------------------
call :log "START [6/6] 2026 OOS backtest (raw vs filled, lineup-aware model)"
python backtest_fill_2026.py > bt_2026_v8_lineup.log 2>&1
if errorlevel 1 (
    call :log "FAIL  [6/6] 2026 backtest  (see bt_2026_v8_lineup.log)"
    goto :fail
)
call :log "DONE  [6/6] 2026 OOS backtest"
type bt_2026_v8_lineup.log | findstr /i "brier acc patched"

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
