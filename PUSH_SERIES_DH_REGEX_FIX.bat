@echo off
REM ============================================================================
REM PUSH_SERIES_DH_REGEX_FIX.bat
REM Fix: matchResult regex conflated "(G2)" (doubleheader) with "(G2 of 3)"
REM (series indicator). DET @ BAL (G2 of 3) was resolving to the scheduled
REM doubleheader G2 gamePk (824840) instead of the already-Final G1 (824839).
REM Tighten regex to bare /\(G([12])\)/ only.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock (
  echo === Removing stale .git\index.lock ===
  del /F /Q .git\index.lock
)

echo === Pre-pull to absorb any auto-bake commits ===
git clean -fd docs\data 2>nul
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pre-pull failed & pause & exit /b 1 )

echo === Refreshing docs/index.html from origin ===
curl -fsS "https://raw.githubusercontent.com/gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl failed & pause & exit /b 1 )

echo === Applying regex fix ===
python _patch_series_dh_regex.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Verifying patch landed ===
findstr /C:"const gMatch = matchup.match(/\(G([12])\)/);" docs\index.html >nul
if errorlevel 1 ( echo MISSING: tightened regex & pause & exit /b 1 )
findstr /C:"of N" docs\index.html >nul
if errorlevel 1 ( echo WARNING: updated comment block missing & rem non-fatal )

echo === Staging + committing ===
git add docs\index.html _patch_series_dh_regex.py PUSH_SERIES_DH_REGEX_FIX.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "fix(dashboard): matchResult regex no longer treats series (G2 of 3) as doubleheader G2" -m "Bug: the regex /\\(G([12])(?:\\s+of\\s+\\d+)?\\)/i matched BOTH '(G2)' (doubleheader from _dedupDoubleheaders) and '(G2 of 3)' (series indicator from _addSeriesSuffix). Series rows therefore resolved to the doubleheader-G2 results key, picking up the wrong gamePk." -m "Concrete failure today: DET @ BAL (G2 of 3) on the slate is just the second game of a 3-game series; the DH G2 (gamePk 824840) is scheduled for 22:05Z. The chip showed PRE-GAME instead of the actual G1 (Final 3-5) outcome." -m "Fix: tighten regex to /\\(G([12])\\)/ — only the bare suffix is the DH signal; series indicators must fall through to the bare AWAY@HOME key. Comment block updated to make the two annotation sources explicit so future readers don't re-introduce the conflation."
if errorlevel 1 ( echo git commit failed & pause & exit /b 1 )

echo === Pull --rebase --autostash + push ===
git clean -fd docs\data 2>nul
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
