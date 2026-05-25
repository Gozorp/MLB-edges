@echo off
REM Three safeguards on top of apply_calibration_from_all_picks:
REM   (1) ceil = base * 1.5 (was just `base`)
REM   (2) stress mask: tier_weight *= 0.3 when stress_warnings or
REM       confidence_downgrade is set on the diag row
REM   (3) warm-up gate: read historical n_picks_used_for_learning from
REM       audit log; if < 30, audit-only mode (passes immediately given
REM       the 125+ historical observations from the backfill)
REM Plus audit entries gain max_weight_change_pct + weights_growing_past_prior.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

python _patch_selflearn_safeguards.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

REM Quick syntax + import sanity test
python -c "from mlb_edge import auto_weight_update as awu; assert awu.NEW_CEILING_MULT == 1.5; assert awu.STRESS_MASK_FACTOR == 0.3; assert awu.WARMUP_THRESHOLD == 30; print('safeguards live, warm-up count:', awu._total_learned_from_count())"
if errorlevel 1 ( echo smoke test failed & pause & exit /b 1 )

git add mlb_edge/auto_weight_update.py _patch_selflearn_safeguards.py PUSH_SELFLEARN_SAFEGUARDS.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(self-learn): asymmetric ceiling fix + stress mask + warm-up gate + observability" -m "Three safeguards on apply_calibration_from_all_picks:" -m "(1) Asymmetric ceiling fix: ceil = baseline_weights[feat] * 1.5 (was just baseline_weights[feat]). The old hard ceil at base turned the learning loop into a one-sided decay rule - weights could shrink to 25 percent of base but never grow past base, so a signal that was under-credited at init had no path to recover. New ceiling lets modest upward growth happen while staying conservative." -m "(2) Stress mask: tier_weight *= 0.3 when the diag row has a non-empty stress_warnings string OR confidence_downgrade is truthy. Prevents double-counting noise - when the model itself flags a prediction as shaky, its outcome contributes less to weight updates. Schema change: _picks_diag_to_calib_rows now keeps stress_warnings + confidence_downgrade columns." -m "(3) Warm-up gate: reads cumulative n_picks_used_for_learning across the entire audit log. If less than 30, switches to audit-only mode (deltas computed and recorded but not applied to weights_state). Backfilled log has 125+ observations so this auto-passes on day one. The gate is also a structural self-heal: blowing away data/state/ re-engages probation automatically, so be careful about git-cleaning data/state/." -m "Plus audit entries now include max_weight_change_pct (largest single-weight move this update) and weights_growing_past_prior (list of weights whose new value exceeded their baseline). Both make the new ceiling behavior immediately observable in the audit log."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
pause
