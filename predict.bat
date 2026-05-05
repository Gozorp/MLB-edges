@echo off
REM ===========================================================================
REM  predict.bat — single-command MLB-edge slate runner
REM  ------------------------------------------------------------------------
REM  Usage from any Windows command prompt:
REM      predict
REM      predict 2026-04-27
REM      predict 2026-04-27 --bets-only
REM      predict --help
REM
REM  No args     -> today's slate, full diagnostic table for every game.
REM  YYYY-MM-DD  -> override the slate date.
REM  --bets-only -> hide the diagnostic table; print only games that clear
REM                 every gate (recommended slate, model_prob inside band,
REM                 edge >= MIN_EDGE_PCT, etc.).
REM  --skip-scrape, --skip-weights, --skip-all-prep
REM              -> skip the corresponding setup steps.
REM ===========================================================================
setlocal enabledelayedexpansion

REM --- locate this script's directory and cd into it -------------------------
cd /d "%~dp0"

REM --- find a python interpreter ---------------------------------------------
where python >nul 2>&1
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>&1
    if !errorlevel!==0 (
        set "PY=py -3"
    ) else (
        echo [predict] ERROR: no python interpreter found on PATH.
        echo Install Python 3.10+ from https://www.python.org/downloads/ and retry.
        exit /b 1
    )
)

REM --- delegate to the Python launcher ---------------------------------------
%PY% predict.py %*
exit /b %errorlevel%
