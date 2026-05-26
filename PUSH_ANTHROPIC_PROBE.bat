@echo off
REM Commit 2 of the health-check expansion: anthropic_api_probe.
REM kalshi_divergence was dropped after the 2026-05-25 probe revealed
REM the OddsAPI subscription was cancelled 4 days prior (no second
REM source to diverge from). Final shape of Commit 2 is a single new
REM check: HTTPS GET to /api/claude/health, RED on enabled:false.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch the file we're patching from origin so the patch applies
REM against a clean known-state, not whatever the local sandbox has.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/tools/health_check.py" -o tools\health_check.py
if errorlevel 1 ( echo curl health_check.py failed & pause & exit /b 1 )

python _patch_anthropic_probe.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Local smoke test
python tools\health_check.py
if errorlevel 1 ( echo smoke test failed & pause & exit /b 1 )

git add tools\health_check.py docs\data\health.json docs\data\health_alert_state.json _patch_anthropic_probe.py PUSH_ANTHROPIC_PROBE.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(observability): health-check commit 2 - anthropic_api_probe" -m "Adds 13th check: anthropic_api_probe. HTTPS GET to /api/claude/health on the Cloudflare Pages deployment. RED when endpoint unreachable OR when enabled:false (ANTHROPIC_API_KEY env var unset, which silently disables Deep Analysis on the dashboard - exactly the silent failure mode the alerter exists to catch). YELLOW when model field doesn't match expected 'claude-opus-4-6'. GREEN otherwise. Sits under CAT_DEPLOYMENT alongside cloudflare_deploy_freshness so the deployment category now end-to-end monitors: site reachable -> latest commit deployed -> Functions runtime serving -> AI integration alive." -m "kalshi_divergence dropped from this commit (and from the roadmap until further notice). 2026-05-25 probe found the OddsAPI subscription was cancelled 2026-05-21, so there is no second economic-market source to diverge against. A Kalshi-vs-ESPN proxy was rejected on cost/value grounds: permanent dual-scrape against a fragile HTML-parsing fallback raises IP-block risk for noisy signal. Will revisit if/when a second prediction market (Polymarket) or a re-subscribed OddsAPI returns." -m "No schema bump - v2 from commit 1 already supports adding checks under existing categories. Total checks: 12 -> 13."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT ===
echo 1. Wait for next */30 cron OR fire manually: Actions -^> Pipeline health check -^> Run workflow
echo 2. Refresh https://gozorp.github.io/MLB-edges/ - new anthropic_api_probe row should appear in the Pipeline health card under "deployment"
echo 3. If anthropic_api_probe is RED with "enabled:false" - the ANTHROPIC_API_KEY env var is missing on Cloudflare Pages. Cloudflare dashboard -^> Pages -^> mlb-edges -^> Settings -^> Environment variables -^> Production -^> add ANTHROPIC_API_KEY then redeploy.
pause
