@echo off
REM ============================================================================
REM PUSH_LINEUP_EDGE.bat
REM New "Lineup edge" card in the per-game expander:
REM   - Composite winner banner (xwOBA + wRC+ from player_aware_signal)
REM   - Per-batter K-vulnerability list vs opposing SP (Log5 odds ratio)
REM Sits above the projected lineup cards. Spans full grid width.
REM
REM Safe-push pattern: stage BEFORE rebase, --autostash, helper files staged
REM up-front to avoid the index.lock dance.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock (
  echo === Removing stale .git\index.lock ===
  del /F /Q .git\index.lock
)

echo === Refreshing docs/index.html from origin ===
curl -fsS "https://raw.githubusercontent.com/gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl failed & pause & exit /b 1 )

echo === Applying Lineup Edge patch ===
python _patch_lineup_edge.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Verifying patch landed ===
findstr /C:"function renderLineupEdge" docs\index.html >nul
if errorlevel 1 ( echo MISSING: renderLineupEdge function & pause & exit /b 1 )
findstr /C:"function _batterKProb" docs\index.html >nul
if errorlevel 1 ( echo MISSING: _batterKProb helper & pause & exit /b 1 )
findstr /C:"so: parseInt(st.strikeOuts" docs\index.html >nul
if errorlevel 1 ( echo MISSING: SO field in roster fetch & pause & exit /b 1 )
findstr /C:"${renderLineupEdge(preview)}" docs\index.html >nul
if errorlevel 1 ( echo MISSING: renderLineupEdge call site & pause & exit /b 1 )
findstr /C:"Lineup edge" docs\index.html >nul
if errorlevel 1 ( echo MISSING: Lineup edge heading & pause & exit /b 1 )

echo === Staging helpers + dashboard ===
git add docs\index.html _patch_lineup_edge.py PUSH_LINEUP_EDGE.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "feat(dashboard): Lineup edge card in per-game expander" -m "New full-width preview-card above the projected-lineup cards:" -m "1) Composite winner banner sourcing lineup_wrcplus_h/a + lineup_xwoba_h/a from the totals row's player_aware_signal JSON. Computes a unified score (wrc - 100) + (xw - 0.310)*1000 per side and labels the gap as marginal/edge/clear." -m "2) Per-batter K-vulnerability lists, one per lineup, sorted high-to-low, color-coded (red >=32%, yellow >=25%, muted below). Uses Log5: p_K = clamp((batter_SO/PA) * (k9/38) / 0.225). Headline contact percentage per side ('contact 76% vs Skubal 11.3 K/9')." -m "Also widens _fetchTeamRoster to capture batter strikeOuts and propagates the so field through idToBat + _enrich so lineup objects carry it. Honors Quant Terminal identity (monospace, compact card, no walls of prose)."
if errorlevel 1 ( echo git commit failed & pause & exit /b 1 )

echo === Pull --rebase --autostash + push ===
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
