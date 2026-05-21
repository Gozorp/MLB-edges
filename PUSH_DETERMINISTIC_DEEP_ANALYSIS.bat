@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Deep Analysis: deterministic client-side narrative
echo  -----------------------------------------------------------
echo  User request: "Make this a functional feature."
echo
echo  Prior behavior: Deep Analysis button on Top Probable Outcomes
echo  showed a muted fallback message ("Claude deep-analysis is
echo  currently disabled...") because /api/claude/health returns
echo  enabled:false right now (Worker disabled; Odds API key
echo  deactivated upstream is the suspected root cause).
echo
echo  New behavior: clicking Deep analysis ALWAYS renders a full
echo  multi-paragraph deterministic narrative synthesized client-
echo  side from the payload row's own data — no Worker required.
echo
echo  Generators ^(by prop type^):
echo    ml -- pick rationale, Stage 1 + Stage 2 prob breakdown,
echo          active signals from reasons[], PQI delta, bullpen
echo          xwOBA gap, platoon lineup tilt, BvP context
echo          ^(owners + weak-vs^), cap-rule firings, stress flags,
echo          verdict
echo    ou -- total runs projection vs line, SP K-rate context
echo          both sides, late-leverage bullpen gap, lineup
echo          concentration, umpire K/BB delta, BvP run-environ
echo          signals, edge interpretation, verdict
echo    k  -- K rate vs league avg, threshold P^(K^>=N^), umpire
echo          K delta, weather effects, bottom-line confidence
echo
echo  Enhancement layer ^(non-blocking^): if the Cloudflare Worker
echo  IS enabled, /api/claude/ask is fired in the background and
echo  Claude's response is APPENDED beneath the deterministic
echo  block.  If the Worker is offline or errors, the user still
echo  gets the full deep-dive — no misleading red error.
echo
echo  Architectural shift: Deep Analysis is no longer gated on
echo  the Worker.  It's a real feature backed by the model's own
echo  outputs (signals, stage probs, caps, PQI, BvP_*, hl_*,
echo  ump_* columns already in window.__slate.rows).
echo
echo  Files changed:
echo
echo  1. docs/index.html
echo     _propDeepAnalysisOnClick rewritten to always call
echo     _propRenderDeepDeterministic^(propType, payload^) first,
echo     then fire /api/claude/ask in the background as an
echo     enhancement.  ^(Deterministic narrative generators
echo     _deepNarrativeML/_deepNarrativeOU/_deepNarrativeK and
echo     supporting helpers _safeJsonParse, _capRulesFromReasons,
echo     _parseSignalsList, _fmtPct/_fmtPp/_fmtNum,
echo     _summarizeLineupJson/_summarizeBvpJson,
echo     _propRenderDeepDeterministic were added in the prior
echo     commit batch.^)
echo
echo  2. PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat ^(this file^)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: payload rows already carry the
echo                   columns the narrative reads
echo                   ^(reasons[], stage1_prob, stage2_prob,
echo                   pqi_delta, hl_bullpen_xwoba_gap,
echo                   platoon_lineup_json, away_bvp_top5_json,
echo                   home_bvp_top5_json, ump_k_pct_delta...^)
echo    [E] Rule 3  -- node --check JS gate below
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- single targeted fix; deterministic
echo                   narrative replaces fallback, doesn't tear
echo                   out the optional Claude layer
echo    [E] Rule 6  -- try/catch on _propRenderDeepDeterministic
echo                   AND on the Claude enhancement fetch; both
echo                   render best-effort messages on failure
echo    [E] Rule 11 -- when payload columns are missing, the
echo                   narrative emits "n/a" rather than confident
echo                   wrong-direction text
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_det_deep
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"                              "%TMPDIR%\docs\index.html"                              >nul
copy /Y "PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat"         "%TMPDIR%\PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat"         >nul

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
copy /Y "%TMPDIR%\docs\index.html"                              "docs\index.html"                              >nul
copy /Y "%TMPDIR%\PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat"         "PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat"         >nul

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'extracted {len(blocks)} script blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Staging + committing...
git add docs/index.html
git add PUSH_DETERMINISTIC_DEEP_ANALYSIS.bat
git status --short
git commit -m "Deep Analysis: deterministic client-side narrative is now the primary path. Prior: button on Top Probable Outcomes called /api/claude/ask. When the Worker reports enabled:false (current state — Worker disabled, suspected upstream cause is the DEACTIVATED Odds API key), the button rendered a muted fallback explaining Claude was offline. User asked to make it a real functional feature. New: _propDeepAnalysisOnClick now ALWAYS renders _propRenderDeepDeterministic(propType, payload) immediately on first reveal, regardless of Worker state. The deterministic generators (added earlier in this commit batch) synthesize multi-paragraph deep-dives directly from the payload row's own columns — signals, stage1/stage2 probs, PQI delta, bullpen xwOBA gap, platoon_lineup_json, away/home_bvp_top5_json, ump_k_pct_delta, hl_*, etc. ml = pick rationale + stage breakdown + active signals + PQI + bullpen + platoon + BvP + caps + stress + verdict; ou = total projection + SP K env + bullpen + lineup concentration + ump + BvP run env + edge interp + verdict; k = K rate + threshold P(K>=N) + ump K delta + bottom-line confidence. Claude is still wired as an enhancement: if /api/claude/health returns enabled, /api/claude/ask is called in the background and its response is APPENDED below the deterministic block as 'Claude take:'. If the Worker is offline or errors, the deterministic narrative stands alone — no misleading red error. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (payload rows already carry the columns the narrative consumes), Rule 3 node --check, Rule 5 deterministic replaces fallback without tearing out the optional Claude enhancement, Rule 6 try/catch on both deterministic render and Claude fetch, Rule 11 missing payload columns render 'n/a' rather than confident wrong-direction text, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^)
echo    2. Click any "Deep analysis v" in Top Probable Outcomes
echo    3. Should display a full multi-paragraph narrative with
echo       stage probs, signals, PQI, bullpen gap, BvP context,
echo       cap firings, and a verdict line -- NOT the muted
echo       "Claude is disabled" message anymore
echo    4. When the Worker is re-enabled, a "Claude take:" block
echo       will appear underneath the deterministic narrative
echo       without any further code change
echo ============================================================
pause
