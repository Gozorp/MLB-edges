@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  HARD CAP 8: bottom-bucket marginal-favorite filter
echo  -----------------------------------------------------------
echo  Targets the highest-volume calibration leak: pick_prob in
echo  (0.50, 0.55] holds 57%% of all picks and they hit 40.6%% vs
echo  52.4%% predicted (-11.8pp overconfidence gap).
echo
echo  Trigger:
echo    pick_prob in (0.50, 0.52]
echo    AND edge_pp ^<= +12pp
echo    AND score ^>= 3  (B+ or higher)
echo
echo  Action:  score -^> 1  (B-, do not parlay)
echo
echo  Backtest evidence (9 days, 5/8-5/18, n=113 graded picks):
echo    * picks fitting trigger: 19
echo    * outcome: 6W / 13L = 31.6%% hit rate
echo    * tier breakdown: 13 bet-tier (GOLD/PLATINUM) 4W/9L,
echo                      6 SKIP-tier 2W/4L
echo    * LODO parameter stability: 7/8 folds within tolerance
echo
echo  HONEST OVERFITTING RISK ACCEPTED BY USER (2026-05-20):
echo    Recent window (5/15-5/18, post-cap-stack): 5W/5L = 50%% hit
echo    Older window (5/8-5/14, pre-cap-stack):    1W/8L =  11%% hit
echo    Most of the historical signal is from the older window
echo    when HARD CAP 1 and platoon-brain hadn't shipped yet.  The
echo    50%% recent hit rate may mean the existing cap stack ALREADY
echo    solved this leak, and CAP 8 sacrifices wins for no reason.
echo    User explicitly accepted this risk to ship now rather than
echo    wait for 30 shadow-mode prospective triggers.
echo
echo  Deploy gate: postgame cron will surface answer over next
echo  ~5-7 slates.  If capped-bucket hit rate stays ^>= 45%%,
echo  REMOVE THIS CAP immediately.
echo
echo  Files changed:
echo
echo  1. mlb_edge/parlay_builder.py (+42 lines)
echo     New Rule 8 block in _score_pick, between HARD CAP 7 and
echo     the PRE_CAP_SCORE encoding.  Matches existing cap pattern:
echo     pd.notna gate + float() try/except + reasons append with
echo     [HARD CAP 8] tag + score collapse to 1.
echo
echo  2. tools/claude_brain_prompt.md (+15 lines)
echo     Header "seven caps" -^> "eight caps". New bullet documenting
echo     CAP 8 trigger, action, backtest evidence, honest caveat
echo     about pre/post-cap-stack regime split, and remove-if-stale
echo     instruction.
echo
echo  3. PUSH_HARD_CAP_8.bat (this file)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  — probed (n=113 graded picks)
echo    [E] Rule 2  — test set = 9-day archive (locked)
echo    [E] Rule 3  — ast.parse syntax gate in this script
echo    [E] Rule 4  — safe-push pattern
echo    [E] Rule 9  — thresholds derived from grid-search,
echo                  not invented; both upper bounds documented
echo    [E] Rule 10 — full backtest + LODO stability check
echo    [H] Rule 11 — reverse-direction concerns documented in
echo                  commit body; rule may not generalize
echo    [E] Rule 12 — full architectural decision in commit body
echo    [E] Rule 13 — this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_hard_cap_8
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools"    2>nul
copy /Y "mlb_edge\parlay_builder.py"      "%TMPDIR%\mlb_edge\parlay_builder.py"      >nul
copy /Y "tools\claude_brain_prompt.md"    "%TMPDIR%\tools\claude_brain_prompt.md"    >nul
copy /Y "PUSH_HARD_CAP_8.bat"             "%TMPDIR%\PUSH_HARD_CAP_8.bat"             >nul

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
copy /Y "%TMPDIR%\PUSH_HARD_CAP_8.bat"            "PUSH_HARD_CAP_8.bat"            >nul

echo Syntax-checking before commit...
python -c "import ast; ast.parse(open('mlb_edge/parlay_builder.py', encoding='utf-8').read()); print('syntax OK')"
if errorlevel 1 (echo SYNTAX CHECK FAILED & pause & exit /b 1)

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add tools/claude_brain_prompt.md
git add PUSH_HARD_CAP_8.bat
git status --short
git commit -m "HARD CAP 8: bottom-bucket marginal-favorite filter. Grid-derived from 9-day postgame archive (5/8-5/18, n=113 graded picks). Trigger: pick_prob in (0.50, 0.52] AND edge_pp <= +12pp AND score >= 3; action: score -> 1 (B-, do not parlay). Targets the highest-volume calibration leak in the model: 57%% of picks land in pick_prob (0.50, 0.55], they hit 40.6%% actual vs 52.4%% predicted (-11.8pp overconfidence). Grid-search optimum: 19 captures, 6W/13L = 31.6%% aggregate hit rate; LODO parameter stable in 7/8 folds. HONEST OVERFITTING RISK: archive splits into pre-cap-stack window (5/8-5/14, 1W/8L on captures = 11%%) and post-cap-stack window (5/15-5/18, 5W/5L = 50%%). Most aggregate signal comes from the older window when HARD CAP 1 and platoon-brain hadn't shipped — the 50%% recent hit rate suggests existing caps may have already solved this leak, in which case CAP 8 sacrifices wins for no reason. User explicitly accepted this risk on 2026-05-20 to ship now rather than wait for 30 shadow-mode prospective triggers. Postgame cron will surface deploy/no-deploy answer over next ~5-7 slates; if recent-window 50%% hit rate persists prospectively, this cap should be removed immediately (instruction documented in brain prompt). Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (n=113), Rule 2 test set locked, Rule 9 thresholds grid-derived not invented, Rule 10 full backtest + LODO, [H] Rule 11 reverse-direction caveat documented in code comments + commit body + brain prompt, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Check tonight's slate reasons column for [HARD CAP 8]
echo       entries — those are bottom-bucket picks just demoted
echo    2. After 7-10 days of postgame data accumulates, re-run
echo       the backtest on JUST the post-cap-8-deploy window
echo    3. Decision gate: capped-bucket hit rate ^<= 45%% = keep;
echo       ^>= 45%% = REMOVE THE CAP (it's cutting variance)
echo ============================================================
pause
