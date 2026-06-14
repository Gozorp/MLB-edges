# CALIBRATION_SPEC — Addendum, 2026-06-14

**This is a dated, pre-registered amendment to `CALIBRATION_SPEC.md` (committed origin `81b9106`).**
It does **not** rewrite the locked spec; the original stands as-is. This file records *what is
changing in the future test protocol, when, and why* — before any July validation data is fit or
scored — so the audit trail stays clean.

## Provenance / honesty statement (read first)

- **No July validation outcomes have been used to motivate these changes.** As of this date the
  frozen OOS ledger holds ~53 of the 350 graded picks the spec requires; the experiment cannot and
  has not run.
- **Motivation is two things only:** (1) the documented *prior* known overconfidence (predicts
  ~57.9%, delivers ~54.2%, gap concentrated in the upper bands — already in the locked spec §0),
  and (2) frozen-production *telemetry* from the 6/5–6/14 cold stretch (pooled 52–71, 42%, Brier
  0.219). Telemetry, not validation results.
- **The frozen production model and tomorrow's pipeline are NOT touched.** This amends the test
  plan, nothing live. `ENABLE_STAGE2_CALIBRATION` stays `False`; nothing ships before the July
  re-test passes its locked bar.
- **All thresholds, splits, and decision rules below are fixed now, before any fit.** Method is
  chosen on the fitting slice and judged on a *separate* validation slice — never the same data to
  both discover and declare.

---

## Amendment 1 — reorder the calibration bake-off (shrinkage first, isotonic last)

The locked spec §5 leads with binned-isotonic (C1) and treats Platt (C3) as a "known loser." That
verdict came from the 2024–25 test, which fit on **noisy inner-CV OOF probabilities** where Platt
over-regressed — a data-quality artifact, not a statement about clean production probs. The current
suspected defect is specifically **upper-band overconfidence on clean frozen probs**, for which a
single-parameter shrink is the most principled first try and the hardest to overfit at n≈350.

**Revised method order (replaces the §5 ordering for the July run):**

1. **C1′ — Logit shrinkage / Platt-style (PRIMARY).** `calibrated_p = sigmoid(a + b·logit(raw_p))`.
   A fitted slope `b < 1` pulls 70–80% picks toward the center — directly targeting the observed
   mode. Two parameters; minimal overfit risk.
2. **C2′ — Beta calibration.** For asymmetric miscalibration where Platt is too rigid.
3. **C3′ — Binned-isotonic (`BinnedIsotonicCalibrator`, the prior primary).** Powerful but
   overfit-prone with small, uneven per-band samples; use only if the reliability curve shows clear
   non-linear distortion *and* there is enough data per band.
4. **C0 — RAW** remains the control / incumbent to beat. The §7 acceptance bar (ΔBrier with
   block-bootstrap CI > 0; ECE non-worse; log-loss non-inferior; n≥250) is **unchanged**.

Tie-break remains **simplest-wins** (fewer parameters preferred). Selection on the fitting slice;
judgment on the held-out validation slice.

---

## Amendment 2 — high-confidence margin-tail probe (new, descriptive)

A high-confidence pick losing is a *probability* question; losing **0–8** is a *margin* question.
A true 78% team loses 22% of the time and some of those are ugly by chance. The model is indicted
only if its high-confidence favorites suffer **worse loss-tails than comparable market favorites**.
This probe separates "baseball produces ugly losses" from "the model under-prices tail blowouts."

### Defining "comparable market favorite" (the critical detail)

Bin by **market-implied** probability, not the model's own. Otherwise a model "72%" the market
prices at 56% gets compared to a true-72% market favorite — apples to oranges.

```
p_model_side  = frozen model probability for the selected side
p_market_side = no-vig closing moneyline probability for the SAME side
```

Bin by `p_market_side`. Bands: 55–60 / 60–65 / 65–70 / 70–75 / 75%+.

### Probe design (pre-registered)

