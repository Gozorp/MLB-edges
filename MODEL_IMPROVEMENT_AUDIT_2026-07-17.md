# Model Improvement Audit — 2026-07-17

Ways to improve the win model's prediction outcomes, ranked by evidence.
Sample: 488 graded picks over 44 slates (2026-05-09 → 07-16), joined to
verified finals; market subset n=392 with `fair_prob`.

## Headline diagnosis

**The picks are okay; the probabilities are the problem.** The picked side
wins 53.7% (better than a coin), but the model's *stated* probabilities are
so overconfident that its Brier score (0.2533) is WORSE than always saying
50/50 (0.2500) and worse than just predicting the base rate (0.2486). The
market's devigged fair prob is both better (0.2482) and almost perfectly
calibrated (predicted .531, actual .533). Every point below follows from
this.

## Calibration table (the core evidence)

| pick_prob bucket | n | predicted | actual | gap |
|---|---|---|---|---|
| 0.50–0.55 | 257 | .524 | .502 | +2.2pp |
| 0.55–0.60 | 94 | .569 | .553 | +1.6pp |
| 0.60–0.65 | 59 | .629 | .627 | **+0.2pp** |
| 0.65–0.70 | 59 | .666 | .559 | **+10.7pp** |
| 0.70–1.00 | 19 | .807 | .579 | **+22.8pp** |

Below 0.65 the model is honest. Above 0.65 it hallucinates certainty —
the exact "0.9447 that no baseball team ever is" failure the May
postmortem documented (root cause #3).

## Ranked improvements

### Tier 1 — calibration layer (biggest gain, zero model risk)

1. **Logit shrinkage on pick_prob.** A single-parameter recalibration
   (`p' = σ(k·logit(p))`) fit on the first 60% of the season picks k=0.30
   and improves the held-out last 40%: Brier 0.2555 → 0.2498, log-loss
   0.712 → 0.693. One parameter, refittable monthly, applied as an overlay
   column — the frozen model stays frozen.
2. **Market blend (same medicine that fixed totals).** 25%·model +
   75%·fair gives Brier **0.2466** — better than the model (0.2545) AND
   the market (0.2482) on the same subset. The blend weight says the
   model's real information content beyond the line is ~25%.
3. **Hard-gate contrarian picks.** When the model's pick has fair_prob <
   0.5 (betting against the market side), those picks win **45.1%**
   (n=153). That's not edge, it's a leak — postmortem root cause #2,
   still bleeding a year later. Minimum fix: negative-edge picks can
   never rise above SKIP; better: require edge ≥ +2pp for any staked tier.

### Tier 2 — conviction tiers are inverted

Observed win rates: GOLD .576 (n=184) > PLATINUM .543 (n=70) > DIAMOND
**.500** (n=14). The highest-conviction tiers perform WORST — conviction
is currently anti-signal, because tiers key off the same overconfident
top-bucket probabilities. Meanwhile two cheap signals in the diag carry
real, unused ranking power:

- Stage agreement (F5 & FULL same side): .556 vs .521 when they disagree.
- `f5_full_delta` below median: .561 vs .519 above.

Refit the tier thresholds on the CALIBRATED/blended probability, and make
stage-agreement + low-delta explicit tier requirements above GOLD.

### Tier 3 — model-level (needs retraining discipline)

4. **Thin-sample SP shrinkage at predict time.** Postmortem root cause #4
   (thin xERA = noise dressed as signal). Now that thin-SP games are
   scored rather than withheld (2026-07-17 change), shrink their
   probability toward 0.5 proportional to SP sample size before display.
5. **Probability ceiling.** Until the tail calibrates, cap displayed/graded
   probability at ~0.70. Only 19 picks ever printed above it and they hit
   .579.
6. **Close the feedback loop.** The self-learn audit trail was silently
   wiped for a month (publish-reset bug, fixed today) and weights are
   travel-frozen — the adaptive layer has been flying blind. With game_pk
   identity now in the diag, the grading join is exact; rerun
   `fit_totals_margin_calibration.py`-style refits on a schedule (weekly
   baseline job) instead of ad-hoc.

### Already fixed today (same audit family)

- Totals: market-blend calibration (OOS MAE 3.80→3.23, bias ~0).
- Margin overlay re-anchored to empirical +1.
- Doubleheader identity (game_pk end-to-end) — grading now exact.

## Suggested implementation order

1. `pick_prob_cal` overlay column (shrink k + market blend when fair
   exists) + contrarian gate — one tool, display/tier-level only.
2. Tier refit on calibrated prob + agreement/delta requirements.
3. Thin-SP shrink + 0.70 ceiling.
4. Weekly automated re-audit (this script → tools/, wired to the
   weekly-baseline job).

All four preserve the frozen-model architecture; nothing touches training.

## Addendum — safeguards BUILT (same day, user-directed)

All seven guardrails are implemented and verified:

| # | Safeguard | Where | Verified |
|---|---|---|---|
| 1 | Calibration-drift tripwire (30d rolling, alert >8pp) | `tools/model_guardrails.py` | fired immediately: +19.2pp worst gap, alert=True |
| 2 | Contrarian hard cap (fair<0.5 → SKIP) | `main_predict` tier stack | synthetic PLATINUM w/ fair .40 → SKIP |
| 3 | Probability ceiling 0.70 (raw kept in `pick_prob_raw`) | `main_predict` prob stack | 0.82 → 0.70, raw preserved |
| 4 | Blind-spot team cap (<46% acc, n≥25 → SKIP) | guardrails state + tier stack | active: TOR, MIA, PIT |
| 5 | Tier self-demotion vs GOLD benchmark (60d rolling) | guardrails state + tier stack | active: DIAMOND & PLATINUM → GOLD sizing |
| 6 | Pick-mutation publish tripwire (side flip or Δprob>0.10 w/o SP change) | `tools/publish_guard.py` | selftest: flip+jump trip; TBD-fill+SP-swap exempt |
| 7 | Thin-SP shrink toward 0.5 (by sample share, floor 0.25) | `main_predict` prob stack | 0.64 @ 60 pitches → 0.584 (w=0.60) |

State refreshes via `tools/model_guardrails.py`, wired into
`run_local_slate` BEFORE predict. Calibration is always computed on
`pick_prob_raw` so the guards can never mask the drift they detect.
Transition note: the FIRST guarded republish of an already-published slate
may trip guard #6 on ceiling-clamped rows (legit Δ>0.10); use
`PUBLISH_ALLOW_REGRESSION=1` once or let the guard keep the published copy.
Alerting note: `pipeline_alert.py` has no ALERT_WEBHOOK_URL configured —
guardrail alarms currently print to logs only.
