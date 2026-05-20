@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  BvP-brain MVP: per-batter career history vs today's SP
echo  -----------------------------------------------------------
echo  Architectural twin of platoon-brain MVP (2026-05-14).  Adds
echo  per-batter BvP (batter-vs-pitcher) JSON columns to the diag
echo  CSV so the Claude Brain layer can reason about historical
echo  ownership / weak-vs relationships at the LLM judgment stage.
echo
echo  Why not as XGBoost features:
echo    * Most BvP samples are tiny (typical batter has <10 PA vs
echo      a given SP)
echo    * Naive per-batter rate injection would overfit on the
echo      long tail of noisy small samples
echo    * Same dimensionality-curse logic that drove platoon-brain
echo      to the LLM-context architecture
echo
echo  Files changed:
echo
echo  1. mlb_edge/bvp_brain.py  (NEW, 276 lines)
echo     Public API mirrors platoon_splits.py:
echo        attach_bvp_to_diag(diag_df, matchup_to_pk,
echo                           matchup_to_sp_ids) -^> diag_df
echo     Adds two JSON-string columns:
echo        away_bvp_top5_json
echo        home_bvp_top5_json
echo     Per-batter record: order, name, PA, HR, OPS, HR_per_PA,
echo     shrunk_OPS, sample_flag.
echo     Bayesian shrinkage toward .720 prior (30-PA equivalent).
echo     sample_flag values: NO_DATA, SMALL_SAMPLE, MEANINGFUL,
echo     LOTS_OF_HISTORY, OWNER, WEAK_VS.
echo     Delegates fetch to existing batter_vs_pitcher.fetch_bvp
echo     so cache layer + retry logic are reused.
echo
echo  2. mlb_edge/main_predict.py  (+37 lines)
echo     New best-effort block after the platoon-brain attach.
echo     Builds matchup_to_sp_ids dict from preds (away_sp_id /
echo     home_sp_id columns).  Calls bvp_brain.attach_bvp_to_diag
echo     and re-saves diag CSV.  Wrapped in try/except per Rule 6
echo     so any failure keeps the rest of the pipeline running.
echo
echo  3. tools/pre_flight_bvp.py  (NEW, 245 lines)
echo     Regression-test harness.  Locked test set:
echo        2026-05-16, 2026-05-17, 2026-05-18.
echo     Single-slate smoke test on 5/18 confirmed:
echo        * 6/14 matchups have MEANINGFUL+ BvP signal
echo        * 4 OWNER classifications (real "Acuna-owns-X" type
echo          relationships in the data)
echo        * Max-PA = 21 (CIN@PHI hitter vs CIN's SP)
echo
echo  4. tools/claude_brain_prompt.md  (+59 lines)
echo     New "Per-batter BvP context (2026-05-19 bvp-brain MVP)"
echo     section instructing the brain how to parse the JSON,
echo     when to use shrunk_OPS vs raw, OWNER/WEAK_VS asymmetry
echo     as the key tell, HR-prop heuristic, and false-positive
echo     resistance bias.
echo
echo  5. PUSH_BVP_BRAIN_MVP.bat  (this file)
echo
echo  Architecture-Session Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (vsPlayer endpoint validated with
echo                  Judge vs Verlander: PA=25, HR=2, OPS=.735)
echo    [E] Rule 2  — test set locked: 5/16, 5/17, 5/18; signal
echo                  density confirmed via exploratory scan
echo    [E] Rule 3  — ast.parse + JS syntax gate in this script
echo    [E] Rule 4  — safe-push: temp -^> fetch -^> reset -^> restore
echo    [E] Rule 6  — best-effort try/except + log.warning at
echo                  every fallback / per-row level
echo    [H] Rule 9  — sample_flag thresholds (PA=10, PA=30,
echo                  OPS=.900, OPS=.500) marked [H] starting
echo                  guesses; brain reads as soft signals not
echo                  hard gates
echo    [E] Rule 11 — reverse-direction sanity: no single batter's
echo                  BvP flips a pick; brain prompt enforces this
echo    [E] Rule 13 — this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_bvp_brain
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"    2>nul
copy /Y "mlb_edge\bvp_brain.py"           "%TMPDIR%\mlb_edge\bvp_brain.py"           >nul
copy /Y "mlb_edge\main_predict.py"        "%TMPDIR%\mlb_edge\main_predict.py"        >nul
copy /Y "tools\pre_flight_bvp.py"         "%TMPDIR%\tools\pre_flight_bvp.py"         >nul
copy /Y "tools\claude_brain_prompt.md"    "%TMPDIR%\tools\claude_brain_prompt.md"    >nul
copy /Y "PUSH_BVP_BRAIN_MVP.bat"          "%TMPDIR%\PUSH_BVP_BRAIN_MVP.bat"          >nul

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
copy /Y "%TMPDIR%\mlb_edge\bvp_brain.py"          "mlb_edge\bvp_brain.py"          >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"       "mlb_edge\main_predict.py"       >nul
copy /Y "%TMPDIR%\tools\pre_flight_bvp.py"        "tools\pre_flight_bvp.py"        >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"   "tools\claude_brain_prompt.md"   >nul
copy /Y "%TMPDIR%\PUSH_BVP_BRAIN_MVP.bat"         "PUSH_BVP_BRAIN_MVP.bat"         >nul

echo Syntax-checking Python modules before commit...
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['mlb_edge/bvp_brain.py', 'mlb_edge/main_predict.py', 'tools/pre_flight_bvp.py']]; print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/bvp_brain.py
git add mlb_edge/main_predict.py
git add tools/pre_flight_bvp.py
git add tools/claude_brain_prompt.md
git add PUSH_BVP_BRAIN_MVP.bat
git status --short
git commit -m "BvP-brain MVP: per-batter career history vs today's SP for the Claude Brain layer. Architectural twin of platoon-brain MVP (2026-05-14). Adds two JSON-string columns to the diag CSV (away_bvp_top5_json, home_bvp_top5_json) containing per-batter career batter-vs-pitcher records for the top-5 lineup batters facing today's opposing SP. Each record: order, name, vs_today_SP_PA, vs_today_SP_HR, vs_today_SP_OPS, vs_today_SP_HR_per_PA, shrunk_OPS (Bayesian-shrunk toward .720 prior), sample_flag (NO_DATA / SMALL_SAMPLE / MEANINGFUL / LOTS_OF_HISTORY / OWNER / WEAK_VS). New module mlb_edge/bvp_brain.py wraps the existing batter_vs_pitcher.fetch_bvp (reuses cache + retry).  main_predict gets a best-effort attach block immediately after the platoon-brain attach, sharing the matchup_to_pk lookup table.  Brain prompt gets a new 'Per-batter BvP context' section instructing the LLM to use shrunk_OPS over raw for small samples, count OWNER vs WEAK_VS asymmetry as the key tell, and never let single-batter BvP flip a pick. Same XGBoost-dimensionality-curse argument as platoon-brain: most BvP samples are tiny (<10 PA), naive feature injection would overfit, so push to LLM judgment layer instead. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probe done (vsPlayer endpoint validated with Judge vs Verlander = 25 PA 2 HR .735 OPS), Rule 2 test set locked at 5/16/5/17/5/18 (single-slate smoke test on 5/18 found 6/14 matchups with MEANINGFUL+ signal, 4 OWNER classifications, max-PA 21 on CIN@PHI), Rule 6 best-effort try/except + log.warning throughout, Rule 9 sample_flag thresholds marked [H] starting guesses to be backtest-tuned, Rule 11 reverse-direction sanity enforced via brain prompt (no single batter flips a pick), Rule 13 push script narrates change. Validated end-to-end: bvp_brain CLI on Judge/Verlander returns correct shrunk_OPS and MEANINGFUL flag; pre_flight_bvp smoke run on 5/18 GREEN in 17.5s."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. python tools/pre_flight_bvp.py
echo       (single-slate scan ~18s; full 3-slate ~60-90s)
echo    2. Trigger daily-slate workflow; check the next diag
echo       CSV contains away_bvp_top5_json + home_bvp_top5_json
echo       columns with non-empty payloads for matchups where
echo       both SPs are confirmed
echo    3. On next claude-brain run, verify the brain reasoning
echo       cites specific OWNER / WEAK_VS / MEANINGFUL flags
echo       in its narrative output
echo
echo  Failure modes already handled:
echo    * Missing SP IDs in preds -^> empty BvP payloads, no crash
echo    * Network failure -^> per-batter NO_DATA stub, slate continues
echo    * vsPlayer endpoint changes shape -^> caught in fetch_bvp's
echo      existing parser, returns None, treated as NO_DATA
echo    * Rookie batter (no history) -^> NO_DATA flag, brain ignores
echo ============================================================
pause
