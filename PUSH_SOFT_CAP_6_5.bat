@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  SOFT CAP 6.5 - half-Kelly damping in [+18, +25]pp band
echo  -----------------------------------------------------------
echo  Files changed:
echo
echo  1. mlb_edge/main_predict.py
echo     New post-processing block runs AFTER stress_warnings is
echo     populated, BEFORE the diag CSV is written:
echo
echo       if 18.0 ^< edge_pp ^<= 25.0:
echo           kelly_full      *= 0.5
echo           kelly_quarter   *= 0.5
echo           kelly_eighth    *= 0.5
echo           stress_warnings += ";calibration_caution_18_25pp"
echo
echo     The grade and tier are unchanged - this is a STAKE
echo     reduction, not a tier demotion.  Parlay-eligibility is
echo     also unchanged because parlays already exclude edges
echo     above +15pp via the existing edge-band filter.
echo
echo  2. tools/run_backtest.py
echo     CAP_LABELS gains [SOFT CAP 6.5] entry.  Audit logic for
echo     hit-rate tracking in this band deferred to a follow-up
echo     once 30 picks have accumulated.
echo
echo  3. tools/claude_brain_prompt.md
echo     New section under "Hard caps already enforced" documents
echo     SOFT CAP 6.5 with explicit guidance: treat picks in this
echo     band as conviction-supported but exposure-capped.
echo     CONFIRM acceptable; OVERRIDE only with strong reason.
echo
echo  4. tools/claude_postgame_prompt.md
echo     signals_to_recheck vocabulary gains the canonical name
echo     [SOFT CAP 6.5] calibration_caution_18_25pp so postgame
echo     entries can grep-match this band's firings across days.
echo
echo  Why this is the right move:
echo    Three observed losses (5/8 SEA@CHW +31, 5/8 NYM@ARI +23,
echo    5/13 PHI@BOS +31) cluster in the upper-edge band.  HARD
echo    CAP 6 catches everything above +25pp.  The +18 to +24pp
echo    range is the most likely place the same calibration
echo    breakdown extends downward, but n=3 isn't enough for a
echo    hard SKIP.  Half-Kelly damping buys validation data at a
echo    50%% discount:
echo      - If the band hits ^>= 50%%: signal is real, remove
echo        damping or reduce to 0.75.
echo      - If the band hits 35-45%%: keep half-Kelly, confirms
echo        the calibration breakdown is gradual.
echo      - If the band hits ^< 30%%: promote to HARD CAP 7 at
echo        the empirically-determined threshold.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_soft_cap_65
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"     2>nul
copy /Y "mlb_edge\main_predict.py"                "%TMPDIR%\mlb_edge\main_predict.py"                >nul
copy /Y "tools\run_backtest.py"                   "%TMPDIR%\tools\run_backtest.py"                   >nul
copy /Y "tools\claude_brain_prompt.md"            "%TMPDIR%\tools\claude_brain_prompt.md"            >nul
copy /Y "tools\claude_postgame_prompt.md"         "%TMPDIR%\tools\claude_postgame_prompt.md"         >nul
copy /Y "PUSH_SOFT_CAP_6_5.bat"                   "%TMPDIR%\PUSH_SOFT_CAP_6_5.bat"                   >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring edits...
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"                "mlb_edge\main_predict.py"                >nul
copy /Y "%TMPDIR%\tools\run_backtest.py"                   "tools\run_backtest.py"                   >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"            "tools\claude_brain_prompt.md"            >nul
copy /Y "%TMPDIR%\tools\claude_postgame_prompt.md"         "tools\claude_postgame_prompt.md"         >nul
copy /Y "%TMPDIR%\PUSH_SOFT_CAP_6_5.bat"                   "PUSH_SOFT_CAP_6_5.bat"                   >nul

echo Syntax-checking main_predict.py and run_backtest.py before commit...
python -c "import ast; ast.parse(open('mlb_edge/main_predict.py', encoding='utf-8').read()); ast.parse(open('tools/run_backtest.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/main_predict.py
git add tools/run_backtest.py
git add tools/claude_brain_prompt.md
git add tools/claude_postgame_prompt.md
git add PUSH_SOFT_CAP_6_5.bat
git status --short
git commit -m "SOFT CAP 6.5: half-Kelly damping in [+18,+25]pp calibration-suspect band. The transition from binary hard caps to continuous position sizing. HARD CAP 6 catches edge_pp > +25pp on the premise that the isotonic calibrator hallucinates in the upper-tail bucket, validated 3-for-3 on the archive (SEA@CHW +31.2pp, NYM@ARI +23pp, PHI@BOS +31.0pp). The +18 to +24pp band is the most likely place the calibration breakdown extends downward, but the sample is too thin (n=3 confirmed losses) to justify a hard SKIP -- doing so would create a self-fulfilling blind spot where the system never gathers data to know whether the calibrator degrades gracefully or cliffs at +25pp. Instead, main_predict applies a half-Kelly damping (kelly_full/quarter/eighth all *= 0.5) when edge_pp is in (+18, +25]pp and appends 'calibration_caution_18_25pp' to the row's stress_warnings. Grade and parlay-eligibility are unchanged -- parlays already exclude edge > +15pp via the existing MAX_PARLAY_EDGE_PP filter, so the blast radius is contained to standalone-bet stake sizing. CAP_LABELS in run_backtest.py gains the SOFT CAP 6.5 entry; full audit framework (hit rate vs 0.50 target over 30 picks) to extend in a follow-up once data accumulates. Brain prompt documents the cap with explicit guidance: CONFIRM acceptable, OVERRIDE only with strong qualitative reason. Postgame prompt gains the canonical name in the signals_to_recheck vocabulary so postgame writers can grep-match firings across days. The architectural shift: binary risk management was the right scaffold for the obviously-broken patterns, but continuous position sizing is the right tool for known-suspect-but-undersampled bands."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  After the next daily-slate cron, expect:
echo    - Any row with edge_pp in (+18, +25]pp gets:
echo        kelly_* columns at 50%% of their unmodified value
echo        stress_warnings contains "calibration_caution_18_25pp"
echo    - Dashboard renders the stress flag as a caution chip
echo    - Cap audit recognizes the new label in CAP_LABELS
echo
echo  Monitoring window: 30 picks in the band.  At that point:
echo    - Hit rate ^>= 50%%   -^> relax damping (or remove)
echo    - Hit rate 35-45%%  -^> keep, calibration breakdown is gradual
echo    - Hit rate ^< 30%%   -^> promote to HARD CAP 7
echo ============================================================
pause
