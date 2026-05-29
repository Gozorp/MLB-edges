@echo off
REM ===========================================================================
REM PUSH_REMOVE_ANTHROPIC_PROBE.bat
REM ---------------------------------------------------------------------------
REM WHY: Claude features run on your Claude Max subscription
REM      (CLAUDE_CODE_OAUTH_TOKEN, via the claude-brain / claude-postgame
REM      Actions), NOT a pay-per-token ANTHROPIC_API_KEY. The Worker is
REM      intentionally never given an API key, so anthropic_api_probe -- which
REM      asserts the Worker reports enabled:true -- was a PERMANENT false-RED.
REM      claude_brain_heartbeat is the single source of truth for Claude health:
REM      if the OAuth token/subscription fails, the brain bake fails and that
REM      heartbeat goes RED on its own.
REM
REM WHAT: tools/remove_anthropic_probe.py strips all 3 reference sites from
REM       tools/health_check.py (registry entry + function + CHECKS list entry),
REM       AST-validated, backup -> health_check.py.bak.
REM
REM BONUS: the local working-copy tools/health_check.py is NUL-corrupted (769
REM        null bytes). `git reset --hard origin/main` below overwrites it with
REM        the clean committed version before editing, so this also REPAIRS it.
REM
REM SAFE: Rule 3 ast gate, Rule 4 safe-push, Rule 5 single-purpose, Rule 12/13.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

git fetch origin main
git reset --hard origin/main

REM --- strip the probe out of the now-clean health_check.py ---
python tools\remove_anthropic_probe.py
if errorlevel 1 ( echo removal step failed & pause & exit /b 1 )

REM --- Rule 3 gate: file still valid Python AND the probe is gone ---
python -c "import ast; s=open(r'tools/health_check.py',encoding='utf-8').read(); ast.parse(s); assert 'anthropic_api_probe' not in s, 'probe still referenced'; assert 'claude_brain_heartbeat' in s, 'lost brain heartbeat'; print('gate OK: probe removed, health_check.py valid')"
if errorlevel 1 ( echo AST GATE FAILED -- not committing & pause & exit /b 1 )

git add tools\health_check.py tools\remove_anthropic_probe.py PUSH_REMOVE_ANTHROPIC_PROBE.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "fix(health-check): remove anthropic_api_probe -- Claude runs on Max, not an API key" -m "Claude features run on the Claude Max subscription (CLAUDE_CODE_OAUTH_TOKEN via claude-brain/claude-postgame), not a metered ANTHROPIC_API_KEY. The Worker is never given an API key by design, so anthropic_api_probe was a permanent false-RED. claude_brain_heartbeat already monitors Claude health. Removed registry entry + function + CHECKS entry via tools/remove_anthropic_probe.py (AST-based, backup). Rule 3 ast-gated, Rule 4 safe-push."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo anthropic_api_probe removed. Deployment category now has just
    echo cloudflare_deploy_freshness; Claude health = claude_brain_heartbeat.
) else (
    echo Nothing to commit -- probe already removed on origin.
)
echo.
pause
