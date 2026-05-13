@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping 5 hard caps + monitoring + prompt updates
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. mlb_edge/parlay_builder.py
echo     +5 hard-cap rule blocks at the END of _score_pick:
echo        [HARD CAP 1] negative-edge GOLD prevention
echo        [HARD CAP 2] F3>1000 + home-fav>65%% requires elite opp SP
echo        [HARD CAP 3] PLATINUM calibration artifact (p_model>0.85
echo                     + delta>0.20 -^> SKIP)
echo        [HARD CAP 4] Stage 1/2 delta + confidence_downgrade
echo        [HARD CAP 5] F1* small-sample SP quarantine
echo     +pre_cap_score / pre_cap_grade columns for monitoring
echo
echo  2. tools/claude_brain_prompt.md
echo     New section above Decision Heuristics listing the five
echo     hard caps so Claude doesn't waste tokens re-deriving.
echo
echo  3. tools/claude_postgame_prompt.md
echo     Adds the five canonical cap names to the
echo     signals_to_recheck vocabulary so the postgame writer
echo     can attribute saves/over-restrictions to specific caps.
echo
echo  Validation (postgame archive):
echo    CAP 1: 3-for-3 negative-edge GOLD losses caught
echo    CAP 3: 2-for-2 PLATINUM calibration artifact losses
echo    CAP 4: 3 supporting cases (MIN@CLE x2, PIT@SF)
echo    CAP 5: 3 rookie/early-career losses (Misiorowski,
echo           Schlittler, Leiter)
echo    CAP 2: 1 failure + 1 correct Claude override
echo
echo  Monitoring: pre_cap_score in the diag CSV lets the
echo  weekly backtest compare "what the score WOULD have been"
echo  vs "what it ended up at", attributable per cap.  If a
echo  cap fires often AND those demoted picks win > 55%%, we
echo  know the cap is over-restricting and worth relaxing.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_5caps
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
copy /Y "mlb_edge\parlay_builder.py"              "%TMPDIR%\mlb_edge\parlay_builder.py"              >nul
copy /Y "tools\claude_brain_prompt.md"            "%TMPDIR%\tools\claude_brain_prompt.md"            >nul
copy /Y "tools\claude_postgame_prompt.md"         "%TMPDIR%\tools\claude_postgame_prompt.md"         >nul
copy /Y "PUSH_5_HARD_CAPS.bat"                    "%TMPDIR%\PUSH_5_HARD_CAPS.bat"                    >nul

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
copy /Y "%TMPDIR%\mlb_edge\parlay_builder.py"              "mlb_edge\parlay_builder.py"              >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"            "tools\claude_brain_prompt.md"            >nul
copy /Y "%TMPDIR%\tools\claude_postgame_prompt.md"         "tools\claude_postgame_prompt.md"         >nul
copy /Y "%TMPDIR%\PUSH_5_HARD_CAPS.bat"                    "PUSH_5_HARD_CAPS.bat"                    >nul

echo Staging + committing...
git add mlb_edge/parlay_builder.py
git add tools/claude_brain_prompt.md
git add tools/claude_postgame_prompt.md
git add PUSH_5_HARD_CAPS.bat
git status --short
git commit -m "Five hard-cap rules + monitoring + coordinated prompt updates. parlay_builder _score_pick now applies five post-scoring hard caps validated against docs/data/postgame/*.json. CAP 1 prevents GOLD on any negative edge (3-for-3 on CHC@TEX 5/9, NYY@MIL 5/9, NYY@BAL 5/11). CAP 2 prevents F3>1000 home-favorite>65 picks from anchoring parlays when the opposing SP is not season-xERA<4.0 elite. CAP 3 forces SKIP when p_model>0.85 combines with f5_full_delta>0.20 (PLATINUM calibration artifact, 2-for-2 ATL@LAD 5/10, SF@LAD 5/11). CAP 4 caps at B- when Stage 1/2 delta>=0.12 stacks with confidence_downgrade=True. CAP 5 quarantines GOLD picks whose only F-signal is asterisked F1_xera_gap (small-sample SP). All five fire as additive post-score caps. Pre-cap score is snapshotted and surfaced as pre_cap_score + pre_cap_grade columns in the diag CSV so the weekly backtest can monitor over-restriction (caps that fire on games that would have been wins are the failure mode). claude_brain_prompt.md adds a new section listing the five caps so Claude doesn't waste tokens re-deriving the math. claude_postgame_prompt.md adds the canonical cap names to the signals_to_recheck vocabulary for cross-day pattern tracking."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  What happens on the next daily-slate cron (06:00 UTC):
echo  - parlay_builder applies the five caps before grading
echo  - Expect a noticeable drop in GOLD/PLATINUM counts;
echo    that's the intended outcome
echo  - Diag CSV gains pre_cap_score + pre_cap_grade columns
echo
echo  What to monitor over the next 7 days:
echo  - GOLD volume per slate (likely drops ~40-60%%)
echo  - Hit rate of SURVIVING GOLDs (should rise to 55%%+)
echo  - Cap precision: for each demoted pick, did it lose?
echo  - The weekly-backtest cron next Sunday computes both
echo    the realized PnL and the shadow PnL (what would have
echo    been bet without the caps).  Compare the two to
echo    quantify the lift.
echo ============================================================
pause
