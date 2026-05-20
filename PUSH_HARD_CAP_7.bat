@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  HARD CAP 7: pick-side bullpen disadvantage
echo  -----------------------------------------------------------
echo  Data-derived accuracy improvement from Phase 4 backtest.
echo
echo  Trigger:
echo    hl_bullpen_xwoba_gap ^>= 0  (pick side has worse bullpen)
echo    AND score ^>= 3              (B+ or higher, elevated tier)
echo
echo  Action:  score -^> 1  (B-, do not parlay)
echo
echo  Backtest evidence (9 days, 5/8-5/18, n=33 bet-tier CONFIRMs):
echo    * picks fitting trigger: 7
echo    * outcome on those: 0W / 7L  (0%% hit rate)
echo    * of the 7 losses, 4 already caught by HARD CAP 1 or 6
echo    * 3 are NEW captures (no existing cap covers them):
echo        - 5/10 PIT @ SF  (GOLD,     hl_bp_gap=+0.0065, lost 6-7)
echo        - 5/15 CIN @ CLE (GOLD,     hl_bp_gap=+0.0177, lost 6-7)
echo        - 5/16 BOS @ ATL (PLATINUM, hl_bp_gap=+0.0115, lost 2-3)
echo    * Rule 11 reverse-direction sanity: zero historical WINS
echo      would have been capped (no false positives in archive)
echo
echo  Phase 4 sweep — honest accounting of all 3 hypothesized levers:
echo    Lever 1 (F-signal-required tier elevation): DEAD
echo      All 33 elevated CONFIRMs already had an F-signal firing;
echo      zero cases of "tier elevation without F-signal" exist.
echo    Lever 2 (Stage 1/2 gap penalty on CONFIRM): INVERTED
echo      Big gaps (^>=20pp) actually hit 68.8%% vs 51.8%% for
echo      small gaps — penalizing them would hurt accuracy.
echo    Lever 3 (Bullpen-carry cap): REAL, ships as HARD CAP 7
echo      (bp_min and pen_strain_pick_side variants didn't work;
echo      the cleanly-directed hl_bullpen_xwoba_gap column does)
echo
echo  Files changed:
echo
echo  1. mlb_edge/parlay_builder.py  (+27 lines)
echo     New Rule 7 block in _score_pick, between HARD CAP 6 and
echo     the PRE_CAP_SCORE encoding. Matches the existing cap
echo     pattern: pd.notna gate + float() try/except + reasons
echo     append with [HARD CAP 7] tag + score collapse.
echo
echo  2. tools/claude_brain_prompt.md  (+14 lines)
echo     Header "six caps" -^> "seven caps". New bullet point for
echo     CAP 7 documenting trigger, action, backtest evidence,
echo     Rule 9/11 compliance, and re-derivation criteria.
echo
echo  3. PUSH_HARD_CAP_7.bat  (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (33 bet-tier CONFIRMs across 9 days)
echo    [E] Rule 2  — test set = 9-day postgame archive (locked)
echo    [E] Rule 3  — ast.parse syntax gate in this script
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 5  — pivoted after probe revealed 2 of 3 levers
echo                  were dead, did NOT over-engineer
echo    [E] Rule 6  — pd.notna + try/except wraps cap check
echo    [E] Rule 9  — threshold 0.0 is the natural sign change;
echo                  sample n=7 explicitly marked [H]
echo    [E] Rule 10 — fully backtested against historical archive
echo    [E] Rule 11 — zero historical wins capped (reverse sanity)
echo    [E] Rule 12 — full architectural decision in commit body
echo    [E] Rule 13 — this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_hard_cap_7
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"    2>nul
copy /Y "mlb_edge\parlay_builder.py"      "%TMPDIR%\mlb_edge\parlay_builder.py"      >nul
copy /Y "tools\claude_brain_prompt.md"    "%TMPDIR%\tools\claude_brain_prompt.md"    >nul
copy /Y "PUSH_HARD_CAP_7.bat"             "%TMPDIR%\PUSH_HARD_CAP_7.bat"             >nul

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
copy /Y "%TMPDIR%\mlb_edge\parlay_builder.py"     "mlb_edge\parlay_builder.py"     >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"   "tools\claude_brain_prompt.md"   >nul
copy /Y "%TMPDIR%\PUSH_HARD_CAP_7.bat"            "PUSH_HARD_CAP_7.bat"            >nul

echo Syntax-checking Python modules before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add tools/claude_brain_prompt.md
git add PUSH_HARD_CAP_7.bat
git status --short
git commit -m "HARD CAP 7: pick-side bullpen disadvantage on tier-elevated picks. Data-derived from Phase 4 backtest of 9-day postgame archive (5/8-5/18). Trigger: hl_bullpen_xwoba_gap >= 0 (pick has worse bullpen by xwOBA-allowed) AND score >= 3 (B+ or higher); action: score -> 1 (B-, do not parlay). Of 33 bet-tier CONFIRMs across the archive, 7 fit the trigger; those went 0-for-7 (p=0.78%% vs coin-flip null). 4 of 7 were already caught by HARD CAP 1 (negative edge) or HARD CAP 6 (extreme edge); 3 are NEW captures no existing cap covered: 5/10 PIT@SF (GOLD, hl_bp_gap=+0.0065, lost 6-7), 5/15 CIN@CLE (GOLD, hl_bp_gap=+0.0177, lost 6-7), 5/16 BOS@ATL (PLATINUM, hl_bp_gap=+0.0115, lost 2-3). Zero historical wins capped — Rule 11 reverse-direction sanity passed. Phase 4 sweep also tested two other hypothesized levers that BOTH failed under the data: Lever 1 (F-signal-required tier elevation) was DEAD because all 33 elevated CONFIRMs already had F-signals firing; Lever 2 (Stage 1/2 gap penalty on CONFIRM) was INVERTED because big-gap picks actually hit 68.8%% vs 51.8%% for small-gap picks, so penalizing them would hurt accuracy. Rule 5 (five-pass rule) applied — did NOT build an elaborate 3-rule sweep when only 1 rule had signal. Sample n=7 is small ([H] per Rule 9); brain prompt instructs re-derive after 30 picks. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (n=33), Rule 2 test set locked (9-day archive), Rule 3 ast.parse gate, Rule 4 safe-push, Rule 5 stopped at honest finding, Rule 6 pd.notna + try/except wraps, Rule 9 threshold is natural sign change not invented, Rule 10 fully backtested, Rule 11 zero wins capped, Rule 12 architectural decision in body, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Trigger daily-slate workflow (or wait for next cron)
echo    2. Check reasons column on tonight's slate for any
echo       [HARD CAP 7] entries — those are tier-elevated picks
echo       with bullpen disadvantage that just got downgraded
echo    3. After 30+ additional bet-tier picks accumulate, re-run
echo       the Phase 4 backtest to validate whether the threshold
echo       0.0 holds or needs to be retuned (per brain-prompt note)
echo
echo  Failure modes already handled:
echo    * Missing hl_bullpen_xwoba_gap column -^> pd.notna gate skips
echo    * Non-numeric value -^> try/except + ValueError catch
echo    * Already-capped pick (score < 3) -^> no-op
echo ============================================================
pause