```
Universe:
  Frozen-model July validation picks where:
    - p_model_side >= 0.65
    - a closing moneyline exists and a no-vig market prob can be computed
    - game has a final score; not void/postponed/suspended

Comparator:
  All market favorites from the same validation period, bucketed by p_market_side.

Variables:
  margin       = selected_side_runs - opponent_runs
  loss_margin  = opponent_runs - selected_side_runs   (conditional on the pick losing)

Tail metrics (per band):
  loss rate; avg margin when losing; P(loss by 3+); P(loss by 5+); P(loss by 7+)

Primary question:
  Conditional on comparable market-implied probability, do model high-confidence
  picks suffer materially worse loss-tail outcomes than ordinary market favorites?

Optional stratification (only if sample allows — do not over-filter a thin sample):
  home/away; closing total band (<=7.5 / 8–8.5 / 9+)
```

### Split model–market disagreement into two groups

These are different failure modes; mixing them muddies the result.

- **Group A — model high-confidence AND market also favorite.** Answers: is the model worse than
  ordinary favorites at similar market prices?
- **Group B — model high-confidence but market neutral / disagrees.** Answers: are the model's
  strongest disagreements with the market especially blowout-prone?

### Decision rule (not binary — sample will be thin)

- **GREEN:** model high-confidence picks have similar-or-better loss-tail behavior than matched
  market favorites.
- **YELLOW:** worse tail behavior, but CIs wide or sample thin → inconclusive, re-probe later.
- **RED:** materially worse loss-tail across multiple thresholds (esp. P(loss 5+), P(loss 7+))
  after market-probability and total-runs stratification.

Only **RED** justifies escalating to the feature track (and even then, only via the gated retrain
process — never a live hotfix).

### Data-availability gate (honest constraint)

The ideal design needs **closing no-vig lines** and a **full market-favorite universe**. Current
reality: Kalshi is the primary moneyline source (near vig-free) but captured at **slate-build
time, not close**; the multi-book odds API is dead; closing/CLV capture is not yet built. So:

- **Tier-1 (runnable with existing data):** use the diag's Kalshi-implied `fair_prob` (build-time)
  as the market anchor, on the games the model evaluated. State plainly in the result that this is
  Kalshi, build-time (not closing), and limited to the model's slate — not a full market universe.
  The **Group A / Group B split is fully runnable now** (`pick_prob` vs `fair_prob` are both in the
  diag).
- **Tier-2 (needs data infra):** the full design with true closing no-vig lines and a market-wide
  favorite universe — gated on building closing-odds/CLV capture, which the odds-API outage
  currently blocks. Do not claim Tier-2 conclusions from Tier-1 data.

---

## Amendment 3 — feature engineering is a hypothesis queue, not a diagnosis

The blowout pattern creates a *plausible* feature-engineering hypothesis — especially around day-of
pitcher, bullpen, lineup, and player-level volatility inputs vs. team-history priors. **It is not a
confirmed diagnosis.** The current evidence supports **calibration overconfidence** more strongly
than any specific feature defect. The same blowouts are equally consistent with ordinary MLB tail
variance, over-weighted favorite priors, market-regime drift, bullpen/news latency, starter
volatility, or lineup/weather effects.

Therefore: the July probe must first **confirm a structural tail-risk problem (RED above)** before
any new feature is allowed into the production model. No feature work ships on the strength of a
narrative; it enters the queue as a hypothesis and clears the same gated-retrain bar as everything
else.

---

## What stays exactly as locked

- The §3 dataset, §4 weight-shift exclusion, §6 walk-forward partition, §7 acceptance bar, and §8
  block-bootstrap CI logic in `CALIBRATION_SPEC.md` are **unchanged**.
- Execution remains **post-freeze (≈7/14)**; n-gate still 350.
- NULL still means keep raw and ship nothing.

*Principle: you may improve the test plan before the test — but you do not pretend the original
locked spec always said so. This addendum is that honesty.*
