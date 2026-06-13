# Bullpen-Fatigue Model-Feature — JULY Pre-Registration (LOCKED 2026-06-13)

**Status:** LOCKED pre-registration (Rule 2). Written *before* the feature is wired
into `model.py` and *independent* of the feature-generation code, so the test
cannot be tuned to the feature. Execute only after the SFO freeze lifts (~7/14+).
Nothing here changes production today.

**Headline:** the within-appearance bullpen-fatigue feature is the July priority,
motivated by a measured, localized calibration failure (below). Promotion is gated
on a walk-forward lift bar; if it doesn't clear the bar, it does NOT ship.

---

## 1. Motivating evidence (OOS attribution, 2026-06-04 → 06-12, n=92 graded picks)

The frozen win model's overconfidence is **concentrated in fatigued-bullpen games**,
not uniform:

| pick-side pen | n | predicted | actual | calibration gap |
|---|---|---|---|---|
| all picks | 92 | 56.8% | 51.1% | +5.7pp |
| **fatigued (OVERWORKED/B2B2B)** | 19 | 56.3% | **36.8%** | **+19.5pp** |
| rested | 73 | 56.9% | 54.8% | +2.1pp |
| high p72h (≥ median 31) | 47 | 56.8% | 44.7% | +12.1pp |
| low p72h (< median) | 45 | 56.7% | 57.8% | −1.0pp |

Rested-pen games are essentially calibrated → the error is **structural (fatigue),
not a morning-projection logging artifact**. Two independent cuts (system fatigue
flags + raw p72h median) agree in direction with a large effect. CAVEAT held: the
fatigued bucket is **n=19 (< 30)** → suggestive, not confirmed; and a fatigued pen
may proxy for team-form (extra-inning games / losing skids), which n is too thin to
separate yet. Both are addressed by the gates below.

## 2. Candidate features (LOCKED list; pre-first-pitch data only)

From the offline `tools/bp_fatigue_features.py` per-pitcher durability profiles
(`bp_fatigue_profiles.json`) + the 7-day `bullpen_meta` sidecar, computed for BOTH
the pick-side and opponent-side bullpens, available strictly before first pitch:
`top3_mean_pitches_72h`, `consecutive_days_max`, `overworked_arm_present` (bool),
`avg_leverage_last_3`, and the durability `oob_rate` / `velo_late` / susceptibility
of the projected high-leverage arms. No in-game / post-first-pitch data (leakage).

## 3. Walk-forward validation parameters (LOCKED — harness is independent of feature code)

- **Dataset:** the OOS ledger + archived diags, freeze-window forward
  (2026-06-04 → execution date). Each row = one graded pick with outcome.
- **Split:** **walk-forward, expanding window, grouped by `game_date`** (an entire
  slate is train xor test — never split a day across the boundary, no same-day
  leakage). Burn-in ≥ 60 graded picks before the first scored block; step one slate
  at a time; score only out-of-sample blocks. Identical folds for baseline and
  candidate (same seed, same partition).
- **Two models compared on the identical folds:** `BASE` = current frozen feature
  set; `CAND` = `BASE` + the §2 features. **Team-form covariates (14-day win%,
  run-differential z, days-rest) are in BOTH models**, so the fatigue features must
  add lift *orthogonal to team form* — this is the confounder control, locked in.
- **Metrics & acceptance bar (ALL must hold, else NULL → keep frozen `BASE`):**
  1. **Primary — calibration/log-loss:** CAND OOS log-loss < BASE, with a
     **block-bootstrap (resample by game-day) 95% CI on the delta that excludes 0**
     (same bar as `CALIBRATION_SPEC.md`).
  2. **Discrimination:** ΔAUC ≥ **+0.01 and DeLong-significant**, OR ΔAUC
     non-inferior (≥ −0.005) *with* the significant log-loss win (mirrors the
     emp-Bayes SP-xERA prereg bar).
  3. **Targeted — the actual goal:** on the walk-forward OOS, the **fatigued-bucket
     calibration gap shrinks by ≥ 5pp absolute** vs BASE, **and the rested-bucket
     gap is non-inferior** (degrades by ≤ 2pp). The feature must fix the hole it was
     hired to fix without breaking the clean slates.
- **Sample precondition:** re-run the §1 attribution on the July-grown ledger; the
  **fatigued bucket must reach n ≥ 30** for the targeted test (3) to count as
  confirmatory rather than descriptive. If still < 30 at execution, the gate (3) is
  reported but the ship decision rests on (1)+(2) alone, and (3) re-tests later.
- **Final decision:** ship the retrained model only if (1)+(2)+(3) clear on a
  **final, untouched holdout block** not used in any tuning. Otherwise NULL: keep
  the frozen model, archive the result.

## 4. Hard guards (do not violate)

- **NEVER per-team penalty weights** (carried over from
  `project_blowout_overfavor_prereg` / `project_stake_gates_rejected`). This is a
  single calibrated continuous fatigue feature in the global model, not a team knob.
- The frozen production model stays prod until the bar is cleared; no shadow-to-prod
  flip without it.
- Interplay with `CALIBRATION_SPEC.md`: run the raw-vs-calibrator re-test on `BASE`
  first; the fatigue retrain is evaluated against whichever (raw vs calibrated)
  is production at execution time.
- Harness independence: the test script reads the §2 features as **opaque columns**
  and shares no thresholds or constants with the feature-generation code; it is
  written and frozen before the feature is wired into `model.py`.

## 5. Hygiene track (parallel, low expectation)

Ship the `lineup_confirmed` flag + game-time (post-lineup-lock) prediction logging
to the OOS ledger as **data infrastructure** (sharpens every future test). Per §1,
its calibration payoff is expected to be **minor** (rested-pen games already
calibrated → staleness is not the dominant error). It is not gated on a lift bar;
it is correctness hygiene only.

---

*Anchor result to reproduce first in July: the §1 split (fatigued +19.5pp vs rested
+2.1pp). Re-run the EXACT split on the grown ledger before trusting the retrain.*
