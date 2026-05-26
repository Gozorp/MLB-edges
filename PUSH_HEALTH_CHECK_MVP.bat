@echo off
REM Pipeline health check MVP.
REM Adds tools/health_check.py + .github/workflows/health-check.yml.
REM First scheduled run (within 30 min of merge) will write
REM docs/data/health.json and docs/data/health_alert_state.json.
REM
REM MANUAL STEP REQUIRED AFTER PUSH:
REM   1. Discord -> Server Settings -> Integrations -> Webhooks ->
REM      New Webhook -> copy URL
REM   2. GitHub -> Settings -> Secrets and variables -> Actions ->
REM      New repository secret -> Name: DISCORD_HEALTH_WEBHOOK
REM Until that secret is set, the workflow writes health.json but the
REM Discord push half stays disabled. The pull half (dashboard card)
REM works either way.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-create the new files in the working tree (lost by the reset).
REM tools/health_check.py and .github/workflows/health-check.yml were
REM written via the Write tool to the user FS; reset wipes untracked
REM files, but these were already on disk before reset since they
REM weren't yet committed. Confirm they're still present.
if not exist tools\health_check.py (
  echo ERROR: tools/health_check.py missing - re-run the previous Write step
  pause
  exit /b 1
)
if not exist .github\workflows\health-check.yml (
  echo ERROR: .github/workflows/health-check.yml missing
  pause
  exit /b 1
)

REM Local smoke test: run the script once to make sure it doesn't crash.
python tools/health_check.py
if errorlevel 1 (
  echo health_check.py exited non-zero
  pause
  exit /b 1
)

git add tools/health_check.py .github/workflows/health-check.yml PUSH_HEALTH_CHECK_MVP.bat
REM Also stage the locally-generated health.json + alert state so the
REM dashboard has something to render before the first cron fires.
git add docs/data/health.json docs/data/health_alert_state.json 2>nul

git commit -m "feat(observability): pipeline health-check MVP with Discord webhook" -m "New artifacts:" -m "- tools/health_check.py: 3-check observer (daily_slate_heartbeat via git log grep, weights_state_freshness via audit log ts, core_models_presence via os.exists). Writes docs/data/health.json + docs/data/health_alert_state.json. POSTs to DISCORD_HEALTH_WEBHOOK on RED transitions (6h rate-limit per check) and at 08:00 UTC daily as a dead-man-switch digest. Zero non-stdlib deps." -m "- .github/workflows/health-check.yml: cron */30 * * * * + workflow_dispatch. Defensive git pull --rebase --autostash before push to absorb races with daily-slate runs." -m "Manual setup: add DISCORD_HEALTH_WEBHOOK as a GitHub Actions secret to enable the push half. Until then, the pull half (health.json + dashboard card) still works."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === NEXT STEPS ===
echo 1. Create Discord webhook: Server Settings -^> Integrations -^> Webhooks
echo 2. Add as GitHub repo secret named DISCORD_HEALTH_WEBHOOK
echo 3. Trigger workflow manually: Actions tab -^> Pipeline health check -^> Run workflow
echo 4. Verify Discord receives the first test ping
pause
