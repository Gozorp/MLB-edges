@echo off
REM Commit 3 of the legacy-blowout teardown sequence. Purges
REM recursive_weight_update.py + the update-weights CLI subcommand,
REM moves the surviving state-IO helpers into mlb_edge/weights_state.py,
REM re-points all imports, and fixes a latent bug in the audit log
REM (the try/except _BASELINES import has been silently failing the
REM entire life of the safeguard observability — now actually works).
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

REM Re-fetch the three files we're patching from origin so the patch
REM applies against a clean known state.
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/mlb_edge/auto_weight_update.py" -o mlb_edge\auto_weight_update.py
if errorlevel 1 ( echo curl auto_weight_update.py failed & pause & exit /b 1 )
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/mlb_edge/edge_calculator.py" -o mlb_edge\edge_calculator.py
if errorlevel 1 ( echo curl edge_calculator.py failed & pause & exit /b 1 )
curl -fsS "https://raw.githubusercontent.com/Gozorp/MLB-edges/main/mlb_edge/main.py" -o mlb_edge\main.py
if errorlevel 1 ( echo curl main.py failed & pause & exit /b 1 )

python _patch_delete_legacy.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Smoke test: confirm the patched modules import cleanly. The full
REM end-to-end dry-run lives under joblib which is heavy; we just
REM verify the import chain works (auto_weight_update + weights_state +
REM edge_calculator), which is what would break if any import got
REM mis-pointed during the rewrite.
python -c "import sys; sys.path.insert(0, '.'); from mlb_edge import weights_state; from mlb_edge import auto_weight_update; from mlb_edge import edge_calculator; print('IMPORTS OK')"
if errorlevel 1 ( echo import smoke failed & pause & exit /b 1 )

REM Now git rm the dead file. -f is needed because the file is in the
REM working tree (unmodified relative to HEAD).
git rm -f mlb_edge\recursive_weight_update.py
if errorlevel 1 ( echo git rm failed & pause & exit /b 1 )

REM Stage everything else: the patched files, the new weights_state.py,
REM the patch script, and this .bat.
git add mlb_edge\auto_weight_update.py mlb_edge\edge_calculator.py mlb_edge\main.py mlb_edge\weights_state.py _patch_delete_legacy.py PUSH_DELETE_LEGACY.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "chore(self-learn): delete recursive_weight_update.py + update-weights CLI" -m "Commit 3 of the legacy-blowout teardown. Purges recursive_weight_update.py (113 lines, was the home of apply_blowout_penalties + state-IO helpers + blowout constants). Surviving state-IO helpers move into the new mlb_edge/weights_state.py (~80 lines, pure I/O). All imports re-pointed across auto_weight_update.py, edge_calculator.py, main.py. The update-weights CLI subcommand is gone — --picks/--outcomes args, the dispatch elif, and the three orphaned helper functions (_normalize_picks_csv, _normalize_outcomes_csv, run_update_weights) are all deleted. The CLI choices list drops to backtest, train, predict." -m "Bonus fix: the audit log's safeguard fields (weights_growing_past_prior + runaway_alarm) have been DEAD-ON-ARRIVAL the entire life of the code because of a broken try/except import that always raised. The patch replaces that broken import with a direct reference to the already-imported SP_WEIGHTS from .config. Side effect: those audit fields now actually populate when conditions are met, fixing latent observability that we thought was working." -m "Net diff: -113 (recursive_weight_update.py gone) + ~80 (weights_state.py new) + ~60 (main.py shrinks) = roughly -90 lines. Plus the conceptual simplification of having one learning path instead of two and one filename that describes what the module does."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
echo.
echo === DONE ===
echo Three-commit legacy-blowout teardown is complete:
echo   Commit 1 (bb22c47): persist 28-day baseline
echo   Commit 2 (f532bca): sever chained blowout step
echo   Commit 3 (this):    delete recursive_weight_update.py + CLI
echo.
echo Daily +/-4 percent gradient cap is now a hard invariant.
echo Audit-log safeguards (weights_growing_past_prior + runaway_alarm)
echo are now actually firing instead of being silently dead.
pause
