@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard UI fix (Task #1): complement-prob + chart binding
echo  -----------------------------------------------------------
echo  Two bugs in docs/index.html's detail-panel renderer.
echo  Both fixed together since they share the same render path.
echo
echo  BUG 1: complement-prob display
echo    Three sites used r.full_prob (HOME team's prob) thinking
echo    it was the picked-team's prob.  For HOME picks they
echo    coincide; for AWAY picks the displayed number was the
echo    complement.  Most visible on LAD@SD PLATINUM A grade:
echo    detail card flashed "36.8%% LAD" while table and parlay
echo    report consistently showed 63.2%%.
echo
echo  BUG 2: search-result chart binding
echo    Chart.js win-prob chart didn't render when detail panel
echo    was surfaced via Ask-the-slate (worked via direct row
echo    click).  Same code path, different entry hook missing.
echo
echo  Fix scope:
echo    docs/index.html only (+34 / -5 lines)
echo      * _ensureWinProbChart    -^> r.pick_prob || r.p_model
echo      * _ltRenderBlockHTML     -^> r.pick_prob || r.p_model
echo      * formatNarrative        -^> r.pick_prob || r.p_model
echo      * formatGame             -^> tags card with row index
echo      * ask handler            -^> binds charts post-render
echo
echo  Backend pipeline unaffected.  Pure frontend fix.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edit to temp...
set TMPDIR=%TEMP%\mlb_edge_ui_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"               "%TMPDIR%\docs\index.html"               >nul
copy /Y "PUSH_DASHBOARD_UI_FIX.bat"     "%TMPDIR%\PUSH_DASHBOARD_UI_FIX.bat"     >nul

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
copy /Y "%TMPDIR%\docs\index.html"              "docs\index.html"              >nul
copy /Y "%TMPDIR%\PUSH_DASHBOARD_UI_FIX.bat"    "PUSH_DASHBOARD_UI_FIX.bat"    >nul

echo JS syntax-checking docs/index.html before commit...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks))"
if errorlevel 1 (echo JS EXTRACT FAILED & pause & exit /b 1)
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Staging + committing...
git add docs/index.html
git add PUSH_DASHBOARD_UI_FIX.bat
git status --short
git commit -m "Dashboard: fix detail-panel complement-prob display + search-result chart binding (Task #1). Two bugs in docs/index.html's detail-panel renderer, shipped together since they share the same render path. BUG 1: three sites bound r.full_prob (HOME team's prob) thinking it was the picked-team's prob — for HOME picks they coincide, for AWAY picks the displayed number was the complement. Most visible on LAD@SD 2026-05-20 PLATINUM A grade: detail card flashed 36.8%% LAD while table and parlay report consistently showed 63.2%%. High UX friction — a user clicking into an A-grade pick and seeing 36.8%% bolded will immediately distrust the model. Fix: r.pick_prob || r.p_model at three sites — _ensureWinProbChart (chart anchor), _ltRenderBlockHTML (live-tracker pre-game card), formatNarrative (search-result render). BUG 2: when detail panel was surfaced via Ask-the-slate search results, Chart.js never rendered (empty card with caption only). Direct row-click path called _ensureWinProbChart in toggleDetail; search-result path inserted narrative HTML but had no chart-binding hook. Fix: tag each search-result card with data-search-row-index referencing the row's index in slate.rows; after ask() sets innerHTML, walk those tagged cards, assign unique winprob-canvas-search-N ids to the canvas placeholders, and call _ensureWinProbChart with the synthetic search row index. Best-effort wrapped in try/except with console.warn on failure. Validated: node --check on extracted JS passes. Backend pipeline (predict, grade, parlay) unaffected — pure frontend fix."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Hard-refresh https://mlb-edges.saladin-alfaatih.workers.dev/
echo       (Ctrl+Shift+R to bust the worker cache)
echo    2. Load 2026-05-20 slate
echo    3. Click LAD @ SD row directly -^> detail card should
echo       show "63.2%% LAD" (was "36.8%% LAD")
echo    4. Type "LAD" in Ask the slate -^> search result should
echo       render WITH the dashed-line model probability chart
echo       (was rendering empty)
echo ============================================================
pause
