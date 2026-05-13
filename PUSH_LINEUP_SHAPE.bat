@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping lineup_shape + bullpen-strain features + prompts
echo  -----------------------------------------------------------
echo  Files in this push:
echo
echo  1. mlb_edge/lineup_shape.py  (NEW module)
echo     Pure functions: concentration_index, top_bottom_dropoff,
echo     bullpen_strain_score. No I/O dependencies, fully testable.
echo
echo  2. mlb_edge/lineup.py  (additive edit)
echo     Per-side lineup_concentration_idx + top_bot_dropoff
echo     computed from the per-batter xwOBA list BEFORE the
echo     PA-weighted aggregation collapses it.  build_lineup_features
echo     returns home_lineup_concentration_idx +
echo     away_lineup_concentration_idx alongside existing keys.
echo
echo  3. mlb_edge/build_pipeline.py  (additive edit)
echo     Surfaces per-side home_hl_bullpen_xwoba +
echo     away_hl_bullpen_xwoba so the strain interaction can be
echo     computed downstream.
echo
echo  4. mlb_edge/main_predict.py  (additive edit)
echo     New diag CSV columns:
echo       - home_lineup_concentration
echo       - away_lineup_concentration
echo       - hl_bullpen_xwoba_gap
echo       - pen_strain_pick_side
echo
echo  5. tools/claude_brain_prompt.md  (heuristic 6, 7, 8 added)
echo     Threshold guidance for lineup shape, pen-strain
echo     interaction, comparative bullpen quality.  This is what
echo     turns the new columns into actual Claude reasoning.
echo
echo  6. tools/claude_postgame_prompt.md  (signal vocabulary added)
echo     Canonical column names for postgame writer to cite when
echo     grading losses.  This closes the recursive feedback loop:
echo     postgame can attribute losses to the new signals by name,
echo     next morning's brain run search-matches them as patterns.
echo
echo  Three signals now available end-to-end:
echo    - lineup_concentration: top-3/bot-3 xwoba ratio
echo    - pen_strain_pick_side: opp_pen_xwoba * our_top_xwoba
echo    - hl_bullpen_xwoba_gap: comparative bullpen quality
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_lineup_shape
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
copy /Y "mlb_edge\lineup_shape.py"           "%TMPDIR%\mlb_edge\lineup_shape.py"           >nul
copy /Y "mlb_edge\lineup.py"                 "%TMPDIR%\mlb_edge\lineup.py"                 >nul
copy /Y "mlb_edge\build_pipeline.py"         "%TMPDIR%\mlb_edge\build_pipeline.py"         >nul
copy /Y "mlb_edge\main_predict.py"           "%TMPDIR%\mlb_edge\main_predict.py"           >nul
copy /Y "tools\claude_brain_prompt.md"       "%TMPDIR%\tools\claude_brain_prompt.md"       >nul
copy /Y "tools\claude_postgame_prompt.md"    "%TMPDIR%\tools\claude_postgame_prompt.md"    >nul
copy /Y "PUSH_LINEUP_SHAPE.bat"              "%TMPDIR%\PUSH_LINEUP_SHAPE.bat"              >nul

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
copy /Y "%TMPDIR%\mlb_edge\lineup_shape.py"           "mlb_edge\lineup_shape.py"           >nul
copy /Y "%TMPDIR%\mlb_edge\lineup.py"                 "mlb_edge\lineup.py"                 >nul
copy /Y "%TMPDIR%\mlb_edge\build_pipeline.py"         "mlb_edge\build_pipeline.py"         >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"           "mlb_edge\main_predict.py"           >nul
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"       "tools\claude_brain_prompt.md"       >nul
copy /Y "%TMPDIR%\tools\claude_postgame_prompt.md"    "tools\claude_postgame_prompt.md"    >nul
copy /Y "%TMPDIR%\PUSH_LINEUP_SHAPE.bat"              "PUSH_LINEUP_SHAPE.bat"              >nul

echo Staging + committing...
git add mlb_edge/lineup_shape.py
git add mlb_edge/lineup.py
git add mlb_edge/build_pipeline.py
git add mlb_edge/main_predict.py
git add tools/claude_brain_prompt.md
git add tools/claude_postgame_prompt.md
git add PUSH_LINEUP_SHAPE.bat
git status --short
git commit -m "Lineup shape + bullpen-strain features + coordinated Claude prompt updates. New module mlb_edge/lineup_shape.py exposes concentration_index (top-3 vs bot-3 xwOBA ratio capturing star-anchored vs balanced lineup shape) and bullpen_strain_score (opposing_hl_pen_xwoba x our_top_lineup_xwoba, the multiplicative interaction term standing in for the WHIP-to-OPS collision pattern since per-closer WHIP is not exposed in the diag pipeline). lineup.py computes the index from the per-batter xwOBA list before PA-weighted aggregation collapses it. build_pipeline surfaces per-side home_hl_bullpen_xwoba + away_hl_bullpen_xwoba so the strain interaction can be computed downstream. main_predict adds four new diag columns: home_lineup_concentration, away_lineup_concentration, hl_bullpen_xwoba_gap (comparative), pen_strain_pick_side. claude_brain_prompt.md adds three new decision heuristics (6, 7, 8) with explicit threshold guidance so Claude actually weights the new signals consistently. claude_postgame_prompt.md adds the canonical signal vocabulary so the postgame writer cites these signals by name when grading losses, closing the recursive feedback loop. Without the prompt updates this ships data but no reasoning; with them, Claude can self-learn from the new signals starting with the next 07:00 UTC brain run."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  What happens next automatically:
echo  - Tomorrow's daily-slate cron emits the new columns
echo  - Next claude-brain run reads the updated prompt + sees
echo    the new diag columns, applies heuristics 6/7/8
echo  - Tomorrow noon UTC, claude-postgame uses the new signal
echo    vocabulary when writing the postgame
echo  - Pattern accumulates in docs/data/postgame/*.json for
echo    the brain to self-learn over the next 2-4 weeks
echo
echo  Honest expectation: the new signals are HYPOTHESES, not
echo  validated rules.  Watch their pattern attribution in the
echo  postgame archive for ~30 days before locking them as
echo  hard tier modifiers.
echo ============================================================
pause
