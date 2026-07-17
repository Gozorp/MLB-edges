# Totals & Margin Recalibration — 2026-07-17

User-directed optimization of (1) O/U total-runs calibration and (2) run-line /
margin-of-victory projection, from historical prediction performance.

## Data

| Source | Games | Ground truth |
|---|---|---|
| `bt_totals_2023/2024/2025.csv` | 1,814 | in-file `actual_runs` |
| `picks_totals_2026-*.csv` joined to finals | 377 | `docs/data/_results_*.json` + `postgame/*.json` (`final_score` verified away-first, 259/259 agreement) |
| `picks_2026-*_diag.csv` (pick, `pick_prob`) joined to finals | 488 | same |

Doubleheaders: only the first game of a dup `(date, away, home)` key is joined
(the truth sources collapse DH matchups); 4 keys skipped.

## Part A — Totals (O/U)

**Finding: the raw model total carries almost no signal.** `pred_runs` vs
actual combined runs, by season: correlation 0.06 / −0.02 / 0.03 / 0.02
(2023/24/25/26); OLS slope ≈ 0.05. The market line alone is far better
(MAE 3.26 vs model 3.87 on the 2,025-game with-line subset).

**Walk-forward OOS (train prior years → test next; n=1,445):**

| Estimator | Bias | MAE |
|---|---|---|
| raw `pred_runs` | −0.17 (−0.54 in 2024) | 3.80 |
| const shift | −0.14 | 3.80 |
| linear(pred) | −0.28 | 3.47 |
| linear(line) | +0.06 | 3.24 |
| **blend pred+line** | **+0.07** | **3.23** |

Deployed calibrator (fit on all 2,025 with-line games):
`cal_total = 0.0525·pred + 0.9682·line + 0.162`; no-line fallback is the
shrunk linear in `pred` alone. **Interval narrowing:** OOS P10–P90 residual
width 12.1 → 10.1 runs (~17% tighter), and bands are now *honest* (empirical
OOS quantiles, roughly asymmetric −4.8/+5.3 around the calibrated total).
Most-probable exact total = round(cal) + modal OOS offset (P ≈ 0.10 — MLB
totals are intrinsically high-variance; sd(actual) ≈ 4.5 runs).

Residual sd is flat across predicted-total levels (4.2–4.35 above 7.5 runs),
so a single quantile set is used.

## Part B — Run line / margin of victory

**Finding: confidence does not buy margin.** On 488 graded 2026 games the
favored (picked) side won just 53.7%; `E[margin | logit(pick_prob)]` fits with
a ~zero-to-negative slope OOS (the p ≥ 0.65 buckets won only 53–63%). Median
favored margin = **+1**; the MAE-optimal constant projection is exactly +1
(MAE 3.65 vs 3.58 for the old overlay, 3.70 for the OLS fit).

Old overlay (`K_BASE=1.5`, spreads to 6.5): bias −0.07, MAE 3.58, **exact-margin
hit rate 7.9%**. "Always favored-by-1": **14.1%**.

**Deployed changes** (`tools/spread_projection.py`, still a display-only
overlay):
- Curve re-anchored: `spread = clip(0.85 + 0.45·logit(p), 0.5, 2.5)`,
  production-scale clamp tightened to [0.9, 1.1] → spreads live near the
  empirical median instead of overshooting to 4–6.
- Score split now uses the **market-blend calibrated total** (Part A) instead
  of raw `pred_runs_mc` (falls back MC → 8.6).
- New payload per game: `most_probable_margin` (favored by 1, with empirical
  probability), `margin_top5` (exact-differential distribution for the game's
  confidence bucket), `total_band` (calibrated total + P25/P75 + P10/P90 +
  most-probable exact total).

## Pipeline wiring

- `tools/fit_totals_margin_calibration.py` — refits everything, writes
  `data/state/totals_margin_calibration.json` (atomic). Rerun after any
  meaningful new sample (e.g. monthly).
- `tools/totals_overlay.py` — appends `pred_runs_cal`, `total_p10/25/75/90`,
  `most_probable_total`, `mpt_prob`, `cal_basis` to `picks_totals_<date>.csv`
  after the frozen totals model runs; wired into `tools/run_local_slate.py`
  (non-fatal, sandboxed).
- `tools/spread_projection.py` — reads the calibration JSON (falls back to
  in-file constants), emits the new fields above.

The frozen win model, totals model, parlay builder, and grading are untouched
— consistent with the overlay architecture. The pre-registered
`tools/totals_recal_backtest.py` gates (|bias| ≤ 0.25, MAE ≤ raw) are exceeded
by the blend on every OOS fold.

## Honest caveats

- The blend leans ~95% on the market line; the model total adds ~0.005 runs
  of MAE. The display now reflects the best available estimate, but the totals
  *edge* model itself (pick side vs line) is a separate question this pass
  does not touch.
- Margin sample is one part-season (488 games). The flat/inverted
  confidence→margin slope may partly reflect this season's model calibration
  drift; refit monthly via the fitter.
- 8 of 14 games on a typical slate lack a market line at bake time
  (`cal_basis="no_line"`); their bands use the wider no-line quantiles.
