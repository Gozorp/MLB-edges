@echo off
REM ============================================================================
REM job_nightly_chain.bat -- FULL autonomous nightly chain (proposed replacement
REM   for job_refit.bat; point task "mlb_edge_refit" here after review).
REM   Order:  build+bake slate -> daily variance -> calibrator refit -> publish
REM   = the local equivalent of the old cloud daily-slate.yml, then the refit.
REM ============================================================================
cd /d "%~dp0.."
if not exist logs mkdir logs
set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin"
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"

REM --- Locale-proof ISO slate date (UTC, to MATCH daily_variance + publish_local).
REM     Do NOT use %%DATE%% -- it is locale-formatted ("Thu 06/05/2026") and breaks
REM     predict.py's strict YYYY-MM-DD parser. publish_local's candidate is
REM     picks_<utcnow.date>_diag.csv, so the slate MUST be built for this same date.
for /f %%d in ('%PY% -c "import datetime;print(datetime.datetime.now(datetime.timezone.utc).date().isoformat())"') do set "SLATE=%%d"

echo ==== %DATE% %TIME% : nightly chain  slate=%SLATE% ====>> "logs\midnight.log"

REM 0) stale git-lock sweep -- a crashed publish can leave .git\*.lock which silently
REM    freezes every later publish; clear locks older than 5 min before anything runs.
%PY% tools\sweep_git_locks.py >> "logs\midnight.log" 2>&1

REM 1) build slate + BAKE to docs/data  (predict.py->diag + totals + copy + manifest)
%PY% tools\run_local_slate.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2) Daily Variance Report (reads the FRESH docs/data diag for this date)
%PY% tools\daily_variance_report.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.5) hot/cold streak indicator sidecar (reads fresh diag; fully sandboxed)
%PY% tools\streak_indicator.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.6) SP HR-prone recency sidecar (reads fresh diag SP names; fully sandboxed)
%PY% tools\sp_hr_recency.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.7) weather/wind runs-tilt sidecar (Open-Meteo at first pitch; fully sandboxed)
%PY% tools\weather_runs.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.8) OOS prediction ledger (append-only; logs pre-executive pick_prob, scores vs finals; sandboxed)
%PY% tools\oos_ledger.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.85) SKIP shadow-audit ledger (read-only; Tier-1 schema + GOLD+ shadow candidate; sandboxed)
%PY% tools\skip_shadow_audit.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.9) Team Power Tiers (season record + run differential; display-only; sandboxed)
%PY% tools\team_tiers.py >> "logs\midnight.log" 2>&1

REM 2.95) The Spread -- projected run differential (display-only post-processing overlay; sandboxed)
%PY% tools\spread_projection.py %SLATE% >> "logs\midnight.log" 2>&1

REM 2.96) Projected SP for pending-probable games (display-only; statsapi rotation+rest; sandboxed)
%PY% tools\sp_projection.py %SLATE% >> "logs\midnight.log" 2>&1

REM 3) calibrator refit (fits on rolling postgame outcomes; self-throttles)
%PY% tools\refit_post_calibrator.py >> "logs\midnight.log" 2>&1

REM 4) publish docs/data -> origin/main (temp-stashes candidates across reset --hard)
%PY% tools\publish_local.py nightly >> "logs\midnight.log" 2>&1

echo ==== %DATE% %TIME% : nightly chain done ====>> "logs\midnight.log"
