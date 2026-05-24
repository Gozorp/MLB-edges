@echo off
REM ============================================================================
REM PUSH_BULLPEN_EDGE.bat
REM New "Bullpen edge" preview-card sits between Lineup Edge and the
REM projected-lineup cards. Per side: per-batter K-prob vs the opposing
REM closer/top high-leverage arm + one-line strain note from bullpen_meta.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging + committing ===
git add docs\index.html _patch_bullpen_edge.py PUSH_BULLPEN_EDGE.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(dashboard): Bullpen edge card in per-game expander" -m "New full-width preview-card sits between Lineup Edge and the per-team lineup cards. Two side-by-side panels show per-batter K-vulnerability against the OPPOSING bullpen's top high-leverage arm (closer proxy), reusing the same Log5 _batterKProb helper that the SP version uses." -m "Adds a one-line strain note pulled from the bullpen_meta sidecar: when the closer is on B2B/3-day/OVERWORKED, surfaces a 'setup man may take the 9th' / 'effectively unavailable tonight' callout so the user can see when the obvious leverage arm is going to be skipped." -m "Reuses _batterKProb (Lineup-Edge sprint) and _bullpenMetaForMatchup (Bullpen Outlook sprint). New helpers _topBullpenArm and _bullpenStrainNote with loose name-match to absorb 'Jr.' / accent diffs between roster fetch and bullpen_meta. Graceful-degrade paths: opposing bullpen not yet hydrated -> muted, lineup not posted -> muted, no eligible batters -> muted."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
