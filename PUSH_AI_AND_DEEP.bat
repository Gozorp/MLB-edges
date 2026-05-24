@echo off
REM ============================================================================
REM PUSH_AI_AND_DEEP.bat
REM Two-part ship:
REM   1. Cloudflare Pages Functions for AI: functions/api/claude/health.js
REM      + ask.js. Flips on automatically once you set ANTHROPIC_API_KEY
REM      as a Pages env var. NOTE: only works on the Cloudflare Pages
REM      mirror, NOT on gozorp.github.io (GitHub Pages has no server).
REM   2. Expanded deterministic deep-analysis: collapsible sub-sections
REM      (Pick thesis / Counter-signals / What would change my mind / etc.),
REM      hover tooltips on every stat with its source, inline
REM      "Ask Claude about this" button that pre-fills the slate question.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging ===
git add docs\index.html _patch_deep_narrative_expand.py PUSH_AI_AND_DEEP.bat functions\api\claude\health.js functions\api\claude\ask.js
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "feat(dashboard): AI-augmented narrative + expanded deep analysis" -m "PART 1 (AI): new Cloudflare Pages Functions functions/api/claude/health.js and ask.js. health.js reports enabled iff ANTHROPIC_API_KEY env is set. ask.js proxies to Anthropic Messages API (model defaults to claude-opus-4-6, override via ANTHROPIC_MODEL env). Slate CSV head injected as context so Claude has the numbers. CORS-open for cross-origin from gozorp.github.io. To enable: Cloudflare dashboard -> Pages -> project -> Settings -> Environment variables -> add ANTHROPIC_API_KEY then redeploy. Dashboard's _initAskClaude + _propDeepAnalysisOnClick already poll /api/claude/health and light up the AI surface when enabled returns true." -m "PART 2 (Deep analysis expansion): three deterministic narratives (_deepNarrativeML / _deepNarrativeOU / _deepNarrativeK) restructured into named collapsible sub-sections. ML now has: Pick thesis (open) / Pitching matchup / Signals & rule firings / PQI & bullpen / Lineup (platoon) / BvP / Bullpen outlook / Counter-signals (NEW) / What would change my mind (NEW) / Bottom line (open). OU and K have parallel structures with prop-appropriate sections." -m "Helpers: _deepSection(title, body, openByDefault), _dt(value, source) for hover-tooltip stats, _deepCounterSignalsML/OU/K (derive what argues AGAINST the model's call from the existing signals), _deepPivotPointsML/OU/K (quantified tripwires that would flip the call), _deepAskClaudeButton (inline button that pre-fills the Ask-the-Slate textarea with a prop-scoped question and scrolls to it)." -m "CSS: .deep-section + .deep-h5 collapsible pattern, .dt tooltip class (dotted underline + cursor:help), .deep-ask-claude button styling. Click delegation listener at bottom of file toggles .deep-section.open on .deep-h5 click and handles the Ask-Claude button."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
