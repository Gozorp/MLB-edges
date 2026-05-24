@echo off
REM ============================================================================
REM PUSH_SIDECAR_BARE_KEY.bat
REM Fix: O/U pill, Claude pill, parlay grade sidecar lookups in renderSlate
REM were failing because matchupKey from r.matchup carries the (G2 of 3)
REM / (G2) suffix added by _addSeriesSuffix / _dedupDoubleheaders, while
REM the sidecar maps are keyed by bare AWAY @ HOME.
REM
REM Concrete: today's slate had 15 rows, 5 totals entries in the map, but
REM ZERO O/U pills rendered.
REM
REM Tight-stage push (no curl, file already patched locally).
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging + committing ===
git add docs\index.html _patch_sidecar_bare_key.py PUSH_SIDECAR_BARE_KEY.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(dashboard): strip series/DH suffix before sidecar map lookups" -m "renderSlate was looking up O/U totals, Claude pill, and parlay grade with the SUFFIXED matchup string (e.g. 'PIT @ TOR (G3 of 3)'). All three sidecar maps are keyed by the BARE 'AWAY @ HOME' form, so every lookup returned undefined and the cells rendered blank for every row carrying a series-game annotation." -m "Observed 2026-05-24: 15 slate rows, 5 entries in __totalsByMatchup (bare keys), but 0 of 15 ou-pill elements actually rendered. Same blind spot was hiding Claude pills and parlay-reason text." -m "Fix: derive bareMatchupKey by stripping the trailing parenthetical, do bare-key-first lookup with the suffixed key as fallback (in case some future sidecar ever uses the full string). matchupKey itself stays unchanged for display."

if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
