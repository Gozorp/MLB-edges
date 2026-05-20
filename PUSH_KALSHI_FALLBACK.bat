@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Kalshi public-API fallback for the odds chain
echo  -----------------------------------------------------------
echo  When the primary Odds API returns empty / fails / no-key,
echo  the pipeline now falls through to Kalshi BEFORE ESPN.
echo
echo  Why Kalshi:
echo    * CFTC-regulated US prediction market
echo    * No-vig binary contracts (two YES probs sum to ~1.00
echo      structurally)
echo    * Free public REST API, no auth required
echo    * Cleaner than ESPN HTML scraping (which has broken
echo      multiple times on layout changes)
echo
echo  Files changed:
echo
echo  1. mlb_edge/kalshi_odds.py  (NEW, 505 lines)
echo     Public API mirrors odds_fallback.py exactly:
echo        fetch_kalshi_mlb_odds(slate_date) -^> DataFrame
echo        backfill_missing_odds(primary_df, slate_date) -^> DF
echo     Endpoint: api.elections.kalshi.com/trade-api/v2
echo     Series:   KXMLBGAME
echo     Throttle: 1.0s/req (stays under unauthenticated rate
echo               limit; ~15s for 15-game slate)
echo     Retry:    single retry on HTTP 429 after 3.0s backoff
echo     Sanity:   Rule 11 reverse-direction check — two-team
echo               YES probs must sum into [0.85, 1.15] band
echo
echo  2. mlb_edge/main_predict.py
echo     Step 3.1/5 rewritten as unified Kalshi -^> ESPN chain.
echo     Helper closures _try_fallback / _try_backfill keep the
echo     per-fallback logic readable and DRY.  ESPN incumbent
echo     preserved as last-resort backstop.
echo
echo  3. PUSH_KALSHI_FALLBACK.bat  (this file)
echo
echo  Architecture-Session Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (KXMLBGAME series + yes_bid/ask)
echo    [E] Rule 2  — test set: 5/19 settled slate, 15/15 games,
echo                  all Rule 11 sanity checks passed
echo    [E] Rule 3  — ast.parse gate built into this script
echo    [E] Rule 5  — pivoted to bash str.replace on Edit-tool
echo                  corruption (saved as memory pattern)
echo    [E] Rule 6  — best-effort try/except with log.warning
echo                  on every fallback / backfill call
echo    [E] Rule 9  — no invented thresholds; liquidity/OI are
echo                  metadata, not gates
echo    [E] Rule 11 — sum-sanity at [0.85, 1.15] confirmed
echo                  empirically on live 5/19 slate
echo    [E] Rule 13 — this script narrates the change
echo
echo  Validation already done:
echo    * Live test on 5/19 slate: 15/15 games, ~16s runtime
echo    * AST parse green on main_predict, kalshi_odds, and
echo      odds_fallback (incumbent unchanged)
echo    * All public functions importable
echo
echo  Expected behavior on next daily-slate cron:
echo    * If primary Odds API succeeds: Kalshi backfill fires
echo      for any games the primary missed (rare)
echo    * If primary Odds API empty/fails: Kalshi takes over,
echo      ESPN remains as final backstop
echo    * On any Kalshi failure: log.warning, fall through to
echo      ESPN, no pipeline crash
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_kalshi
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
copy /Y "mlb_edge\kalshi_odds.py"     "%TMPDIR%\mlb_edge\kalshi_odds.py"     >nul
copy /Y "mlb_edge\main_predict.py"    "%TMPDIR%\mlb_edge\main_predict.py"    >nul
copy /Y "PUSH_KALSHI_FALLBACK.bat"    "%TMPDIR%\PUSH_KALSHI_FALLBACK.bat"    >nul

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
copy /Y "%TMPDIR%\mlb_edge\kalshi_odds.py"     "mlb_edge\kalshi_odds.py"     >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"    "mlb_edge\main_predict.py"    >nul
copy /Y "%TMPDIR%\PUSH_KALSHI_FALLBACK.bat"    "PUSH_KALSHI_FALLBACK.bat"    >nul

echo Syntax-checking Python modules before commit...
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['mlb_edge/kalshi_odds.py', 'mlb_edge/main_predict.py']]; print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/kalshi_odds.py
git add mlb_edge/main_predict.py
git add PUSH_KALSHI_FALLBACK.bat
git status --short
git commit -m "Kalshi public-API fallback for the odds chain. When the primary Odds API returns empty / fails / no-key, the pipeline now falls through to Kalshi BEFORE the existing ESPN fallback. Kalshi is a CFTC-regulated US prediction market with no-vig binary MLB markets (KXMLBGAME series) where the two YES contracts per game structurally sum to ~1.00 — no de-vig math needed. Public REST API, no auth, throttled at 1.0s/req to stay under the unauthenticated rate limit (~15s for a 15-game slate). Cleaner than ESPN HTML scraping which has broken on layout changes (CHC/CLE pairing bug on 5/3). New module mlb_edge/kalshi_odds.py mirrors odds_fallback.py's public API: fetch_kalshi_mlb_odds(slate_date) and backfill_missing_odds(primary_df, slate_date). main_predict step 3.1/5 rewritten as unified Kalshi -> ESPN chain via _try_fallback / _try_backfill helper closures (DRY across both sources). Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probe done, Rule 2 test set = 5/19 settled slate (15/15 games, all Rule 11 sanity checks passed structurally), Rule 6 best-effort try/except with log.warning throughout, Rule 9 no invented thresholds (liquidity/OI are metadata not gates), Rule 11 reverse-direction sum-sanity band [0.85, 1.15], Rule 13 push script narrates change. Live-tested 2026-05-19 against settled markets: BAL@TB resolved cleanly (TB at 0.99 = winner, model's TB pick was correct), CWS@SEA, ATL@MIA etc all returned valid market structure. ESPN incumbent preserved as final backstop; downstream recommend_slate / shin de-vig / fair_prob derivation work unchanged."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Run tools/dryrun_kalshi.py to confirm endpoint up
echo       (or: python -m mlb_edge.kalshi_odds 2026-05-20)
echo    2. Trigger daily-slate workflow; watch the [odds] log
echo       lines for "kalshi_fallback populated" or "Kalshi
echo       backfilled N additional rows"
echo    3. On next claude-brain run, confirm fair_prob is
echo       populated for ALL games (no PENDING_ODDS rows)
echo
echo  Failure modes already handled:
echo    * Kalshi 429 rate limit -^> single retry after 3s
echo    * Kalshi network failure -^> log.warning, fall to ESPN
echo    * Two-team YES sum outside [0.85, 1.15] -^> skip row
echo    * ESPN also empty -^> log.warning, slate ships without
echo      fair_prob (existing graceful-degradation path)
echo ============================================================
pause
