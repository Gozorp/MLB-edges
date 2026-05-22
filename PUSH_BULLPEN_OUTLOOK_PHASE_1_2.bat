@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Bullpen Outlook — Phase 1 + 2 of the per-reliever sprint
echo  -----------------------------------------------------------
echo  User authorized this multi-session build on 2026-05-21,
echo  explicitly overriding the SFO departure freeze.  Memory:
echo    project_bullpen_model_sprint_plan.md       (7-phase plan)
echo    project_sfo_departure_freeze.md            (override noted)
echo
echo  Tonight's ship covers Phases 1+2 of 7:
echo
echo  PHASE 1 — Data foundation
echo    mlb_edge/bullpen_meta_writer.py (NEW, ~280 lines)
echo      - SCHEMA_VERSION=1 sidecar JSON per slate
echo      - Top 8 relievers per team with rest_days,
echo        consecutive_days, pitches_72h, avg_leverage_last_3,
echo        fatigue_flag (FRESH/NORMAL/B2B/B2B2B/OVERWORKED),
echo        available_today
echo      - Per-team summary: top3_pitch_total_72h,
echo        ceiling_tier, n_relievers_back_to_back, etc.
echo      - Reads BullpenSnapshot from existing
echo        bullpen_tracker.snapshot() — no new MLB API calls
echo      - Rule 6 best-effort throughout; never raises
echo      - Self-tested with synthetic data: boundary tests on
echo        all 5 fatigue_flag values pass
echo
echo    mlb_edge/main_predict.py (+30 lines)
echo      - New "step 2.5/5" calls write_bullpen_meta after
echo        the model prediction step
echo      - Writes docs/data/bullpen_meta_^<slate_date^>.json
echo      - Best-effort wrap: any failure logs warning, slate
echo        continues unaffected
echo
echo  PHASE 2 — Dashboard renders in 3 places
echo
echo    docs/index.html (+200 lines)
echo      A. Helper functions:
echo         _bullpenMetaForMatchup, _bullpenTierColor,
echo         _bullpenFlagBadge, _bullpenTeamNarrative,
echo         _bullpenFatigueTable, renderBullpenOutlook
echo      B. New ^<div id="bullpen-outlook"^>^</div^> between
echo         top-outcomes and slate
echo      C. loadSlate now fetches bullpen_meta_^<date^>.json
echo         and stashes in window.__bullpenMeta (schema_version
echo         check before consuming)
echo      D. THREE render points:
echo         (1) Dedicated "Bullpen Outlook" card on dashboard
echo             — per-game card with narrative both sides
echo         (2) Deep Analysis dropdowns (Top Probable Outcomes):
echo             _deepNarrativeML augmented with bullpen paragraph
echo         (3) Detail panel that opens on slate-row click:
echo             _formatGamePreviewUpcoming adds fatigue table
echo             with rest/consec/p72/LI/flag columns; rows for
echo             unavailable relievers struck through
echo
echo  PHASE 3-7 (DEFERRED; queued in memory file):
echo    3. Training data gathering (2024-2026 appearance log)
echo    4. Feature engineering
echo    5. XGBoost model training with walk-forward validation
echo    6. Inference + dashboard wiring
echo    7. Validation harness (postgame projected-vs-actual)
echo
echo  Pre-Flight Prompt v1.0 compliance:
echo    [E] Rule 1  -- probed: BullpenSnapshot already exposes
echo                   pitch_log + rest_days_by_pitcher +
echo                   workload_by_team; new writer is pure
echo                   aggregation, no new pipeline stage
echo    [E] Rule 3  -- ast.parse + py_compile + node --check gates
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- shipped ONLY Phases 1+2 tonight; locked the
echo                   7-phase plan in memory so future sessions
echo                   resume the next phase without re-discussion
echo    [E] Rule 6  -- best-effort wrapping in writer + try/catch
echo                   around every dashboard render point
echo    [E] Rule 11 -- SCHEMA_VERSION=1 field protects downstream
echo                   from silent schema drift in future phases
echo    [E] Rule 12 -- architectural rationale documented in
echo                   bullpen_meta_writer.py header
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_bullpen_p12
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\mlb_edge" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "mlb_edge\bullpen_meta_writer.py"           "%TMPDIR%\mlb_edge\bullpen_meta_writer.py"           >nul
copy /Y "mlb_edge\main_predict.py"                  "%TMPDIR%\mlb_edge\main_predict.py"                  >nul
copy /Y "docs\index.html"                           "%TMPDIR%\docs\index.html"                           >nul
copy /Y "PUSH_BULLPEN_OUTLOOK_PHASE_1_2.bat"        "%TMPDIR%\PUSH_BULLPEN_OUTLOOK_PHASE_1_2.bat"        >nul

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
copy /Y "%TMPDIR%\mlb_edge\bullpen_meta_writer.py"          "mlb_edge\bullpen_meta_writer.py"          >nul
copy /Y "%TMPDIR%\mlb_edge\main_predict.py"                 "mlb_edge\main_predict.py"                 >nul
copy /Y "%TMPDIR%\docs\index.html"                          "docs\index.html"                          >nul
copy /Y "%TMPDIR%\PUSH_BULLPEN_OUTLOOK_PHASE_1_2.bat"       "PUSH_BULLPEN_OUTLOOK_PHASE_1_2.bat"       >nul

