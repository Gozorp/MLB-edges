@echo off
REM Commit 1 of the health-check expansion: 9 new checks + schema v2 +
REM dashboard card. Kalshi divergence and Anthropic API probe held for
REM Commit 2 (network-dependent + pipeline change).
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch the latest origin versions of the two files we're patching
REM so we apply against a clean known-state, not whatever the sandbox
REM left behind.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/tools/health_check.py" -o tools\health_check.py
if errorlevel 1 ( echo curl health_check.py failed & pause & exit /b 1 )
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl index.html failed & pause & exit /b 1 )

python _patch_health_expand_commit1.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Local smoke test
python tools\health_check.py
if errorlevel 1 ( echo smoke test failed & pause & exit /b 1 )

git add tools\health_check.py docs\index.html docs\data\health.json docs\data\health_alert_state.json _patch_health_expand_commit1.py PUSH_HEALTH_EXPAND_COMMIT1.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(observability): health-check commit 1 - 9 new checks + dashboard card" -m "tools/health_check.py grows from 3 -> 12 checks. New: refit_calibrator_heartbeat, weekly_backtest_heartbeat, claude_brain_heartbeat (workflow git-log greps); bullpen_meta_freshness, odds_api_completeness, pending_sp_data_rate (CSV/file reads); cloudflare_deploy_freshness (HTTPS GET to Pages /api/health); runaway_ceiling_alarm, stress_warning_rate (audit log + diag CSV). Schema bumped to v2: every check now carries a category tag (workflows/data_flow/deployment/model), and the snapshot emits a per-category roll-up alongside the existing overall + checks fields." -m "docs/index.html: new health-card section between Ask the Slate accordion and parlay block. Fetches docs/data/health.json on load, renders category roll-up with click-to-expand individual check messages. Monospace + neon palette matches Quant Terminal identity. ~140 lines CSS+JS+HTML inline." -m "Held for Commit 2: kalshi_divergence (needs pipeline change to dual-source fair_prob), anthropic_api_probe (HTTPS to /api/claude/health). Isolating network-dependent checks in their own commit so a failure there doesn't take down the rest of the loop."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT ===
echo 1. Wait for next */30 cron OR fire manually: Actions -^> Pipeline health check -^> Run workflow
echo 2. Refresh https://gozorp.github.io/MLB-edges/ - new "Pipeline health" card should appear
echo 3. If cloudflare_deploy_freshness is RED, the Pages URL is wrong - tell me the correct one
pause
