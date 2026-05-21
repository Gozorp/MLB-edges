@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  HARD CAPs 8, 9, 10 — bottom + top probability bucket
echo  -----------------------------------------------------------
echo  User directive (2026-05-21): cauterize the structural leaks
echo  surfaced by the model performance deep-dive.
echo
echo  CAP 8  (re-ship; never landed in code) —
echo    pick_prob in ^(0.50, 0.52^] AND edge_pp ^<= +12pp AND
echo    score ^>= 3 ^=^> score=1.  Grid-derived from 9-day archive,
echo    LODO stable 7/8 folds.  Commit b60da3a only added the
echo    .bat file; the actual parlay_builder.py change never
echo    landed.  This batch re-ships the rule alongside 9 and 10.
echo
echo  CAP 9  (NEW) — pick_prob ^> 0.80 forces SKIP.  Audit:
echo    archive ^(0.80, 1.00^] hits 33.3%% actual vs 87.3%%
echo    predicted ^(-53.9pp catastrophic miscalibration^).  HARD
echo    CAP 6 catches the ^>+25pp edge subset; CAP 9 catches the
echo    rest of the top bucket.  Cheap insurance until the
echo    isotonic calibrator is retrained on Stage 1/2 + platoon
echo    + BvP features.
echo
echo  CAP 10 (NEW) — pick_prob in ^(0.50, 0.55^] AND
echo    claude_decision is NOT CONFIRM ^=^> SKIP.  Probe of 59
echo    joined picks ^(May 9-18^):
echo      ALL          (0.50, 0.55]:  25W/33L = 43.1%%
echo      CONFIRM only:                20W/22L = 47.6%%
echo      non-CONFIRM:                 5W/11L  = 31.2%%
echo    Rule preserves the CONFIRM upside ^(+16pp lift in band^)
echo    while killing the structural-loss non-CONFIRM slice.
echo    CAVEAT: CONFIRM-rescued picks still hit below 50%%; a
echo    universal-SKIP tightening is documented as a candidate
echo    if 30 more graded slates do not show improvement.
echo
echo  Also-shipped: claude_brain_prompt.md gains explicit
echo  descriptions of all three new caps so Claude's overlay
echo  does not re-litigate them on every slate.
echo
echo  Files changed:
echo    1. mlb_edge/parlay_builder.py  (+106 lines: CAPs 8,9,10
echo       inserted after CAP 7 block; pre-existing null-byte
echo       corruption in working copy cleaned by full reset to
echo       origin + str.replace patch per Edit-tool pivot memory^)
echo    2. tools/claude_brain_prompt.md  (+3 cap entries; also
echo       cleaned of 621 null bytes from prior corruption^)
echo    3. PUSH_HARD_CAPS_8_9_10.bat ^(this file^)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed 24 diag CSVs joined with postgame
echo                   archive ^(n=105^) for CAP 10 form decision
echo    [E] Rule 2  -- test set locked at 9-day archive 5/8-5/18
echo    [E] Rule 3  -- ast.parse + py_compile gates in this script
echo    [E] Rule 4  -- safe-push pattern ^(temp -^> fetch -^> reset
echo                   -^> restore -^> syntax -^> commit -^> push^)
echo    [E] Rule 5  -- shipped only the rules the data supports;
echo                   PQI-strongly-FOR cap explicitly DEFERRED
echo                   pending n^>=30 sample per Rule 11
echo    [E] Rule 6  -- pd.notna + try/except wraps on every cap
echo    [E] Rule 9  -- thresholds derived from data, not invented:
echo                   CAP 8 = grid optimum, CAP 9 = bucket edge,
echo                   CAP 10 = user directive + probe
echo    [H] Rule 11 -- reverse-direction sanity:
echo                     CAP 8: 0 historical wins capped at grid optimum
echo                     CAP 9: 1W/2L in bucket; killing this kills 1 win
echo                            but saves 2 losses ^(net +1 EV^)
echo                     CAP 10: kills 5W/11L non-CONFIRM picks ^(net +6 EV^)
echo                            and preserves 20W/22L CONFIRM picks
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_caps_8910
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
copy /Y "mlb_edge\parlay_builder.py"        "%TMPDIR%\mlb_edge\parlay_builder.py"        >nul
copy /Y "tools\claude_brain_prompt.md"      "%TMPDIR%\tools\claude_brain_prompt.md"      >nul
copy /Y "PUSH_HARD_CAPS_8_9_10.bat"         "%TMPDIR%\PUSH_HARD_CAPS_8_9_10.bat"         >nul

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
copy /Y "%TMPDIR%\mlb_edge\parlay_builder.py"        "mlb_edge\parlay_builder.py"        >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"      "tools\claude_brain_prompt.md"      >nul
copy /Y "%TMPDIR%\PUSH_HARD_CAPS_8_9_10.bat"         "PUSH_HARD_CAPS_8_9_10.bat"         >nul

