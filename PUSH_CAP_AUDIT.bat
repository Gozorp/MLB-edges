@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Shipping shadow-Kelly cap audit
echo  -----------------------------------------------------------
echo  Files changed:
echo  1. tools/run_backtest.py
echo     +CAP_PRECISION_TARGETS pre-committed per-cap thresholds
echo        CAP 1 (negative-edge GOLD):     1.00
echo        CAP 2 (F3 + non-elite opp SP):  0.60
echo        CAP 3 (PLATINUM artifact):      1.00
echo        CAP 4 (Stage 1/2 + downgrade):  0.75
echo        CAP 5 (F1* small-sample SP):    0.65
echo     +cap_audit() function: parallel actual + shadow Kelly
echo        Option A: path-dependent compounding bankrolls
echo        Option B: per-cap attribution at fixed $1000 nominal
echo     +automated [WARN] flags when any cap drops below target
echo     +--cap-audit CLI flag (writes cap_audit_latest.md +
echo        cap_audit_latest.json + cap_audit_ledger.csv)
echo
echo  2. .github/workflows/weekly-backtest.yml
echo     +second python step that runs --cap-audit after the
echo        historical backtest.  Sunday 05:00 UTC cron now
echo        generates cap audit alongside the PnL report.
echo
echo  Why this matters:
echo    The five hard caps shipped 2026-05-13 each have a precision
echo    target derived from their archive validation.  Cap 1 and
echo    Cap 3 were 100%% in-archive (3-for-3 and 2-for-2) so any
echo    drop is meaningful.  Cap 2/4/5 have wider tolerances since
echo    their archive samples were smaller.  Below-target precision
echo    means a cap is catching wins -^> recommend relaxation.
echo
echo  Output cadence:
echo    Weekly (Sun 05:00 UTC) the cap audit refreshes from all
echo    cap-era diag CSVs that have accumulated since the prior
echo    Sunday.  cap_audit_latest.md is overwritten in place; the
echo    timestamped variants are kept for history.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_cap_audit
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\tools" 2>nul
mkdir "%TMPDIR%\.github\workflows" 2>nul
copy /Y "tools\run_backtest.py"                    "%TMPDIR%\tools\run_backtest.py"                    >nul
copy /Y ".github\workflows\weekly-backtest.yml"    "%TMPDIR%\.github\workflows\weekly-backtest.yml"    >nul
copy /Y "PUSH_CAP_AUDIT.bat"                       "%TMPDIR%\PUSH_CAP_AUDIT.bat"                       >nul

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
copy /Y "%TMPDIR%\tools\run_backtest.py"                    "tools\run_backtest.py"                    >nul
copy /Y "%TMPDIR%\.github\workflows\weekly-backtest.yml"    ".github\workflows\weekly-backtest.yml"    >nul
copy /Y "%TMPDIR%\PUSH_CAP_AUDIT.bat"                       "PUSH_CAP_AUDIT.bat"                       >nul

echo Staging + committing...
git add tools/run_backtest.py
git add .github/workflows/weekly-backtest.yml
git add PUSH_CAP_AUDIT.bat
git status --short
git commit -m "Shadow-Kelly cap audit + weekly cron wiring. run_backtest.py grows a cap_audit() function that runs parallel actual + shadow Kelly simulations across all cap-era diag CSVs (files with a pre_cap_grade column). Produces both Option A (path-dependent compounding bankrolls, captures operational reality) and Option B (per-cap attribution at fixed $1000 nominal, isolates algorithmic efficacy) so over- vs under-restriction can be diagnosed separately from sequence-of-returns noise. Pre-commits per-cap precision targets in CAP_PRECISION_TARGETS (CAP 1/3 = 1.00 since archive was 100%%, CAP 2 = 0.60, CAP 4 = 0.75, CAP 5 = 0.65). When realized precision drops below target the audit emits a [WARN: RECOMMEND RELAXATION] flag in the markdown — automated honesty, no human rationalization. The decimal-odds recovery uses (ev_per_dollar + 1) / p_model so existing diag columns are sufficient. Writes cap_audit_latest.md + .json + cap_audit_ledger.csv to docs/data/backtest/. weekly-backtest.yml adds a second step that runs --cap-audit after the historical PnL backtest, so the Sunday 05:00 UTC cron produces both reports in one job."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS.
echo
echo  What happens on the next Sunday cron (05:00 UTC):
echo  - Historical PnL backtest runs as before, produces
echo    docs/data/backtest/^<ts^>_summary.md
echo  - --cap-audit step runs IMMEDIATELY AFTER, produces
echo    docs/data/backtest/cap_audit_latest.md + .json + .csv
echo  - Both committed in the same fresh-clone push step
echo
echo  What to monitor on the first cap-audit output:
echo  - Headline cap lift (Option A): is actual_bk ^> shadow_bk?
echo    If yes, caps are net-positive on compounded PnL.
echo  - Per-cap precision table (Option B): which caps are
echo    catching losses (precision = 1.0) vs catching wins
echo    (precision ^< target -^> [WARN] flag automatically)
echo  - If any cap shows [WARN], the recommended relaxation is
echo    a one-line edit in mlb_edge/parlay_builder.py: bump
echo    the threshold (e.g. F3 ^> 1000 -^> F3 ^> 1100) so the
echo    cap fires on fewer borderline cases.
echo
echo  Manual trigger (anytime):
echo    python tools\run_backtest.py --cap-audit
echo ============================================================
pause
