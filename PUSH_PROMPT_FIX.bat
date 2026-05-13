@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Prompt fix: read CSV `pick` column directly (not f5_prob)
echo  -----------------------------------------------------------
echo  Bug diagnosed via 5/10 audit:
echo    Of 14 graded games on 5/10, 4 had Claude's model_pick
echo    pointing at the WRONG team — and in every case the
echo    wrong team is what f5_prob would predict (home if
echo    f5>=0.5, away otherwise).  Confirmed: Claude was
echo    inferring the pick from f5_prob instead of reading
echo    the explicit `pick` column.
echo
echo  Manifests ONLY on games where Stage 1 and Stage 2
echo  disagree about which side wins (f5_prob and full_prob
echo  straddle 0.5).  5/9 and 5/11 came out clean because
echo  no Stage 1/2 split games occurred those days.
echo
echo  Verdict (WIN/LOSS) was correct in every postgame
echo  entry — only the narrative description of "which team
echo  the model favored" was wrong.  Total loss count of
echo  29 remains accurate.
echo
echo  Files in this push:
echo    1. tools/claude_brain_prompt.md
echo       Adds a CRITICAL block under "Your Inputs" instructing
echo       Claude to read the `pick` column directly and never
echo       derive from f5_prob / full_prob / p_model / tier.
echo    2. tools/claude_postgame_prompt.md
echo       Same instruction in the postgame writer's "compute
echo       the model's pick" step, with worked example.
echo    3. model_losses.md
echo       Reconciliation note at the top of the previously
echo       extracted file flagging the 5/10 misread entries.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_prompt_fix
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
copy /Y "tools\claude_brain_prompt.md"       "%TMPDIR%\tools\claude_brain_prompt.md"       >nul
copy /Y "tools\claude_postgame_prompt.md"    "%TMPDIR%\tools\claude_postgame_prompt.md"    >nul
copy /Y "model_losses.md"                    "%TMPDIR%\model_losses.md"                    >nul
copy /Y "PUSH_PROMPT_FIX.bat"                "%TMPDIR%\PUSH_PROMPT_FIX.bat"                >nul

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
copy /Y "%TMPDIR%\tools\claude_brain_prompt.md"     "tools\claude_brain_prompt.md"     >nul
copy /Y "%TMPDIR%\tools\claude_postgame_prompt.md"  "tools\claude_postgame_prompt.md"  >nul
copy /Y "%TMPDIR%\model_losses.md"                  "model_losses.md"                  >nul
copy /Y "%TMPDIR%\PUSH_PROMPT_FIX.bat"              "PUSH_PROMPT_FIX.bat"              >nul

echo Staging + committing...
git add tools/claude_brain_prompt.md
git add tools/claude_postgame_prompt.md
git add model_losses.md
git add PUSH_PROMPT_FIX.bat
git status --short
git commit -m "Prompt fix: read CSV `pick` column directly, do not derive from f5_prob. Bug surfaced in 5/10 postgame audit: 4 of 14 graded games had model_pick pointing at the wrong team because Claude was inferring the pick from f5_prob (home-side Stage 1 prob) instead of reading the explicit pick column. Manifests only on games where Stage 1 and Stage 2 disagree about which side wins; 5/9 and 5/11 had no such games. WSH @ MIA, COL @ PHI, HOU @ CIN, NYM @ ARI on 5/10 are the affected entries. WIN/LOSS verdict in every postgame is correct (computed against actual CSV pick); only the narrative attribution of which team the model favored is wrong for those four. claude_brain_prompt.md adds an explicit CRITICAL block under the Inputs section. claude_postgame_prompt.md adds the same instruction in the model_pick computation step with a worked example. model_losses.md gets a reconciliation note at the top flagging the affected entries so the on-disk file is honest about which entries had misread picks. Also flagged but not fixed by this prompt change: CHC @ TEX on 5/10 had a different misread (model_pick=CHC when both probabilities favored TEX) that is not f5_prob-related — possibly tier-driven; warrants separate investigation if it recurs."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  What happens next:
echo  - Next claude-brain cron (07:00 / 18:00 / 22:30 UTC)
echo    reads the updated prompt, applies the CRITICAL block.
echo  - Future claude_picks JSONs will have model_pick matching
echo    the CSV pick column even on Stage 1/2 split games.
echo  - Future postgames will name the correct team in
echo    headline + hypothesis.
echo  - Existing claude_picks/<5-10>.json and postgame/<5-10>.json
echo    files remain as-is (no retroactive backfill — the
echo    verdict counts are correct, only narratives are off,
echo    and patching them would require regenerating with
echo    fresh Claude inference which costs Max quota).
echo ============================================================
pause
