@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  OVERRIDE regression backtest harness  (24-day path)
echo  -----------------------------------------------------------
echo  User-locked decisions (2026-05-21):
echo
echo    Sample floor:        n ^>= 10 out-of-sample fires
echo    Keep threshold:      precision ^>= 85%%
echo    Gray zone:           strict bimodal ^(below 85%% =^> demote^)
echo    Reverse sanity:      ^>= 2 wins flipped =^> demote regardless
echo    Out-of-sample:       only fires AFTER 2026-05-21 count
echo    Auto-ship policy:    notify only; user reviews + runs .bat
echo
echo  Probe finding before this push (recorded for posterity):
echo    in-sample fires:     6  (the original 6-of-6 record)
echo    out-of-sample fires: 0  (verdict: INCONCLUSIVE today)
echo
echo  Why this approach instead of the 4-season Phase 2 harness:
echo    Per the SFO departure freeze memory, the user departs for
echo    Japan in late June.  A 2-3 week retrain-and-replay rebuild
echo    leaves zero buffer.  This harness produces an auto-decision
echo    when n=10 out-of-sample fires accumulate (~17 days at the
echo    historical 0.6 fires/day rate), well before departure, with
echo    zero infrastructure risk during travel.
echo
echo  What this push ships:
echo
echo  1. tools/backtest_override.py ^(NEW, 280 lines^)
echo     - collect_override_fires^(^) walks docs/data/postgame/*.json
echo     - compute_verdict^(^) applies the four locked thresholds
echo     - write_status^(^) emits override_status.json
echo     - write_ledger^(^) emits override_ledger.csv
echo     - write_push_bat^(^) emits PUSH_OVERRIDE_DEMOTE.bat ONLY
echo       when verdict='demote' ^(notify-only, no auto-push^)
echo     - Rule 6 best-effort wrapping; malformed postgame degrades
echo       to inconclusive rather than crashing the cron
echo
echo  2. .github/workflows/claude-postgame.yml ^(+30 lines^)
echo     - new step "Run OVERRIDE regression backtest" runs after
echo       every postgame JSON commit, against the fresh clone
echo     - commits the updated status JSON + ledger CSV daily
echo     - surfaces a workflow ::warning if verdict transitions to
echo       demote, so GitHub Actions UI lights up
echo
echo  3. docs/data/backtest/override_status.json ^(initial, n=0 OOS^)
echo  4. docs/data/backtest/override_ledger.csv ^(initial, 6 rows in-sample^)
echo  5. PUSH_OVERRIDE_BACKTEST.bat ^(this file^)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: 10 joinable postgame days, 6 OVERRIDE fires
echo    [E] Rule 2  -- thresholds locked BEFORE harness coded
echo                   ^(memory/project_override_backtest_thresholds.md^)
echo    [E] Rule 3  -- ast.parse + py_compile + yaml.safe_load gates below
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- did NOT build the Phase 2 4-season harness;
echo                   shipped the smallest harness that produces
echo                   the answer the user needs before departure
echo    [E] Rule 6  -- _safe_load_json + try/except per file;
echo                   missing postgame degrades to inconclusive
echo    [E] Rule 11 -- reverse-direction sanity baked into the
echo                   verdict logic (wins-flipped check)
echo    [E] Rule 12 -- architectural rationale in the file header
echo                   ^(why out-of-sample, why notify-only, etc.^)
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_override_bt
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
mkdir "%TMPDIR%\.github\workflows" 2>nul
mkdir "%TMPDIR%\docs\data\backtest" 2>nul
copy /Y "tools\backtest_override.py"                "%TMPDIR%\tools\backtest_override.py"                >nul
copy /Y ".github\workflows\claude-postgame.yml"     "%TMPDIR%\.github\workflows\claude-postgame.yml"     >nul
copy /Y "docs\data\backtest\override_status.json"   "%TMPDIR%\docs\data\backtest\override_status.json"   >nul
copy /Y "docs\data\backtest\override_ledger.csv"    "%TMPDIR%\docs\data\backtest\override_ledger.csv"    >nul
copy /Y "PUSH_OVERRIDE_BACKTEST.bat"                "%TMPDIR%\PUSH_OVERRIDE_BACKTEST.bat"                >nul

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
copy /Y "%TMPDIR%\tools\backtest_override.py"                "tools\backtest_override.py"                >nul
copy /Y "%TMPDIR%\.github\workflows\claude-postgame.yml"     ".github\workflows\claude-postgame.yml"     >nul
copy /Y "%TMPDIR%\docs\data\backtest\override_status.json"   "docs\data\backtest\override_status.json"   >nul
copy /Y "%TMPDIR%\docs\data\backtest\override_ledger.csv"    "docs\data\backtest\override_ledger.csv"    >nul
copy /Y "%TMPDIR%\PUSH_OVERRIDE_BACKTEST.bat"                "PUSH_OVERRIDE_BACKTEST.bat"                >nul

echo Python syntax-checking harness...
python -c "import ast; ast.parse(open('tools/backtest_override.py', encoding='utf-8').read()); print('ast.parse OK')"
if errorlevel 1 (echo PY SYNTAX FAILED & pause & exit /b 1)

echo py_compile-ing harness...
python -c "import py_compile; py_compile.compile('tools/backtest_override.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo YAML-checking workflow...
python -c "import yaml; y=yaml.safe_load(open('.github/workflows/claude-postgame.yml')); print(f'YAML OK, {len(y[\"jobs\"][\"postgame\"][\"steps\"])} steps')"
if errorlevel 1 (echo YAML LINT FAILED & pause & exit /b 1)

echo Dry-run of harness on local data...
python tools/backtest_override.py
if errorlevel 1 (echo HARNESS DRY-RUN FAILED & pause & exit /b 1)

echo Staging + committing...
git add tools/backtest_override.py
git add .github/workflows/claude-postgame.yml
git add docs/data/backtest/override_status.json
git add docs/data/backtest/override_ledger.csv
git add PUSH_OVERRIDE_BACKTEST.bat
git status --short
git commit -m "OVERRIDE regression backtest harness: out-of-sample sweep + notify-only auto-decision. New tools/backtest_override.py walks docs/data/postgame/*.json, extracts every matchup where claude_decision contains OVERRIDE, splits in-sample (date<=2026-05-21, the freeze date when thresholds were locked) from out-of-sample (date>2026-05-21), and applies the four locked thresholds: sample floor n>=10 OOS fires, keep precision>=85%%, strict bimodal (no gray-zone watch state), reverse-direction demote if >=2 wins flipped regardless of precision. Emits docs/data/backtest/override_status.json + override_ledger.csv every run; if verdict transitions to demote, emits PUSH_OVERRIDE_DEMOTE.bat for user review (auto-ship policy is NOTIFY ONLY per locked memory). claude-postgame.yml gains a new step that runs the harness after every postgame commit against the fresh clone and commits the updated status + ledger; surfaces ::warning when verdict reaches demote so GitHub Actions UI flags it. As of this commit: n_in_sample=6, n_out_of_sample=0, verdict=INCONCLUSIVE. Auto-decision will fire when ~10 OOS fires accumulate (~17 days at the historical 0.6 fires/day rate). Why this and not the 4-season Phase 2 retrain-and-replay harness: per project_sfo_departure_freeze.md, user departs late June; 2-3 week rebuild leaves zero buffer; this harness produces the same decision-grade answer with zero infrastructure risk during travel. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (n=6 in-sample available), Rule 2 thresholds locked BEFORE coding (memory file), Rule 3 ast.parse + py_compile + yaml.safe_load, Rule 4 safe-push, Rule 5 single-purpose harness; does NOT auto-deploy, does NOT touch parlay_builder.py, does NOT build the Phase 2 4-season backtest, Rule 6 _safe_load_json + try/except per postgame file, Rule 11 reverse-direction sanity baked into verdict computation, Rule 12 architectural rationale in the file header, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Next postgame cron run ^(12:00 UTC^) will:
echo       - write today's postgame JSON as usual
echo       - then run the OVERRIDE harness on the fresh clone
echo       - commit override_status.json + override_ledger.csv
echo    2. Check the Actions tab for a ::warning if verdict
echo       transitions to demote
echo    3. As OOS OVERRIDE fires accumulate, n_out_of_sample
echo       climbs toward the floor of 10
echo    4. When verdict='demote' lands, PUSH_OVERRIDE_DEMOTE.bat
echo       appears in the repo root; review and double-click it
echo       to ship the demotion patch ^(the patch logic itself
echo       is intentionally a placeholder in the template -- when
echo       the first demote fires, finalize the patch then^)
echo    5. Current status: INCONCLUSIVE  (6 in-sample fires, 0 OOS)
echo ============================================================
pause
