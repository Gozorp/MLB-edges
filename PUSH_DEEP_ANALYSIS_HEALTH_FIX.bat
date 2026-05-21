@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bugfix: Deep Analysis health-check
echo  -----------------------------------------------------------
echo  Bug found in production debugging sweep:
echo
echo    "Deep analysis" button on Top Probable Outcomes returns
echo    "Claude error: could not load slate context" when clicked.
echo
echo  Root cause:
echo    /api/claude/health currently returns {enabled: false}.
echo    The Ask Claude widget probes this and gracefully hides
echo    itself when disabled.  The new Deep Analysis button I
echo    shipped in 82f0887 did NOT probe health — it fires
echo    /api/claude/ask unconditionally, hits the Worker's
echo    disabled-state error, and shows a misleading red error
echo    in the UI.
echo
echo  Fix:
echo    _propDeepAnalysisOnClick now probes /api/claude/health
echo    on first invocation, caches the result in
echo    window.__claudeHealth, and short-circuits with a
echo    friendly muted message when disabled:
echo
echo      "Claude deep-analysis is currently disabled. Click
echo       the matching row in the Slate table below for
echo       in-depth model reasoning, signal breakdown, and
echo       grade derivation — all generated client-side
echo       without requiring the Claude API."
echo
echo  This matches the gracefully-hide pattern that the
echo  existing Ask Claude widget uses, plus adds an inline
echo  pointer to the slate-row deep-dive (which IS available
echo  and doesn't need the Worker).
echo
echo  Files changed:
echo
echo  1. docs/index.html (+18 lines net)
echo     _propDeepAnalysisOnClick gets a health probe + cache
echo     gate before the existing /api/claude/ask call.
echo
echo  2. PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed via curl: /api/claude/health
echo                  returns {enabled: false}
echo    [E] Rule 3  — node --check JS gate
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — single targeted fix, no broader rewrite
echo    [E] Rule 6  — best-effort wrap on health probe (default
echo                  to {enabled:false} on fetch failure)
echo    [E] Rule 13 — this script narrates the change
echo
echo  Note: the Worker being disabled may be intentional (cost,
echo  API key rotation, rate-limit pause).  This commit does
echo  NOT try to enable it — only makes the UI gracefully
echo  handle the disabled state.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_deep_health_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"                       "%TMPDIR%\docs\index.html"                       >nul
copy /Y "PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat"     "%TMPDIR%\PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat"     >nul

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
copy /Y "%TMPDIR%\docs\index.html"                       "docs\index.html"                       >nul
copy /Y "%TMPDIR%\PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat"     "PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat"     >nul

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks))"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Staging + committing...
git add docs/index.html
git add PUSH_DEEP_ANALYSIS_HEALTH_FIX.bat
git status --short
git commit -m "Bugfix: Deep Analysis button probes /api/claude/health before firing. Production debugging sweep found that clicking 'Deep analysis' on any Top Probable Outcomes prop returns 'Claude error: could not load slate context' — misleading. Root cause: the Worker's /api/claude/health currently returns {enabled: false}; the existing Ask Claude widget gracefully hides when this flag is false, but the new Deep Analysis button I shipped in 82f0887 fired /api/claude/ask unconditionally and surfaced the disabled-state error verbatim. Fix: _propDeepAnalysisOnClick probes /api/claude/health on first invocation, caches in window.__claudeHealth, and short-circuits with a friendly muted message + pointer to the slate-row deep-dive (which is client-side and doesn't need the Worker) when disabled. Matches the gracefully-hide pattern of _initAskClaude. Best-effort wrap on the health probe defaults to {enabled:false} on fetch failure. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (curl /api/claude/health returned enabled:false), Rule 3 node --check, Rule 5 single targeted fix, Rule 6 best-effort wrap, Rule 13 push script narrates. This commit does NOT try to enable the Worker — that may be intentionally disabled (cost / API key / rate limit). Only the UI behavior is fixed."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Hard-refresh dashboard
echo    2. Click any "Deep analysis ▼" in Top Probable Outcomes
echo    3. Should display the friendly disabled message in
echo       muted gray, NOT the misleading red Claude error
echo    4. When the Worker is re-enabled (when its
echo       /api/claude/health returns enabled:true), the button
echo       will automatically start producing real responses
echo       without further code changes
echo ============================================================
pause
