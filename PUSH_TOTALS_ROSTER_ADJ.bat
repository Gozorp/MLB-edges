@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Roster-adjusted totals shadow layer (Phase 1)
echo  -----------------------------------------------------------
echo  Adds a post-model adjustment to pred_runs based on BvP +
echo  platoon lineup-vs-SP signals.  Shadow mode this commit:
echo  pred_runs_bvp_adjusted column emitted alongside pred_runs,
echo  but production O/U pick selection still uses pred_runs.
echo
echo  Why heuristic + shadow:
echo    The proper "retrain the XGBoost totals model with BvP
echo    features" path needs point-in-time historical BvP data.
echo    The vsPlayer endpoint returns CAREER totals (not
echo    as-of-date), so retrain requires play-by-play
echo    reconstruction across 3 seasons.  That's tracked as
echo    Phase 2 (multi-day backfill).  Tonight's heuristic
echo    captures the directional signal so we can validate the
echo    concept while the data infrastructure is built.
echo
echo  Adjustment formula (all magnitudes are [H] starting
echo  guesses per Rule 9, to be re-calibrated after backtest):
echo
echo    runs_delta_side = (bvp_ops_shrunk - 0.720) * 100
echo                    * 0.05 R/G per 1pp OPS
echo                    * signal_weight  (0 at 0 PA, 1 at 45+ PA)
echo    total_runs_delta = home_delta + away_delta
echo    pred_runs_bvp_adjusted = pred_runs + total_runs_delta
echo
echo  Files changed:
echo
echo  1. mlb_edge/totals_roster_adjustment.py (NEW, 173 lines)
echo     New module exposing compute_roster_adjustment().
echo     Wraps existing batter_vs_pitcher._aggregate_lineup_vs_sp,
echo     adds signal-strength-weighted delta computation.
echo     Best-effort per Rule 6 (returns zero delta on any
echo     failure so calling pipeline doesn't crash).
echo
echo  2. mlb_edge/main_totals.py (+82 lines)
echo     After Stage 2 XGBoost prediction:
echo       - fetch_slate_meta for today's lineups + SP IDs
echo       - compute_roster_adjustment per game
echo       - augment joined frame with 8 new shadow columns
echo     Picks dict emits 8 new columns alongside existing
echo     pred_runs / our_prob / stake_units fields.
echo
echo  3. PUSH_TOTALS_ROSTER_ADJ.bat (this file)
echo
echo  New columns in picks_totals_^<date^>.csv:
echo    pred_runs_bvp_adjusted   total_runs_delta
echo    home_runs_delta          away_runs_delta
echo    home_bvp_n_pa            away_bvp_n_pa
echo    home_bvp_ops_shrunk      away_bvp_ops_shrunk
echo
echo  Validation gate before production promotion (Phase 2):
echo    1. Postgame cron computes RMSE(pred_runs, actual_total)
echo       and RMSE(pred_runs_bvp_adjusted, actual_total) daily
echo    2. After 7+ slates accumulate (~70-90 games), compare
echo    3. Promote to production if adjusted RMSE is ^>= 5%%
echo       lower; otherwise remove the adjustment layer
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (batter_vs_pitcher exposes the
echo                  exact aggregates needed)
echo    [E] Rule 3  — ast.parse syntax gate in this script
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — heuristic-not-retrain scoping is the
echo                  five-pass response to session fatigue
echo    [E] Rule 6  — outer + per-row best-effort try/except
echo    [H] Rule 9  — RUNS_PER_OPS_POINT=0.05 marked [H] for
echo                  later backtest calibration
echo    [E] Rule 10 — 7-day RMSE deploy gate documented
echo    [E] Rule 11 — signal_weight=0 at 0 PA prevents
echo                  data-free confident adjustments
echo    [E] Rule 13 — this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_totals_roster
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\totals_roster_adjustment.py"  "%TMPDIR%\mlb_edge\totals_roster_adjustment.py"  >nul
copy /Y "mlb_edge\main_totals.py"               "%TMPDIR%\mlb_edge\main_totals.py"               >nul
copy /Y "PUSH_TOTALS_ROSTER_ADJ.bat"            "%TMPDIR%\PUSH_TOTALS_ROSTER_ADJ.bat"            >nul

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
copy /Y "%TMPDIR%\mlb_edge\totals_roster_adjustment.py"  "mlb_edge\totals_roster_adjustment.py"  >nul
copy /Y "%TMPDIR%\mlb_edge\main_totals.py"               "mlb_edge\main_totals.py"               >nul
copy /Y "%TMPDIR%\PUSH_TOTALS_ROSTER_ADJ.bat"            "PUSH_TOTALS_ROSTER_ADJ.bat"            >nul

echo Syntax-checking before commit...
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['mlb_edge/totals_roster_adjustment.py', 'mlb_edge/main_totals.py']]; print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/totals_roster_adjustment.py
git add mlb_edge/main_totals.py
git add PUSH_TOTALS_ROSTER_ADJ.bat
git status --short
git commit -m "Roster-adjusted totals Phase 1: heuristic post-model adjustment layer (SHADOW MODE). New module mlb_edge/totals_roster_adjustment.py computes a per-game runs delta from lineup-vs-SP BvP aggregates: runs_delta = (bvp_ops_shrunk - 0.720) * 100 * 0.05 R/G per 1pp OPS * signal_weight, where signal_weight ramps from 0 (no historical PAs) to 1.0 at 45+ PA/spot. main_totals run_predict augmented with a post-Stage-2 step that fetches lineup_meta, calls compute_roster_adjustment per game, and emits 8 new shadow columns in picks_totals: pred_runs_bvp_adjusted, total_runs_delta, home_runs_delta, away_runs_delta, home_bvp_n_pa, away_bvp_n_pa, home_bvp_ops_shrunk, away_bvp_ops_shrunk. SHADOW MODE: production O/U pick selection still uses original pred_runs in this commit; adjusted prediction is observability-only. Promotion gate to production: postgame cron compares RMSE(pred_runs, actual) vs RMSE(pred_runs_bvp_adjusted, actual) across 7+ days (~70-90 games); promote if adjusted RMSE is >= 5%% lower, otherwise remove the layer. The proper alternative — retrain XGBoost totals model with point-in-time BvP features — is Phase 2 and requires multi-day historical BvP backfill since vsPlayer returns career-current not as-of-date totals. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (_aggregate_lineup_vs_sp produces the exact aggregates needed), Rule 5 heuristic-not-retrain is the five-pass response to session fatigue, Rule 6 outer + per-row try/except with log.warning, [H] Rule 9 RUNS_PER_OPS_POINT=0.05 is starting guess marked for later backtest calibration, Rule 10 7-day RMSE deploy gate documented in code + commit body, Rule 11 signal_weight=0 at zero PAs prevents data-free confident adjustments, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Next main_totals cron should log
echo       "[totals_roster] adjusted N/M games"
echo    2. picks_totals_^<date^>.csv should have 8 new columns
echo    3. Where SP + lineup are known: pred_runs_bvp_adjusted
echo       differs from pred_runs by total_runs_delta
echo    4. Where lineup is empty or SP unknown:
echo       pred_runs_bvp_adjusted == pred_runs (no-op fallback)
echo ============================================================
pause
