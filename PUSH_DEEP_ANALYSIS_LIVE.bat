@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Top Probable Outcomes: live "Deep analysis" via Claude
echo  -----------------------------------------------------------
echo  Wires the Deep analysis buttons (previously stubbed as
echo  "Coming in Phase 4") to the existing /api/claude/ask
echo  Cloudflare Worker endpoint that powers the Ask Claude
echo  widget.  No new backend needed — endpoint already exists.
echo
echo  Per-prop structured prompts (formatted in JS):
echo    ml — game ML pick: starting pitchers, lineup quality,
echo         bullpen, recent form, ump/park, structural risks,
echo         CONFIRM/DOWNGRADE/OVERRIDE verdict
echo    ou — totals O/U: SP quality both sides, lineup-vs-SP,
echo         bullpen workload, park, weather, umpire bias,
echo         confidence level with contradicting signals
echo    k  — pitcher Ks: recent K-rate trend, opposing lineup
echo         contact quality, ump strike zone, weather,
echo         expected pitch count + leash, over/under threshold
echo
echo  Caching: each Deep-analysis container caches the response
echo  once loaded.  Re-clicks toggle visibility (no re-fetch).
echo  Error states reset dataset.loaded so retry on next click.
echo
echo  Files changed:
echo
echo  1. docs/index.html (+81 lines net)
echo     New _propBuildClaudePrompt(propType, payload) helper —
echo     produces structured per-prop-type Claude prompts.
echo     New _propDeepAnalysisOnClick(probeId) async handler —
echo     toggles container visibility, calls /api/claude/ask
echo     on first reveal, caches response.
echo     window.__propPayloads global registry lets onclick
echo     handlers recover structured payloads (which can't pass
echo     through HTML attributes directly).
echo     _propCard() now generates a styled container + button
echo     that invokes the async handler instead of the stub.
echo
echo  2. PUSH_DEEP_ANALYSIS_LIVE.bat (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed: /api/claude/ask exists at
echo                  docs/index.html:4007, returns {answer}
echo    [E] Rule 3  — node --check JS syntax gate
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — reused existing endpoint, did NOT build
echo                  a new /api/claude/prop-analysis route
echo    [E] Rule 6  — try/except + dataset.loaded reset on
echo                  error so retries work cleanly
echo    [E] Rule 13 — this script narrates the change
echo
echo  Behavior on /api/claude/health probe failure:
echo  the deep-analysis fetch will throw a network error which
echo  the handler displays in red.  The Top Probable Outcomes
echo  section itself still renders fine without Claude — only
echo  the deep-analysis expand becomes non-functional.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_deep_analysis
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"               "%TMPDIR%\docs\index.html"               >nul
copy /Y "PUSH_DEEP_ANALYSIS_LIVE.bat"   "%TMPDIR%\PUSH_DEEP_ANALYSIS_LIVE.bat"   >nul

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
copy /Y "%TMPDIR%\docs\index.html"               "docs\index.html"               >nul
copy /Y "%TMPDIR%\PUSH_DEEP_ANALYSIS_LIVE.bat"   "PUSH_DEEP_ANALYSIS_LIVE.bat"   >nul

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks))"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Staging + committing...
git add docs/index.html
git add PUSH_DEEP_ANALYSIS_LIVE.bat
git status --short
git commit -m "Top Probable Outcomes: wire Deep analysis buttons to live /api/claude/ask. Previously stubbed as 'Coming in Phase 4' placeholders. Endpoint already exists at docs/index.html:4007 powering the Ask Claude widget — reused rather than building a new /api/claude/prop-analysis route. Each prop type (ml, ou, k) gets a structured prompt: game ML asks for SP matchup + lineup + bullpen + structural risks + CONFIRM/DOWNGRADE/OVERRIDE verdict; totals asks for both-side SP quality + lineup-vs-SP + bullpen workload + park + weather + ump + contradicting signals; pitcher Ks asks for K-rate trend + opposing contact quality + ump zone + weather + leash + over/under threshold recommendation. Per-card response caching via dataset.loaded flag — re-clicks toggle visibility without re-fetching tokens; error states reset the flag so retries work. window.__propPayloads global registry lets onclick handlers recover structured payloads that don't pass through HTML attributes cleanly. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (verified /api/claude/ask returns {answer}), Rule 3 node --check syntax gate, Rule 5 reused existing endpoint (no backend rebuild), Rule 6 try/except + dataset.loaded reset on error, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Hard-refresh dashboard (Ctrl+Shift+R + ?_=newts)
echo    2. Click any "Deep analysis ▼" in Top Probable Outcomes
echo    3. Should display "Asking Claude for deep analysis..."
echo       then show formatted multi-paragraph response
echo    4. Click again to collapse (no re-fetch — cached)
echo    5. If Cloudflare Worker is offline:
echo       "Network error: ..." displayed in red, retry on next click
echo ============================================================
pause
