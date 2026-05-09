@echo off
cd /d "%~dp0"
del /f /q ".git\index.lock" 2>nul
echo ============================================================
echo  Shipping Claude Brain v2: live-learning loop
echo  -----------------------------------------------------------
echo  1. Push fix:  fresh-clone-then-push for both brain and
echo                postgame workflows (bypasses the credential
echo                invalidation Claude Code Action causes)
echo  2. Auto-postgame writer:  every 12:00 UTC, Claude reads
echo                yesterday's picks + game results and writes
echo                docs/data/postgame/^<date^>.json with W/L
echo                verdicts and patterns_observed. Closes the
echo                memory loop.
echo  3. Pre-game refreshes:  brain now runs 3x/day
echo                (07:00 UTC morning, 18:00 UTC lineups,
echo                22:30 UTC ~30 min before first pitch)
echo                so claude_picks gets fresher with each pass.
echo ============================================================
echo.

REM Save the new files to temp before syncing with remote (so they
REM survive the reset --hard below). The local repo may be a few
REM commits behind remote because earlier fixes were committed via
REM the GitHub web UI; we sync to those, then re-overlay our new
REM files on top.
echo Saving local edits to %TEMP%\mlb_edge_push...
mkdir "%TEMP%\mlb_edge_push" 2>nul
mkdir "%TEMP%\mlb_edge_push\.github" 2>nul
mkdir "%TEMP%\mlb_edge_push\.github\workflows" 2>nul
mkdir "%TEMP%\mlb_edge_push\tools" 2>nul
copy /Y ".github\workflows\claude-brain.yml" "%TEMP%\mlb_edge_push\.github\workflows\claude-brain.yml" >nul
copy /Y ".github\workflows\claude-postgame.yml" "%TEMP%\mlb_edge_push\.github\workflows\claude-postgame.yml" >nul
copy /Y "tools\claude_postgame_prompt.md" "%TEMP%\mlb_edge_push\tools\claude_postgame_prompt.md" >nul
copy /Y "PUSH_BRAIN_FIX.bat" "%TEMP%\mlb_edge_push\PUSH_BRAIN_FIX.bat" >nul
echo.

echo Fetching origin...
git fetch origin
echo.
echo Resetting tracked files to origin/main (untracked files like CSVs unaffected)...
git reset --hard origin/main
echo.

echo Restoring our edits on top of synced state...
copy /Y "%TEMP%\mlb_edge_push\.github\workflows\claude-brain.yml" ".github\workflows\claude-brain.yml" >nul
copy /Y "%TEMP%\mlb_edge_push\.github\workflows\claude-postgame.yml" ".github\workflows\claude-postgame.yml" >nul
copy /Y "%TEMP%\mlb_edge_push\tools\claude_postgame_prompt.md" "tools\claude_postgame_prompt.md" >nul
copy /Y "%TEMP%\mlb_edge_push\PUSH_BRAIN_FIX.bat" "PUSH_BRAIN_FIX.bat" >nul
echo.

echo Staging changes...
git add .github/workflows/claude-brain.yml
git add .github/workflows/claude-postgame.yml
git add tools/claude_postgame_prompt.md
git add PUSH_BRAIN_FIX.bat
git status --short
echo.

echo Committing...
git commit -m "claude-brain v2: live-learning loop (3x/day refresh + auto-postgame writer + fresh-clone push fix)"
echo.

echo Pushing to origin/main...
git push
echo.

echo ============================================================
echo  Done. Next steps:
echo  1. Open https://github.com/Gozorp/MLB-edges/actions
echo  2. Click "Claude brain - executive slate review"
echo  3. Click "Run workflow" -^> Run workflow (leave date blank)
echo  4. Wait ~10-15 min for it to complete with green check
echo  5. Verify docs/data/claude_picks/2026-05-09.json exists
echo  6. Refresh the dashboard and check the Claude column
echo ============================================================
pause
