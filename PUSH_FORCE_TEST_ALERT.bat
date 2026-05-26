@echo off
REM Permanent test-alert button on the health-check workflow.
REM Adds workflow_dispatch boolean input `force_test_alert`. When true,
REM tools/health_check.py reads FORCE_TEST_ALERT env and posts a
REM synthetic Discord ping, bypassing all checks. Useful for verifying
REM webhook plumbing after secret rotation or adding new alert paths.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git add tools/health_check.py .github/workflows/health-check.yml _patch_force_test_alert.py PUSH_FORCE_TEST_ALERT.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(observability): permanent test-alert button for health-check webhook" -m "Adds workflow_dispatch boolean input force_test_alert to health-check.yml. When true, tools/health_check.py reads FORCE_TEST_ALERT env and posts a synthetic Discord ping (skips all checks, doesn't touch health.json or alert state). Useful for verifying webhook plumbing after rotating DISCORD_HEALTH_WEBHOOK, after adding new alert rules, or any time we want immediate end-to-end loop confirmation without waiting for the 8am UTC dead-man digest." -m "Keeping the flag permanent rather than ship-then-pull. Costs ~15 lines, only firable via manual workflow_dispatch so can't false-alarm from cron, and we'll want this button every time we touch the alert path."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === TEST THE LOOP ===
echo 1. Rotate the webhook: delete current Discord webhook + create fresh
echo 2. Set DISCORD_HEALTH_WEBHOOK secret in GitHub repo settings
echo 3. Actions tab -^> Pipeline health check -^> Run workflow
echo 4. CHECK the "Fire a synthetic Discord test ping" box
echo 5. Click Run workflow
echo 6. Expect a blue test embed in Discord within ~30 seconds
pause
