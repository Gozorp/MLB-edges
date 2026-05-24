@echo off
REM ============================================================================
REM PUSH_QUANT_TERMINAL.bat
REM 4-phase Quant Terminal redesign:
REM   P1: hide bracketed date strip (existing <input type=date> stays)
REM   P2: drop REASONS column from slate table (reasoning lives in expander)
REM   P3: compact bullpen cards (status tag + pitch count + top arm)
REM   P4: monospace body font + > prompt on Ask-the-Slate
REM
REM Safe-push pattern: stage helpers BEFORE rebase, use --autostash.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

echo === Refreshing docs/index.html from origin ===
curl -fsS "https://raw.githubusercontent.com/gozorp/MLB-edges/main/docs/index.html" -o docs\index.html
if errorlevel 1 ( echo curl failed & pause & exit /b 1 )

echo === Applying 4-phase patch ===
python _patch_quant_terminal.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Verifying every phase landed ===
findstr /C:"date-strip\" id=\"dateStrip\" style=\"display:none" docs\index.html >nul
if errorlevel 1 ( echo MISSING P1: dateStrip hidden & pause & exit /b 1 )

findstr /C:"<th>Reasons</th>" docs\index.html >nul
if not errorlevel 1 ( echo P2a FAILED: REASONS th still present & pause & exit /b 1 )

findstr /C:"colspan = haveAnyResult ? 13 : 12" docs\index.html >nul
if errorlevel 1 ( echo MISSING P2c: colspan adjust & pause & exit /b 1 )

findstr /C:"Quant-terminal compact card" docs\index.html >nul
if errorlevel 1 ( echo MISSING P3: compact bullpen card & pause & exit /b 1 )

findstr /C:"JetBrains Mono" docs\index.html >nul
if errorlevel 1 ( echo MISSING P4a: monospace font & pause & exit /b 1 )

findstr /C:"ask the slate..." docs\index.html >nul
if errorlevel 1 ( echo MISSING P4b: > prompt placeholder & pause & exit /b 1 )

echo === Staging helpers + dashboard ===
git add docs\index.html _patch_quant_terminal.py PUSH_QUANT_TERMINAL.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

echo === Committing ===
git commit -m "feat(dashboard): Quant Terminal redesign (4 phases)" -m "P1: hide bracketed date strip; existing <input type=date> is the date control." -m "P2: drop REASONS column from slate table; full reasoning still lives in the expander narrative." -m "P3: bullpen outlook cards stripped to status tag + pitch count + top arm; walls of prose moved to game expander." -m "P4: monospace body font (JetBrains Mono stack) + borderless Ask-the-Slate input with '>' command-line prompt."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Pull --rebase --autostash + push ===
git pull --rebase --autostash origin main
if errorlevel 1 ( echo pull failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
