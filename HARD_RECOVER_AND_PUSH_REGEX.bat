@echo off
REM ============================================================================
REM HARD_RECOVER_AND_PUSH_REGEX.bat
REM
REM Repo wedged from earlier autostash conflict (unmerged paths in auto-baked
REM data files). Hard reset to origin/main, then re-apply the regex fix and
REM push cleanly.
REM
REM The only "real" change we care about preserving is the matchResult regex
REM fix in docs/index.html, which we'll re-apply via _patch_series_dh_regex.py
REM after the reset.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock (
  echo === Removing stale .git\index.lock ===
  del /F /Q .git\index.lock
)

echo === Aborting any in-progress rebase/merge ===
git rebase --abort 2>nul
git merge --abort 2>nul

echo === Resetting hard to origin/main ===
git fetch origin main
if errorlevel 1 ( echo fetch failed & pause & exit /b 1 )
git reset --hard origin/main
if errorlevel 1 ( echo reset failed & pause & exit /b 1 )

echo === Cleaning untracked files in docs/data ===
git clean -fd docs\data 2>nul

echo === Re-applying regex patch on clean origin tree ===
python _patch_series_dh_regex.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Verifying patch landed ===
findstr /C:"const gMatch = matchup.match(/\(G([12])\)/);" docs\index.html >nul
if errorlevel 1 ( echo MISSING: tightened regex & pause & exit /b 1 )

echo === Staging + committing ===
git add docs\index.html _patch_series_dh_regex.py PUSH_SERIES_DH_REGEX_FIX.bat HARD_RECOVER_AND_PUSH_REGEX.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(dashboard): matchResult regex no longer treats series (G2 of 3) as doubleheader G2" -m "Regex /\\(G([12])(?:\\s+of\\s+\\d+)?\\)/i matched BOTH '(G2)' (doubleheader) and '(G2 of 3)' (series indicator). Series rows therefore resolved to the DH-G2 results key, picking up the wrong gamePk." -m "Concrete failure 2026-05-24: DET @ BAL (G2 of 3) is just game 2 of a 3-game series; the actual DH G2 (gamePk 824840) is scheduled for 22:05Z. The chip showed PRE-GAME instead of the G1 (Final 3-5) outcome." -m "Fix: tighten regex to /\\(G([12])\\)/ - only bare suffix is the DH signal. Comment block updated to make the two annotation sources explicit."
if errorlevel 1 ( echo git commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