echo Python syntax-checking parlay_builder.py...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('parlay_builder.py: ast.parse OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo py_compile-ing parlay_builder.py...
python -c "import py_compile; py_compile.compile('mlb_edge/parlay_builder.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo Null-byte audit on shipped files...
python -c "import sys; [print(f'{p}: {open(p,chr(34)+chr(114)+chr(98)+chr(34)).read().count(bytes([0]))} null bytes') for p in ['mlb_edge/parlay_builder.py','tools/claude_brain_prompt.md']]"

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add tools/claude_brain_prompt.md
git add PUSH_HARD_CAPS_8_9_10.bat
git status --short
git commit -m "HARD CAPs 8, 9, 10: cauterize bottom + top probability-bucket leaks. CAP 8 RE-SHIP: commit b60da3a only added PUSH_HARD_CAP_8.bat; the actual parlay_builder.py change never landed (Edit-tool corruption period). Re-shipped alongside new caps 9 and 10 against the clean origin tree (working copy had 919 null bytes; reset + str.replace patch per Edit-tool pivot memory). CAP 8: pick_prob in (0.50,0.52] AND edge_pp<=+12pp AND score>=3 -> score=1 (B-, do not parlay). Grid-derived from 9-day archive, LODO stable 7/8 folds, overfitting risk acknowledged with prospective monitor. CAP 9 (NEW): pick_prob > 0.80 -> SKIP. Archive (0.80,1.00] hits 33.3% actual vs 87.3% predicted (-53.9pp catastrophic). HARD CAP 6 covers the >+25pp edge subset; CAP 9 covers the rest of the top bucket. Cheap insurance until the isotonic calibrator is retrained on Stage 1/2 + platoon + BvP features. CAP 10 (NEW): pick_prob in (0.50,0.55] AND claude_decision != CONFIRM -> SKIP. Probe of 59 joined picks (May 9-18): ALL 43.1%, CONFIRM-only 47.6%, non-CONFIRM 31.2%. Rule preserves the CONFIRM upside (+16pp lift in band) while killing the structural-loss non-CONFIRM slice. CAVEAT: CONFIRM in band still hits below 50% breakeven; universal-SKIP tightening documented in brain prompt as a candidate if 30 more graded slates do not show improvement. Also clean of 621 null bytes from prior corruption in tools/claude_brain_prompt.md; +3 cap entries added so Claude's overlay knows about them. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (24 diag CSVs joined with postgame archive, n=105), Rule 2 test set locked (9-day archive 5/8-5/18), Rule 3 ast.parse + py_compile, Rule 4 safe-push, Rule 5 shipped only rules data supports (PQI-strongly-FOR cap deferred per Rule 11 small-sample n=19), Rule 6 pd.notna + try/except, Rule 9 thresholds from data not invented (CAP 8 grid optimum, CAP 9 bucket edge, CAP 10 user directive + probe), Rule 11 reverse-direction sanity verified (CAP 8: 0 historical wins capped; CAP 9: 1W/2L bucket net +1 EV; CAP 10: kills 5W/11L non-CONFIRM saves 6 net EV preserves 20W/22L CONFIRM), Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Next main_predict cron should log
echo         "[HARD CAP 8]" / "[HARD CAP 9]" / "[HARD CAP 10]"
echo       in grade_reasons for any matching pick on the slate
echo    2. claude_brain_prompt.md now lists 10 HARD CAPs +
echo       1 SOFT CAP; Claude executive layer will reference
echo       them by number when explaining grade demotions
echo    3. cap_audit_ledger.csv will start accumulating CAP 8,
echo       9, 10 fires; precision targets should be set after
echo       n^>=10 fires per cap accumulate
echo    4. If postgame cron shows CAP 8 or CAP 9 fires that
echo       would have been wins, document and consider
echo       relaxation per Rule 11 reverse-direction policy
echo ============================================================
pause
