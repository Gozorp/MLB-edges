@echo off
REM ============================================================================
REM PUSH_TBD_DESIGN_FIXES.bat
REM Ships 3-in-1 fix for the "too many TBDs" design issue:
REM   1. Doubleheader collision: fetchMLBResults now keys by gameNumber so
REM      G2's result no longer overwrites G1.  matchResult parses (G1)/(G2).
REM   2. Visual differentiation: LIVE (green dot + score), PRE-GAME (blue
REM      clock), POSTPONED (amber) chips instead of one undifferentiated
REM      gray TBD chip.
REM   3. Stop filtering postponed games (3 sites) so the POSTPONED badge
REM      we shipped in 7bd9d5f finally renders.
REM
REM Safe-push pattern (memory: add BEFORE pull --rebase).
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

echo === Refreshing local docs/index.html from origin (working tree was truncated) ===
curl -s "https://raw.githubusercontent.com/gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl failed & pause & exit /b 1 )

echo === Re-applying TBD design patch on clean origin tree ===
python _patch_tbd_design.py
if errorlevel 1 ( echo patch script failed & pause & exit /b 1 )

echo === Verifying patch landed (grep checks) ===
findstr /C:"_liveScoreFor" docs\index.html >nul
if errorlevel 1 ( echo MISSING: _liveScoreFor helper & pause & exit /b 1 )
findstr /C:"PRE-GAME" docs\index.html >nul
if errorlevel 1 ( echo MISSING: PRE-GAME chip & pause & exit /b 1 )
findstr /C:"@G${gameNumber}" docs\index.html >nul
if errorlevel 1 ( echo MISSING: DH key & pause & exit /b 1 )
findstr /C:"gMatch[1]" docs\index.html >nul
if errorlevel 1 ( echo MISSING: matchResult DH parse & pause & exit /b 1 )

echo === Staging + committing ===
git add docs\index.html
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(dashboard): TBD-chip design + DH collision + show postponed badge" -m "1) fetchMLBResults now keys by gameNumber so G2 of a doubleheader no longer overwrites G1 in the results map.  matchResult parses (G1)/(G2) tag and looks up the specific game first." -m "2) New chip styles for live state differentiation: LIVE = green dot + away/score/home (e.g. '* LIVE LAD 4-3 MIL'), PRE-GAME = blue clock '? PRE-GAME', POSTPONED = amber.  Previously all non-final states rendered as one undifferentiated gray TBD chip." -m "3) Removed _isPostponedRow filter from all 3 call sites (initial loadSlate, poller re-render, K-rerender) so the POSTPONED badge added in 7bd9d5f finally renders on the slate."
if errorlevel 1 ( echo git commit failed & pause & exit /b 1 )

echo === Pull --rebase then push ===
git pull --rebase origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
