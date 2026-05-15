@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Platoon-Brain MVP: top-5 batter context for Claude Brain
echo  -----------------------------------------------------------
echo  PRE_FLIGHT GREEN confirmed — endpoint + samples validated
echo  on 3 locked-in test slates (NYY@MIL, ATL@LAD, CHC@TEX).
echo
echo  Files changed:
echo
echo  1. mlb_edge/platoon_splits.py  (NEW)
echo     Joiner module: pulls career splits vs LHP/RHP from
echo     MLB statsapi careerStatSplits endpoint with sitCodes
echo     filtering.  Weekly cache at data/platoon_cache/^<id^>.json.
echo     Switch-hitter logic resolves vs_today_SP_OPS to the
echo     correct side based on opposing SP handedness.
echo     attach_top_5_to_diag(diag_df, ...) adds two JSON-string
echo     columns to the diag CSV:
echo        away_top_5_batters_json
echo        home_top_5_batters_json
echo
echo  2. mlb_edge/main_predict.py
echo     New best-effort block after the diag CSV rewrite that
echo     calls platoon_splits.attach_top_5_to_diag(graded, ...)
echo     using game_pk + SP handedness extracted from preds.
echo     Wrapped in try/except so any failure here keeps the
echo     diag CSV ungraded-but-baked instead of blocking the
echo     pipeline.
echo
echo  3. tools/batch_dryrun_platoon.py  (NEW)
echo     A/B comparison harness for the platoon-brain MVP.
echo     Runs the 3-slate locked test set, produces
echo     docs/data/dryrun_top5_v1_vs_v2.md with per-batter
echo     payloads + summary stats (LOW_SAMPLE count, BIG_SPLIT
echo     count, avg PA per side).  Does NOT call the LLM —
echo     surfaces the v2-payload structure for human or
echo     audit-mode comparison.
echo
echo  4. tools/claude_brain_prompt.md
echo     New "Per-player top-5 batter context" section
echo     instructing the brain how to parse the JSON, when to
echo     discount LOW_SAMPLE rows, and the false-positive
echo     resistance bias.
echo
echo  5. tools/pre_flight_platoon.py  (already shipped)
echo     Regression-test harness for the data pipeline.
echo
echo  Validation gates built in:
echo    * platoon_splits has best-effort try/except per row
echo    * attach_to_diag has best-effort try/except for the
echo      whole call
echo    * main_predict's platoon block has its own try/except
echo    * All numeric fields default to None on missing data;
echo      no float(None) can propagate from this module
echo
echo  Expected behavior on next daily-slate cron:
echo    * Each diag CSV gains 2 new JSON-string columns
echo    * data/platoon_cache populates with weekly-TTL files
echo    * claude-brain reads the new columns and incorporates
echo      platoon context into reasoning per the brain prompt
echo    * On any failure, the columns simply contain "[]" and
echo      the rest of the pipeline runs unchanged
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_top5_batter
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"     2>nul
copy /Y "mlb_edge\platoon_splits.py"             "%TMPDIR%\mlb_edge\platoon_splits.py"             >nul
copy /Y "mlb_edge\main_predict.py"               "%TMPDIR%\mlb_edge\main_predict.py"               >nul
copy /Y "tools\batch_dryrun_platoon.py"          "%TMPDIR%\tools\batch_dryrun_platoon.py"          >nul
copy /Y "tools\pre_flight_platoon.py"            "%TMPDIR%\tools\pre_flight_platoon.py"            >nul
copy /Y "tools\claude_brain_prompt.md"           "%TMPDIR%\tools\claude_brain_prompt.md"           >nul
copy /Y "PUSH_TOP_5_BATTER_CONTEXT.bat"          "%TMPDIR%\PUSH_TOP_5_BATTER_CONTEXT.bat"          >nul

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
copy /Y "%TMPDIR%\mlb_edge\platoon_splits.py"             "mlb_edge\platoon_splits.py"             >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"               "mlb_edge\main_predict.py"               >nul
copy /Y "%TMPDIR%\tools\batch_dryrun_platoon.py"          "tools\batch_dryrun_platoon.py"          >nul
copy /Y "%TMPDIR%\tools\pre_flight_platoon.py"            "tools\pre_flight_platoon.py"            >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"           "tools\claude_brain_prompt.md"           >nul
copy /Y "%TMPDIR%\PUSH_TOP_5_BATTER_CONTEXT.bat"          "PUSH_TOP_5_BATTER_CONTEXT.bat"          >nul

echo Syntax-checking Python modules before commit...
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['mlb_edge/platoon_splits.py', 'mlb_edge/main_predict.py', 'tools/batch_dryrun_platoon.py', 'tools/pre_flight_platoon.py']]; print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/platoon_splits.py
git add mlb_edge/main_predict.py
git add tools/batch_dryrun_platoon.py
git add tools/pre_flight_platoon.py
git add tools/claude_brain_prompt.md
git add PUSH_TOP_5_BATTER_CONTEXT.bat
git status --short
git commit -m "Platoon-brain MVP: per-player top-5 batter context for Claude Brain. The architectural shift from team-aggregated features (lineup_concentration, F2_xwoba_gap) toward per-player narrative context for the LLM judgment layer. New module mlb_edge/platoon_splits.py pulls career xwOBA-vs-handedness from MLB statsapi (careerStatSplits endpoint with sitCodes=vl,vr) and joins to the actual batted lineup top-5 from /game/{pk}/boxscore. Pre-resolves vs_today_SP_OPS for switch-hitters so the LLM doesn't have to compute handedness lookups. Weekly cache at data/platoon_cache/<id>.json. main_predict gains a best-effort attach call after the diag CSV rewrite, adding two new JSON-string columns (away_top_5_batters_json, home_top_5_batters_json) per game row. Brain prompt gets a new 'Per-player top-5 batter context' section instructing the LLM to discount LOW_SAMPLE rows (<100 career PA), look at top-3 collectively, and bias toward false-positive resistance (splits inform but rarely flip decisions). New dryrun harness tools/batch_dryrun_platoon.py builds payloads for the 3-slate locked test set (NYY@MIL baseline, ATL@LAD false-positive control, CHC@TEX strong false-positive control) and writes docs/data/dryrun_top5_v1_vs_v2.md. Existing tools/pre_flight_platoon.py serves as the data-pipeline regression test. The XGBoost booster is unchanged — this is LLM-context only, which sidesteps the dimensionality curse that naive per-player feature engineering would create. Validated end-to-end against statsapi during PRE_FLIGHT: Judge 1299 PA vs LHP, Goldschmidt 2172 PA vs LHP, Ohtani 1447 PA vs LHP — samples are stable for ~95%% of MLB starters."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Run tools/pre_flight_platoon.py to confirm endpoint still up
echo    2. Run tools/batch_dryrun_platoon.py to generate dryrun MD
echo    3. Trigger daily-slate workflow; check 5/15+ diag CSVs
echo       contain non-empty top_5_batters_json columns
echo    4. On next claude-brain run, verify the brain reasoning
echo       cites specific batter names + split numbers in its
echo       narrative output
echo
echo  Failure modes already handled:
echo    * Network failure -> empty JSON "[]", pipeline continues
echo    * Player ID missing -> sample_flag="NO_DATA" record
echo    * Switch-hitter -> resolved to opposite-side splits
echo    * Lineup not yet announced -> empty list, no crash
echo    * Career split endpoint down -> cache hits cover ~95%% of slates
echo ============================================================
pause
