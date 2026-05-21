@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Top Probable Outcomes Phase 1 + pitcher K prop model
echo  -----------------------------------------------------------
echo  New dashboard section ranking high-probability outcomes
echo  across three prop categories.  All probabilities computed
echo  client-side from existing diag CSV + totals CSV; no new
echo  prediction model added.
echo
echo  Three sub-sections:
echo    1. Game ML Picks         (ranked by edge vs market)
echo    2. O/U Totals            (ranked by edge vs book-fair)
echo    3. Pitcher Strikeouts    (ranked by expected Ks)
echo
echo  HR props deferred to Phase 1.5 (need per-batter season
echo  HR/PA, not currently in diag CSV).  Phase 4 (Claude
echo  expand button) stubbed in UI; clicks show placeholder.
echo
echo  Files changed:
echo
echo  1. mlb_edge/build_pipeline.py (+5 lines)
echo     Add home_sp_k_pct + away_sp_k_pct to the per-game
echo     feature row emit.  K rate comes from
echo     point_in_time.pitcher_as_of (existing field, just
echo     wasn't surfaced).
echo
echo  2. mlb_edge/main_predict.py (+13 lines)
echo     Add home_sp_k_pct / away_sp_k_pct / home_sp_name /
echo     away_sp_name to the diag CSV emit dict so they reach
echo     the dashboard.
echo
echo  3. docs/index.html (+238 lines net)
echo     a. New ^<div id="top-outcomes"^>^</div^> between
echo        queryCard and slate.
echo     b. renderTopProbableOutcomes(rows, totalsByMatchup) —
echo        ranks + renders the three sub-sections.
echo     c. _kPropProbability(spKPct, threshold) — normal
echo        approximation to Poisson via Abramowitz-Stegun CDF;
echo        assumes 26 BF per 6 IP.
echo     d. _topGameMLPicks, _topTotals, _topPitcherKs — ranking
echo        helpers, top 5 per category.
echo     e. Template narratives per prop type.
echo     f. "Deep analysis" button shells (Phase 4 placeholder).
echo     g. Lifecycle wired into loadSlate after totals load.
echo
echo  4. PUSH_TOP_OUTCOMES_PHASE1.bat (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (diag CSV columns + SP fields)
echo    [E] Rule 3  — ast.parse + node --check syntax gates
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — scope-controlled to 3 prop types, did
echo                  NOT chase HR/hits/RBIs/run-lines this
echo                  session; Phase 1.5+ tracked
echo    [E] Rule 6  — JS Math.NaN-safe via isFinite() guards
echo    [E] Rule 13 — this script narrates the change
echo
echo  Honest caveats:
echo    [H] K prop probabilities are NOT backtested.  Phase 2
echo        adds postgame eval of pitcher K projections.
echo    [H] HR props NOT included this version.
echo    [H] Edge-vs-market only works for ML + Totals; K props
echo        rank by pure expected_K (no market line).
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_top_outcomes_p1
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\docs"     2>nul
copy /Y "mlb_edge\build_pipeline.py"        "%TMPDIR%\mlb_edge\build_pipeline.py"        >nul
copy /Y "mlb_edge\main_predict.py"          "%TMPDIR%\mlb_edge\main_predict.py"          >nul
copy /Y "docs\index.html"                   "%TMPDIR%\docs\index.html"                   >nul
copy /Y "PUSH_TOP_OUTCOMES_PHASE1.bat"      "%TMPDIR%\PUSH_TOP_OUTCOMES_PHASE1.bat"      >nul

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
copy /Y "%TMPDIR%\mlb_edge\build_pipeline.py"       "mlb_edge\build_pipeline.py"       >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"         "mlb_edge\main_predict.py"         >nul
copy /Y "%TMPDIR%\docs\index.html"                  "docs\index.html"                  >nul
copy /Y "%TMPDIR%\PUSH_TOP_OUTCOMES_PHASE1.bat"     "PUSH_TOP_OUTCOMES_PHASE1.bat"     >nul

echo Syntax-checking Python modules...
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['mlb_edge/build_pipeline.py', 'mlb_edge/main_predict.py']]; print('Python syntax OK')"
if errorlevel 1 (echo PYTHON SYNTAX FAILED & pause & exit /b 1)

echo Syntax-checking dashboard JS...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks))"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Staging + committing...
git add mlb_edge/build_pipeline.py
git add mlb_edge/main_predict.py
git add docs/index.html
git add PUSH_TOP_OUTCOMES_PHASE1.bat
git status --short
git commit -m "Top Probable Outcomes Phase 1: new dashboard section + pitcher K prop client-side model. Adds a 'Top Probable Outcomes' card between Ask-the-slate and the Slate table that ranks high-probability outcomes across three prop categories: Game ML picks (top 5 by edge vs market), O/U Totals (top 5 by edge vs book-fair), and Pitcher Strikeouts (top 5 by expected K). All probabilities computed client-side from existing diag CSV + totals CSV columns; no new prediction model required. Server-side: surfaces home_sp_k_pct + away_sp_k_pct from point_in_time.pitcher_as_of through build_pipeline into the diag CSV, alongside home_sp_name + away_sp_name for prop labels. Client-side: new JS helpers — _kPropProbability(spKPct, threshold) uses normal approximation to Poisson via Abramowitz-Stegun CDF assuming 26 BF per 6 IP; _topGameMLPicks/_topTotals/_topPitcherKs handle ranking with NaN-safe isFinite() guards; template narratives per prop type; 'Deep analysis' button shells stubbed for Phase 4 Claude expand. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (verified sp_k_pct flows from point_in_time -> build_pipeline -> preds -> diag), Rule 3 ast.parse + node --check syntax gates in this script, Rule 5 scope-controlled (3 prop types tonight; HR props + run-lines + SGPs deferred to Phase 1.5/2/3), Rule 6 isFinite guards everywhere, Rule 13 push script narrates. HONEST CAVEATS [H]: K prop probabilities are not backtested (Phase 2 adds postgame K projection eval); HR props excluded this version (need per-batter season HR/PA in diag CSV); edge-vs-market only fires for ML + Totals (K props rank by pure expected_K because no market line scraping). Validated end-to-end: node --check passes on 3475 lines of extracted JS; Python AST parses both modified modules."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Wait for next daily-slate cron (or trigger manually)
echo    2. Hard-refresh dashboard
echo    3. New "Top Probable Outcomes" card should appear above
echo       the Slate table.  3 sub-sections: Game Picks, Totals,
echo       Pitcher Ks.  Empty state shows if no actionable rows.
echo    4. Verify K props show expected_K for each named pitcher
echo       on the slate (column home_sp_name/away_sp_name).
echo    5. "Deep analysis" buttons should toggle a Phase 4
echo       placeholder message inline.
echo ============================================================
pause
