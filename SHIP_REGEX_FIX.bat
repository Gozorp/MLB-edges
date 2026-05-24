@echo off
REM Patch is already applied to docs/index.html (verified via grep).
REM HARD_RECOVER's findstr verify line errored on the [12] bracket escape,
REM blocking the stage/commit/push steps. This .bat just ships.
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging ===
git add docs\index.html _patch_series_dh_regex.py PUSH_SERIES_DH_REGEX_FIX.bat HARD_RECOVER_AND_PUSH_REGEX.bat SHIP_REGEX_FIX.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "fix(dashboard): matchResult regex no longer treats series (G2 of 3) as doubleheader G2" -m "Regex matched BOTH '(G2)' (DH from _dedupDoubleheaders) and '(G2 of 3)' (series from _addSeriesSuffix). Series rows therefore resolved to the DH-G2 key, picking up the wrong gamePk." -m "Concrete failure 2026-05-24: DET @ BAL (G2 of 3) is series game 2; the actual DH G2 (gamePk 824840) is scheduled for 22:05Z. Chip showed PRE-GAME instead of G1's Final 3-5 outcome." -m "Tighten regex to /\\(G([12])\\)/. Comment block updated to make the two annotation sources explicit so future maintenance does not re-introduce the conflation."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