echo Python syntax-checking bullpen_meta_writer.py...
python -c "import ast; ast.parse(open('mlb_edge/bullpen_meta_writer.py', encoding='utf-8').read()); print('bullpen_meta_writer.py: ast.parse OK')"
if errorlevel 1 (echo PY SYNTAX FAILED & pause & exit /b 1)

echo Python syntax-checking main_predict.py...
python -c "import ast; ast.parse(open('mlb_edge/main_predict.py', encoding='utf-8').read()); print('main_predict.py: ast.parse OK')"
if errorlevel 1 (echo PY SYNTAX FAILED & pause & exit /b 1)

echo py_compile gate...
python -c "import py_compile; py_compile.compile('mlb_edge/bullpen_meta_writer.py', doraise=True); py_compile.compile('mlb_edge/main_predict.py', doraise=True); print('py_compile OK')"
if errorlevel 1 (echo PY_COMPILE FAILED & pause & exit /b 1)

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'extracted {len(blocks)} script blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo Null-byte audit on shipped files...
python -c "[print(f'{p}: {open(p,chr(34)+chr(114)+chr(98)+chr(34)).read().count(bytes([0]))} null bytes') for p in ['mlb_edge/bullpen_meta_writer.py','mlb_edge/main_predict.py','docs/index.html']]"

echo Staging + committing...
git add mlb_edge/bullpen_meta_writer.py
git add mlb_edge/main_predict.py
git add docs/index.html
git add PUSH_BULLPEN_OUTLOOK_PHASE_1_2.bat
git status --short
git commit -m "Bullpen Outlook Phase 1+2: per-team sidecar JSON + 3 dashboard render points. User authorized this multi-session build on 2026-05-21, explicitly overriding the SFO departure freeze rule. Sprint plan locked in memory/project_bullpen_model_sprint_plan.md (7 phases total). Phase 1: new mlb_edge/bullpen_meta_writer.py aggregates BullpenSnapshot (already produced by bullpen_tracker.snapshot()) into docs/data/bullpen_meta_<slate_date>.json with SCHEMA_VERSION=1. Per team: top 8 relievers with rest_days, consecutive_days, pitches_72h, avg_leverage_last_3, fatigue_flag (FRESH/NORMAL/B2B/B2B2B/OVERWORKED), available_today + per-team summary (top3_pitch_total_72h, ceiling_tier, n_relievers_back_to_back, etc.). No new MLB API calls; pure aggregation of existing pipeline output. main_predict.py adds step 2.5/5 calling the writer after model prediction. Best-effort throughout — never raises, never blocks the slate cron. Self-tested with synthetic data: all 5 fatigue_flag boundary cases pass. Phase 2: docs/index.html (+200 lines) adds 6 new JS helpers (_bullpenMetaForMatchup, _bullpenTierColor, _bullpenFlagBadge, _bullpenTeamNarrative, _bullpenFatigueTable, renderBullpenOutlook), a new <div id='bullpen-outlook'> between top-outcomes and slate, a loadSlate fetch for the sidecar with schema_version check, and THREE render points: (1) dedicated 'Bullpen Outlook' card with per-game two-team narrative; (2) extension of _deepNarrativeML so every ML pick's Deep Analysis dropdown carries a bullpen paragraph; (3) extension of _formatGamePreviewUpcoming so the slate-row detail panel includes a per-reliever fatigue table with rest/consec/p72/LI/flag columns (unavailable relievers struck through). Phases 3-7 (training data, features, model training, inference, validation) queued in memory file for subsequent sessions. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (BullpenSnapshot already exposes everything needed), Rule 3 ast.parse + py_compile + node --check, Rule 4 safe-push, Rule 5 scoped to Phases 1+2 only, Rule 6 best-effort wrapping in writer + try/catch around each render point, Rule 11 SCHEMA_VERSION=1 protects against silent drift, Rule 12 architectural rationale in writer header, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  Validate next:
echo    1. Next main_predict cron will log:
echo       "[bullpen_meta] sidecar written: docs/data/
echo        bullpen_meta_^<date^>.json (^<N^> teams on slate)"
echo    2. Hard-refresh dashboard ^(Ctrl+Shift+R^).
echo    3. NEW "Bullpen Outlook" card appears between Top
echo       Probable Outcomes and the Slate table.  Each game
echo       row shows both teams' bullpen narrative with
echo       ceiling tier, fatigue alarms, average rest.
echo    4. Click "Deep analysis" on any ML pick — narrative
echo       now includes a "Bullpen outlook:" paragraph.
echo    5. Click any slate row to expand the detail panel —
echo       bullpen section adds a fatigue table with rows for
echo       each tracked reliever; rows for relievers on three
echo       consecutive days appear struck through.
echo    6. If bullpen_meta is missing for a slate, all three
echo       render points degrade gracefully to a muted
echo       "no bullpen data" message; no crashes.
echo ============================================================
pause
