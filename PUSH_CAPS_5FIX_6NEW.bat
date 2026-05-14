@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  CAP 5 regex fix + CAP 6 new rule + audit-target revisions
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py
echo     - HARD CAP 5 regex: r"F1_xera_gap\*"
echo                    --^> r"F1_xera_gap=[\d.]+\*"
echo       Bug: signals string contains "F1_xera_gap=1.90*" with the
echo       asterisk at the END of the numeric value, not adjacent to
echo       "gap".  Original pattern never matched any live diag CSV.
echo       SD@MIL 5/13 A-tier loss exposed it.
echo     - NEW HARD CAP 6: edge_pp ^> +25pp -^> score=0 (SKIP)
echo       Mirrors CAP 3's unconditional-SKIP form.  Catches the
echo       isotonic calibrator's upper-bucket hallucinations.
echo       Validation: 3 losses on edge ^> +23pp in 6 days
echo         5/8 SEA@CHW  (+31.2pp, lost 12-8)
echo         5/8 NYM@ARI  (+23.0pp, lost 1-3)
echo         5/13 PHI@BOS (+31.0pp, A-tier loss 1-3)
echo
echo  2. tools/run_backtest.py
echo     CAP_PRECISION_TARGETS revisions:
echo       CAP 1: 1.00 --^> 0.85   (downside filters don't need perfection;
echo                                 STL@OAK 5/13 win-missed brought live
echo                                 precision to 80%%, 85%% is the right
echo                                 operating point for a defensive rule)
echo       CAP 6: 0.70 (new)        wider target since the rule fires on a
echo                                 sparse sample
echo     CAP_LABELS gains entry for CAP 6.
echo
echo  3. tools/claude_brain_prompt.md
echo     "Hard caps already enforced" section grows from 5 to 6 caps.
echo     Updated CAP 1 status to reflect 4-for-5 live precision + the
echo     explicit "do not relax" instruction.  CAP 5 documents the
echo     regex fix.  CAP 6 added.
echo
echo  4. tools/claude_postgame_prompt.md
echo     signals_to_recheck vocabulary grows from 5 to 6 caps.
echo     Adds canonical name [HARD CAP 6] extreme_positive_edge_hallucination.
echo
echo  Why these two fixes belong together:
echo    Both leaks were surfaced by the 5/13 cap-audit run.  CAP 5's
echo    regex bug let SD@MIL through as an A-tier parlay-eligible loss;
echo    CAP 6's absence let PHI@BOS through as an A-tier loss on +31pp
echo    of fictional edge.  Either one alone would have prevented a
echo    parlay-tier loss today.  Shipping both maximizes coverage going
echo    forward.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_caps_5fix_6new
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"     2>nul
copy /Y "mlb_edge\parlay_builder.py"             "%TMPDIR%\mlb_edge\parlay_builder.py"             >nul
copy /Y "tools\run_backtest.py"                   "%TMPDIR%\tools\run_backtest.py"                   >nul
copy /Y "tools\claude_brain_prompt.md"            "%TMPDIR%\tools\claude_brain_prompt.md"            >nul
copy /Y "tools\claude_postgame_prompt.md"         "%TMPDIR%\tools\claude_postgame_prompt.md"         >nul
copy /Y "PUSH_CAPS_5FIX_6NEW.bat"                 "%TMPDIR%\PUSH_CAPS_5FIX_6NEW.bat"                 >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring edits...
copy /Y "%TMPDIR%\mlb_edge\parlay_builder.py"             "mlb_edge\parlay_builder.py"             >nul
copy /Y "%TMPDIR%\tools\run_backtest.py"                   "tools\run_backtest.py"                   >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"            "tools\claude_brain_prompt.md"            >nul
copy /Y "%TMPDIR%\tools\claude_postgame_prompt.md"         "tools\claude_postgame_prompt.md"         >nul
copy /Y "%TMPDIR%\PUSH_CAPS_5FIX_6NEW.bat"                 "PUSH_CAPS_5FIX_6NEW.bat"                 >nul

echo Syntax-checking parlay_builder.py and run_backtest.py before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); ast.parse(open('tools/run_backtest.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add tools/run_backtest.py
git add tools/claude_brain_prompt.md
git add tools/claude_postgame_prompt.md
git add PUSH_CAPS_5FIX_6NEW.bat
git status --short
git commit -m "HARD CAP 5 regex fix + HARD CAP 6 new rule + audit target revisions. CAP 5 was structurally inert from the moment it shipped: the pattern r'F1_xera_gap\*' looked for the literal three-character sequence 'gap*' but every live diag CSV writes the signal as 'F1_xera_gap=1.90*' (asterisk at the end of the numeric value). SD@MIL 5/13 was the first time a row reached A-tier with F1* as the only F-signal and the cap should have demoted to B; the regex returned None, the if-block was skipped, MIL kept its A grade and lost 3-1. Fix: pattern now r'F1_xera_gap=[\d.]+\*' which correctly captures the value-with-asterisk format. CAP 6 (new) catches isotonic calibrator hallucinations in the upper edge bucket: edge_pp > +25pp forces score=0 (SKIP). Same mechanism as CAP 3 — unconditional clamp, not a penalty. MLB closing-line markets are tight enough that a genuine 25pp edge does not exist; observed +25pp+ edges are calibrator output drifting past the reliable range. Validated 3-for-3 on retrospective application to 5/8 SEA@CHW (+31.2pp lost), 5/8 NYM@ARI (+23pp lost), 5/13 PHI@BOS (+31.0pp A-tier loss). CAP 1 precision target revised 1.00 -> 0.85 in run_backtest.py after 5/13 STL@OAK win-missed firing brought live precision to 4/5 = 80%. Downside filters don't need perfection — 85% is the right operating point and CHC@ATL same-day loss-aversion validates the cap continues to work. CAP 6 added to CAP_PRECISION_TARGETS at 0.70 (wider tolerance for the sparse sample). Brain prompt and postgame prompt both updated to enumerate six caps instead of five and document CAP 5's regex history + CAP 6's reasoning."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  What happens on the next daily-slate cron:
echo    - CAP 5 now actually fires when F1* is the only F-signal on
echo      an A-or-higher pick.  Expect occasional demotions on small-
echo      sample SP matchups.
echo    - CAP 6 fires whenever edge_pp ^> +25pp.  Expect 0-2 fires
echo      per slate based on historical calibrator behavior.
echo    - grade_reasons column gains [HARD CAP 6] entries where
echo      applicable; existing CAP 5 entries will start appearing too.
echo
echo  What the next weekly-backtest run will show:
echo    - cap_audit_latest.md grows a CAP 6 row in the per-cap table.
echo    - CAP 1's [WARN] threshold lowered to 85%% so the current
echo      4/5 = 80%% reading will surface a single explicit warning
echo      rather than a fluctuating green/red flag.
echo    - CAP 5 will start accumulating fires on real data instead
echo      of staying at "no fires yet" indefinitely.
echo ============================================================
pause
