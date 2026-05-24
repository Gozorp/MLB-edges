@echo off
REM ============================================================================
REM PUSH_LIVE_CHART_REFRESH.bat
REM Fix: expanded-row win-prob chart never updates during a live game.
REM Two surgical changes:
REM   1. _ensureWinProbChart accepts forceRefresh + fetches actual curve
REM      for LIVE games (was final-only).
REM   2. Live-tracker tick calls _ensureWinProbChart(true) each cycle so the
REM      chart trails the in-game state.
REM
REM Safe-push pattern: stage helpers BEFORE rebase, --autostash, kill any
REM stale .git\index.lock up-front.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock (
  echo === Removing stale .git\index.lock ===
  del /F /Q .git\index.lock
)

echo === Refreshing docs/index.html from origin ===
curl -fsS "https://raw.githubusercontent.com/gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl failed & pause & exit /b 1 )

echo === Applying live-chart-refresh patch ===
python _patch_live_chart_refresh.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Verifying patch landed ===
findstr /C:"async function _ensureWinProbChart(rowIndex, r, result, forceRefresh)" docs\index.html >nul
if errorlevel 1 ( echo MISSING: forceRefresh param & pause & exit /b 1 )
findstr /C:"_liveStatus === \"live\"" docs\index.html >nul
if errorlevel 1 ( echo MISSING: live-status branch & pause & exit /b 1 )
findstr /C:"_ensureWinProbChart(rowIndex, r, result, true)" docs\index.html >nul
if errorlevel 1 ( echo MISSING: live-tick chart-refresh call & pause & exit /b 1 )

echo === Staging helpers + dashboard ===
git add docs\index.html _patch_live_chart_refresh.py PUSH_LIVE_CHART_REFRESH.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "fix(dashboard): win-prob chart now refreshes during live games" -m "Two bugs were keeping the expanded-row win-prob chart frozen on the static model curve during live play:" -m "1) _ensureWinProbChart short-circuited on el.dataset.rendered === '1' so the chart could only ever draw once per row expansion. Now accepts a forceRefresh 4th arg that bypasses the cache." -m "2) The function only fetched actualCurve (from /api/v1/game/{pk}/winProbability) when result.isFinal was true. Live games therefore never got an actual-trajectory line. Now fetches for LIVE state too (gated by _ltClassifyStatus)." -m "Wired the live-tracker tick to call _ensureWinProbChart(rowIndex, r, result, true) on every successful poll cycle (live or final). Existing 45s base interval / 5-error backoff unchanged."
if errorlevel 1 ( echo git commit failed & pause & exit /b 1 )

echo === Pull --rebase --autostash + push ===
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
