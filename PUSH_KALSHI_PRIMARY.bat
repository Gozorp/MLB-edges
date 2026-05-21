@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Kalshi PRIMARY for moneyline odds (Odds API cancelled)
echo  -----------------------------------------------------------
echo  User directive (2026-05-21): the Odds API subscription is
echo  cancelled.  Promote Kalshi from fallback to primary for
echo  moneyline ^(h2h^) markets.  Surface the totals/F5 dead-state
echo  legibly so it is obvious when those pipelines lose feed.
echo
echo  ARCHITECTURAL HONESTY:
echo    Kalshi covers MOENYLINE ONLY.  Its KXMLBGAME series is
echo    binary game-winner contracts.  Kalshi does NOT carry:
echo      - MLB totals ^(O/U^) markets
echo      - MLB first-5-innings ^(F5^) markets
echo    So a literal "use Kalshi as a replacement" only works
echo    for the moneyline pipeline.  Totals and F5 cannot be
echo    fully replaced without building a new fallback source,
echo    which is a multi-day build that conflicts with the
echo    project_sfo_departure_freeze memory rule.
echo
echo  TONIGHT'S SHIP (smallest safe):
echo
echo  1. mlb_edge/main_predict.py
echo     - Reorder odds chain: Kalshi PRIMARY, OddsAPI fallback,
echo       ESPN last-resort.  Was: OddsAPI primary, Kalshi+ESPN
echo       fallback.
echo     - New _try_oddsapi^(^) wraps the previous primary-path
echo       logic into the same shape as other source fetchers.
echo     - Backfill chain simplified to ESPN only when Kalshi
echo       primary succeeds ^(OddsAPI backfill helper deferred^).
echo     - odds_status values kept stable for downstream
echo       consumers: 'fetched' / 'kalshi_empty+...' / 'all_empty'.
echo
echo  2. mlb_edge/live_totals.py
echo     - File-header note explaining the Kalshi scope limit
echo       and the planned graceful-degrade follow-up.
echo     - ODDS_API_KEY_MISSING error now LOUD with full
echo       context ^("totals pipeline cannot fetch ... Kalshi
echo       does NOT carry totals ... main_totals will skip
echo       writing today's CSV"^).  Easy to grep in logs.
echo
echo  3. mlb_edge/live_f5.py
echo     - Same pattern as live_totals: header note + loud
echo       ODDS_API_KEY_MISSING log line.
echo
echo  4. PUSH_KALSHI_PRIMARY.bat ^(this file^)
echo
echo  DEFERRED TO FOLLOW-UP COMMIT (scope-controlled):
echo    - main_totals.py and main_f5.py structural change:
echo      replace `if raw.empty: return` early-exit with a
echo      graceful path that emits pred_runs/pred_f5 in the
echo      picks_totals/picks_f5 CSV with blank fair_prob /
echo      edge_pp / EV columns.  Estimated 50-100 lines of
echo      careful refactor with downstream-consumer testing.
echo      Tonight: file headers document the plan so a future
echo      session knows where to pick up.
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: 7 files searched, current chain
echo                   structure mapped, Kalshi wrapper public
echo                   API confirmed, scope limits identified
echo    [E] Rule 3  -- ast.parse + py_compile gates below
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- did NOT build new totals/F5 fallback
echo                   sources tonight ^(SFO freeze^); did NOT
echo                   refactor main_totals/main_f5 structurally
echo    [E] Rule 6  -- best-effort try/except in every source
echo                   call; failed source returns None gracefully
echo    [E] Rule 11 -- backwards-compatible odds_status values
echo                   ^(downstream parlay grader / dashboard
echo                   unchanged^)
echo    [E] Rule 12 -- architectural decision documented in
echo                   main_predict.py header comment block
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_kalshi_primary
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\main_predict.py"    "%TMPDIR%\mlb_edge\main_predict.py"    >nul
copy /Y "mlb_edge\live_totals.py"     "%TMPDIR%\mlb_edge\live_totals.py"     >nul
copy /Y "mlb_edge\live_f5.py"         "%TMPDIR%\mlb_edge\live_f5.py"         >nul
copy /Y "PUSH_KALSHI_PRIMARY.bat"     "%TMPDIR%\PUSH_KALSHI_PRIMARY.bat"     >nul

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
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"    "mlb_edge\main_predict.py"    >nul
copy /Y "%TMPDIR%\mlb_edge\live_totals.py"     "mlb_edge\live_totals.py"     >nul
copy /Y "%TMPDIR%\mlb_edge\live_f5.py"         "mlb_edge\live_f5.py"         >nul
copy /Y "%TMPDIR%\PUSH_KALSHI_PRIMARY.bat"     "PUSH_KALSHI_PRIMARY.bat"     >nul

echo Python syntax-checking main_predict.py...
python -c "import ast; ast.parse(open('mlb_edge/main_predict.py', encoding='utf-8').read()); print('main_predict.py: ast.parse OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Python syntax-checking live_totals.py...
python -c "import ast; ast.parse(open('mlb_edge/live_totals.py', encoding='utf-8').read()); print('live_totals.py: ast.parse OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Python syntax-checking live_f5.py...
python -c "import ast; ast.parse(open('mlb_edge/live_f5.py', encoding='utf-8').read()); print('live_f5.py: ast.parse OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo py_compile gate...
python -c "import py_compile; py_compile.compile('mlb_edge/main_predict.py', doraise=True); py_compile.compile('mlb_edge/live_totals.py', doraise=True); py_compile.compile('mlb_edge/live_f5.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo Null-byte audit on shipped files...
python -c "[print(f'{p}: {open(p,chr(34)+chr(114)+chr(98)+chr(34)).read().count(bytes([0]))} null bytes') for p in ['mlb_edge/main_predict.py','mlb_edge/live_totals.py','mlb_edge/live_f5.py']]"

echo Staging + committing...
git add mlb_edge/main_predict.py
git add mlb_edge/live_totals.py
git add mlb_edge/live_f5.py
git add PUSH_KALSHI_PRIMARY.bat
git status --short
git commit -m "Odds chain: Kalshi PRIMARY for moneyline (Odds API subscription cancelled). User directive 2026-05-21. Promotes Kalshi (CFTC-regulated US prediction market, no-vig binary game-winner contracts on KXMLBGAME series) from fallback to primary source for moneyline (h2h) odds in main_predict.py. Odds API kept as fallback in case the subscription is reactivated; ESPN scraping remains last resort. New _try_oddsapi() helper wraps the previous primary-path logic into the same best-effort shape as the other source fetchers so the chain stays uniform. odds_status values preserved for downstream consumers (parlay grader, dashboard, postgame cron) - status now reads 'fetched' (Kalshi succeeded) or 'kalshi_empty+oddsapi_fallback' or 'kalshi_empty+espn_fallback' or 'all_empty'. ARCHITECTURAL HONESTY: Kalshi only carries moneyline contracts. Its KXMLBGAME series is binary game-winner only and does NOT include MLB totals (O/U) or first-5-innings (F5) markets. live_totals.py and live_f5.py now carry file-header notes explaining the scope limit + plan, and emit loud ODDS_API_KEY_MISSING log lines when the Odds API key is unset so the dead-state is legible in cron logs. main_totals.py and main_f5.py structural graceful-degrade (emit pred_runs/pred_f5 in the CSV with blank fair_prob/edge_pp/EV columns instead of skipping the CSV write entirely) is DEFERRED to a follow-up commit per Rule 5 - estimated 50-100 lines of careful refactor with downstream-consumer testing. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (7 files searched, chain structure mapped, Kalshi public API confirmed), Rule 3 ast.parse + py_compile gates, Rule 4 safe-push, Rule 5 did NOT build new totals/F5 fallback sources (SFO freeze), Rule 6 best-effort try/except per source, Rule 11 backwards-compatible odds_status values, Rule 12 architectural decision documented in code header, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Next main_predict cron will try Kalshi FIRST.  Expect
echo       log lines: "[odds] Kalshi primary populated N rows
echo       for M games" instead of "Odds API ... remaining /
echo       used" as the first odds-source log.
echo    2. Today's slate diag should show odds_status='fetched'
echo       ^(Kalshi succeeded^) for most games.
echo    3. If Kalshi returns empty for a game, OddsAPI fallback
echo       fires automatically ^(only if ODDS_API_KEY still set^);
echo       otherwise ESPN scraping fires last.
echo    4. main_totals + main_f5 crons will emit clear
echo       ODDS_API_KEY_MISSING errors when the key is unset;
echo       picks_totals + picks_f5 CSVs will NOT be written for
echo       those slates until the deferred graceful-degrade
echo       follow-up ships.
echo    5. Dashboard's moneyline section keeps working from
echo       Kalshi data; Totals + F5 sections will be empty
echo       on dead-key days.
echo ============================================================
pause
